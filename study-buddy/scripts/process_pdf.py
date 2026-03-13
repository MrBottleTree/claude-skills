#!/usr/bin/env python3
"""
PDF Extractor — adaptive chunking with Gemini CLI + Claude CLI fallback.

Splits each PDF into chunks of at most MAX_PAGES_PER_CHUNK pages (default 20).
Large PDFs automatically get more chunks to avoid API timeouts.
Timeout scales with chunk size. On timeout, the chunk is automatically split
in half and retried (up to MAX_SPLIT_DEPTH times).
Automatic model fallback: Gemini models (CLI) -> Claude models (Claude Code CLI).
Never writes error text to output files.

Output structure (per-slide directory):
    slides_extracted/
        index.md           ← metadata + slide listing
        slide_0001.md      ← content for slide 1
        slide_0002.md      ← content for slide 2
        ...

Requirements:
    pip install pymupdf Pillow
    gemini CLI:  npm install -g @google/gemini-cli  (must be authenticated)
    claude CLI:  Claude Code must be installed and authenticated

Usage:
    python process_pdf.py slides.pdf
    python process_pdf.py slides.pdf --resume
    python process_pdf.py slides.pdf --output ./my_output_dir
    python process_pdf.py slides.pdf --end-page 41
    python process_pdf.py --clear-cache              # delete ALL cached extractions in cwd
    python process_pdf.py slides.pdf --clear-cache   # delete extraction for this PDF only
"""

import argparse
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ── Configuration ─────────────────────────────────────────────────────────────

GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

CLAUDE_MODELS = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]

MIN_CHUNKS = 4           # minimum number of chunks even for small PDFs
MAX_PAGES_PER_CHUNK = 20 # hard cap: never send more than this many pages in one call
DPI = 150
MAX_RETRIES = 3
BACKOFF_BASE = 20.0      # seconds; doubles each retry
TIMEOUT_PER_PAGE = 30    # seconds per page for adaptive timeout
MIN_TIMEOUT = 180        # floor timeout (seconds)
MAX_TIMEOUT = 900        # ceiling timeout (seconds) — 15 minutes
MAX_SPLIT_DEPTH = 3      # max halvings when a chunk times out


# ── CLI helpers ───────────────────────────────────────────────────────────────

def _wrap(args: list[str]) -> list[str]:
    """Prefix with cmd /c on Windows so .cmd shims resolve correctly."""
    if sys.platform == "win32":
        return ["cmd", "/c"] + args
    return args


def _is_rate_limit(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in (
        "429", "rate limit", "quota", "resource_exhausted",
        "resource exhausted", "too many requests",
    ))


def adaptive_timeout(num_pages: int) -> int:
    """Compute a timeout (seconds) scaled to the number of pages in the chunk."""
    return max(MIN_TIMEOUT, min(MAX_TIMEOUT, num_pages * TIMEOUT_PER_PAGE))


def run_gemini_cli(model: str, prompt: str, timeout: int = MIN_TIMEOUT) -> str:
    # Use positional argument (one-shot mode) instead of -p to avoid agentic mode
    # where Gemini responds "I'm ready for your command" instead of extracting content.
    cmd = _wrap(["gemini", "--model", model, "-o", "text", prompt])
    result = subprocess.run(
        cmd, capture_output=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(err[:400] or f"gemini exited {result.returncode}")
    text = result.stdout.strip()
    if not text:
        raise RuntimeError(f"Empty response from gemini ({model})")
    return text


def run_claude_cli(model: str, prompt: str, timeout: int = MIN_TIMEOUT) -> str:
    cmd = _wrap([
        "claude", "--model", model, "-p", prompt,
        "--dangerously-skip-permissions",
    ])
    result = subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=timeout, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(err[:400] or f"claude exited {result.returncode}")
    text = result.stdout.strip()
    if not text:
        raise RuntimeError(f"Empty response from claude ({model})")
    return text


# ── Prompts ───────────────────────────────────────────────────────────────────

def _extraction_instructions(page_nums: list[int]) -> str:
    numbered = ", ".join(str(p) for p in page_nums)
    return (
        f"For each slide output a section with '## Slide N' (N = slide number) "
        f"followed by ALL text verbatim, diagram descriptions, and formulas. "
        f"Output sections for slides: {numbered}. Do not skip or merge any slide."
    )


def build_gemini_prompt(page_nums: list[int], img_paths: list[Path]) -> str:
    # @file references tell Gemini CLI to attach these images to the request
    # Must be space-separated; use one-shot positional mode (not -p) to avoid agentic behavior
    file_refs = " ".join(f"@{str(p).replace(chr(92), '/')}" for p in img_paths)
    return (
        f"These are {len(page_nums)} lecture slides "
        f"(slides {', '.join(str(p) for p in page_nums)}): {file_refs} "
        + _extraction_instructions(page_nums)
    )


def build_claude_prompt(page_nums: list[int], img_paths: list[Path]) -> str:
    # Claude Code CLI will use its Read tool to load each image file
    file_list = "\n".join(
        f"- {str(p).replace(chr(92), '/')}  (this is Slide {pn})"
        for pn, p in zip(page_nums, img_paths)
    )
    return (
        f"Read the following image files in order — they are academic lecture slides:\n"
        f"{file_list}\n\n"
        + _extraction_instructions(page_nums)
    )


# ── Image rendering ───────────────────────────────────────────────────────────

def render_chunk_to_files(
    pdf_path: Path,
    page_indices: list[int],
    tmp_dir: Path,
    dpi: int = DPI,
) -> list[Path]:
    """Render 0-based page indices to PNG files in tmp_dir. Returns file paths."""
    import fitz

    doc = fitz.open(str(pdf_path))
    mat = fitz.Matrix(dpi / 72, dpi / 72)
    paths = []
    for idx in page_indices:
        pix = doc[idx].get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        out = tmp_dir / f"slide_{idx + 1:04d}.png"
        pix.save(str(out))
        paths.append(out)
    doc.close()
    return paths


# ── Fallback extraction ───────────────────────────────────────────────────────

def _try_all_models(label: str, page_nums: list[int], img_paths: list[Path]) -> str:
    """
    Try every Gemini model then every Claude model via CLI with adaptive timeout.
    Returns extracted text or raises RuntimeError if all models fail.
    Does NOT handle chunk splitting — that is done by the caller.
    """
    timeout = adaptive_timeout(len(page_nums))

    for model in GEMINI_MODELS:
        prompt = build_gemini_prompt(page_nums, img_paths)
        for attempt in range(MAX_RETRIES):
            try:
                print(
                    f"  [{label}] gemini --model {model} (attempt {attempt + 1}, "
                    f"timeout {timeout}s for {len(page_nums)} pages)...",
                    flush=True,
                )
                text = run_gemini_cli(model, prompt, timeout=timeout)
                print(f"  [{label}] Done via gemini ({model})", flush=True)
                return text
            except subprocess.TimeoutExpired:
                print(f"  [{label}] {model} timed out after {timeout}s", flush=True)
                raise  # propagate timeout so caller can split the chunk
            except Exception as exc:
                err = str(exc)
                if _is_rate_limit(err) and attempt < MAX_RETRIES - 1:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    print(f"  [{label}] Rate limit on {model} — retrying in {wait:.0f}s...", flush=True)
                    time.sleep(wait)
                else:
                    print(f"  [{label}] {model} failed: {err[:120]}", flush=True)
                    break  # try next model

    print(f"  [{label}] All Gemini models failed. Switching to Claude CLI...", flush=True)

    for model in CLAUDE_MODELS:
        prompt = build_claude_prompt(page_nums, img_paths)
        for attempt in range(MAX_RETRIES):
            try:
                print(
                    f"  [{label}] claude --model {model} (attempt {attempt + 1}, "
                    f"timeout {timeout}s for {len(page_nums)} pages)...",
                    flush=True,
                )
                text = run_claude_cli(model, prompt, timeout=timeout)
                print(f"  [{label}] Done via claude ({model})", flush=True)
                return text
            except subprocess.TimeoutExpired:
                print(f"  [{label}] {model} timed out after {timeout}s", flush=True)
                raise  # propagate timeout so caller can split the chunk
            except Exception as exc:
                err = str(exc)
                if attempt < MAX_RETRIES - 1:
                    wait = BACKOFF_BASE * (2 ** attempt)
                    print(f"  [{label}] {model} failed ({err[:80]}) — retrying in {wait:.0f}s...", flush=True)
                    time.sleep(wait)
                else:
                    print(f"  [{label}] {model} exhausted.", flush=True)
                    break

    raise RuntimeError(
        f"All Gemini and Claude models failed for {label}. "
        "Check that 'gemini' and 'claude' CLIs are installed and authenticated."
    )


def extract_chunk_with_fallback(
    chunk_label: str,
    page_nums: list[int],
    img_paths: list[Path],
    split_depth: int = 0,
) -> str:
    """
    Extract a chunk of slides.  On timeout, automatically splits the chunk in
    half and retries each half (up to MAX_SPLIT_DEPTH halvings).
    Raises RuntimeError only if ALL models AND all splits are exhausted.
    """
    label = f"{chunk_label}[d{split_depth}]" if split_depth else chunk_label
    try:
        return _try_all_models(label, page_nums, img_paths)
    except subprocess.TimeoutExpired:
        if split_depth >= MAX_SPLIT_DEPTH or len(page_nums) <= 1:
            raise RuntimeError(
                f"Timeout on {label} ({len(page_nums)} pages) even at minimum split size. "
                "Try reducing DPI (--dpi 100) or processing a smaller page range."
            )
        mid = len(page_nums) // 2
        left_pages, right_pages = page_nums[:mid], page_nums[mid:]
        left_imgs, right_imgs = img_paths[:mid], img_paths[mid:]
        print(
            f"  [{label}] Timeout — splitting into two sub-chunks of "
            f"{len(left_pages)} and {len(right_pages)} pages and retrying...",
            flush=True,
        )
        left_text = extract_chunk_with_fallback(
            f"{chunk_label}L", left_pages, left_imgs, split_depth + 1
        )
        right_text = extract_chunk_with_fallback(
            f"{chunk_label}R", right_pages, right_imgs, split_depth + 1
        )
        return left_text + "\n\n" + right_text


# ── Response parsing ──────────────────────────────────────────────────────────

def parse_batch_response(text: str, page_nums: list[int]) -> dict[int, str]:
    """
    Extract '## Slide N' sections from a batch response.
    Falls back to assigning the full text to the first page if the model
    didn't follow the format.
    """
    pattern = re.compile(
        r"##\s+[Ss]lide\s+(\d+)\s*\n(.*?)(?=\n##\s+[Ss]lide\s+\d+|\Z)",
        re.DOTALL,
    )
    result = {}
    for m in pattern.finditer(text):
        pn = int(m.group(1))
        if pn in page_nums:
            result[pn] = m.group(2).strip()

    if not result and text.strip():
        # Model didn't follow the format — store full response under first page
        result[page_nums[0]] = text.strip()

    return result


# ── Cache management ──────────────────────────────────────────────────────────

def clear_cache(directory: Path, specific_pdfs: list[Path] | None = None) -> None:
    """Delete extracted directories, legacy _extracted.md files, and temp PNGs."""
    deleted = []

    if specific_pdfs:
        for pdf in specific_pdfs:
            # New format: per-slide directory
            target_dir = pdf.parent / (pdf.stem + "_extracted")
            if target_dir.exists() and target_dir.is_dir():
                shutil.rmtree(target_dir, ignore_errors=True)
                deleted.append(target_dir)
            # Legacy format: single .md file
            target_md = pdf.parent / (pdf.stem + "_extracted.md")
            if target_md.exists():
                target_md.unlink()
                deleted.append(target_md)
    else:
        for d in directory.rglob("*_extracted"):
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
                deleted.append(d)
        # Legacy format cleanup
        for f in directory.rglob("*_extracted.md"):
            f.unlink()
            deleted.append(f)

    # Clean up any stray temp PNG dirs
    for d in directory.rglob("_sb_tmp_*"):
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            deleted.append(d)

    if deleted:
        print(f"Cleared {len(deleted)} cached item(s):")
        for f in deleted:
            print(f"  - {f}")
    else:
        print("No cached files found to clear.")


# ── Per-slide directory output ─────────────────────────────────────────────────

def get_output_dir(pdf_path: Path, args) -> Path:
    if hasattr(args, "output") and args.output:
        return Path(args.output).resolve()
    return pdf_path.parent / (pdf_path.stem + "_extracted")


def load_existing_slides(output_dir: Path) -> set[int]:
    """Return the set of slide numbers already written to output_dir."""
    existing = set()
    if output_dir.exists():
        for f in output_dir.glob("slide_*.md"):
            m = re.match(r"slide_(\d+)\.md$", f.name)
            if m:
                existing.add(int(m.group(1)))
    return existing


def write_slide(output_dir: Path, pn: int, text: str) -> None:
    """Write a single slide's content to its own file."""
    slide_path = output_dir / f"slide_{pn:04d}.md"
    slide_path.write_text(f"## Slide {pn}\n\n{text}", encoding="utf-8")


def write_index(output_dir: Path, pdf_name: str, total_pages: int,
                start_page: int, end_page: int, dpi: int,
                extracted_pages: list[int]) -> None:
    """Write/overwrite index.md with current extraction metadata."""
    lines = [
        f"# Extracted: {pdf_name}",
        f"- Total pages: {total_pages}",
        f"- Extracted range: {start_page} to {end_page}",
        f"- DPI: {dpi}",
        f"- Slides extracted: {len(extracted_pages)}",
        "",
        "## Slide Index",
    ]
    for pn in sorted(extracted_pages):
        lines.append(f"- slide_{pn:04d}.md — Slide {pn}")
    (output_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


# ── Single PDF extraction ─────────────────────────────────────────────────────

def process_single_pdf(pdf_path: Path, args) -> Path:
    """Extract one PDF into a per-slide directory. Returns output directory path."""
    import fitz

    output_dir = get_output_dir(pdf_path, args)
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(str(pdf_path))
    total_pages = len(doc)
    doc.close()

    start_idx = args.start_page - 1
    end_idx = min(args.end_page, total_pages) if args.end_page else total_pages
    all_indices = list(range(start_idx, end_idx))

    # Resume: find which slide files already exist
    existing: set[int] = set()
    if args.resume:
        existing = load_existing_slides(output_dir)
        if existing:
            print(f"[{pdf_path.name}] Resuming — {len(existing)} slides already extracted")

    needed = [i for i in all_indices if (i + 1) not in existing]
    if not needed:
        print(f"[{pdf_path.name}] All {len(all_indices)} pages already extracted — nothing to do.")
        return output_dir

    # Adaptive chunking: use at least MIN_CHUNKS but cap each chunk at MAX_PAGES_PER_CHUNK
    n_chunks = max(MIN_CHUNKS, math.ceil(len(needed) / MAX_PAGES_PER_CHUNK))
    n_chunks = min(n_chunks, len(needed))  # never more chunks than pages
    chunk_size = math.ceil(len(needed) / n_chunks)
    chunks = [needed[i : i + chunk_size] for i in range(0, len(needed), chunk_size)]

    print(
        f"[{pdf_path.name}] {len(needed)} pages -> {len(chunks)} chunk(s) "
        f"(~{chunk_size} pages/chunk, adaptive timeout ~{adaptive_timeout(chunk_size)}s/chunk)"
    )

    new_slides: dict[int, str] = {}

    # Use CWD (project root) for temp images — Gemini CLI's workspace is CWD,
    # and @file references break on paths with spaces (e.g. "Lecture Slides/").
    # CWD is always the space-free project root when invoked via study-buddy.
    tmp_dir = Path.cwd() / f"_sb_tmp_{pdf_path.stem[:30]}"
    tmp_dir.mkdir(exist_ok=True)

    try:
        for chunk_idx, page_indices in enumerate(chunks):
            page_nums = [i + 1 for i in page_indices]
            print(
                f"\n[{pdf_path.name}] Rendering chunk {chunk_idx + 1}/{len(chunks)} "
                f"({len(page_nums)} pages: {page_nums[0]}-{page_nums[-1]})...",
                flush=True,
            )

            # Render pages to temp PNG files
            img_paths = render_chunk_to_files(pdf_path, page_indices, tmp_dir, dpi=args.dpi)

            # Run CLI extraction (raises only if all models and splits exhausted)
            chunk_label = f"Chunk {chunk_idx + 1}/{len(chunks)} (slides {page_nums[0]}-{page_nums[-1]})"
            raw_text = extract_chunk_with_fallback(chunk_label, page_nums, img_paths)

            # Clean up temp images for this chunk immediately
            for p in img_paths:
                p.unlink(missing_ok=True)

            # Parse response into per-slide sections
            parsed = parse_batch_response(raw_text, page_nums)

            # Ensure every page in the chunk has an entry
            for pn in page_nums:
                if pn not in parsed:
                    parsed[pn] = (
                        raw_text.strip()
                        if pn == page_nums[0]
                        else f"[Extracted as part of batch with slides {page_nums[0]}-{page_nums[-1]}. See slide {page_nums[0]} for full batch text if parsing failed.]"
                    )

                # Write each slide immediately (so partial extractions are saved on crash)
                write_slide(output_dir, pn, parsed[pn])
                new_slides[pn] = parsed[pn]

    finally:
        # Always clean up temp dir
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Write/update index.md with all extracted slides
    all_extracted = sorted(existing | set(new_slides.keys()))
    write_index(
        output_dir, pdf_path.name, total_pages,
        args.start_page, args.end_page or total_pages,
        args.dpi, all_extracted,
    )

    print(f"\n[{pdf_path.name}] Done — {len(new_slides)} slides written to {output_dir}/")
    return output_dir


# ── Dependency check ──────────────────────────────────────────────────────────

def check_dependencies() -> list[str]:
    missing = []
    for pkg, import_name, install_cmd in [
        ("pymupdf", "fitz", "pip install pymupdf"),
        ("Pillow", "PIL", "pip install Pillow"),
    ]:
        try:
            __import__(import_name)
        except ImportError:
            missing.append(f"{pkg} not installed — run: {install_cmd}")
    return missing


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Extract PDF slides — per-slide directory output with Gemini/Claude CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("pdfs", nargs="*", help="PDF file(s) to extract (processed one at a time)")
    parser.add_argument("--output", "-o", help="Output directory path (single PDF only)")
    parser.add_argument("--start-page", type=int, default=1, help="First page, 1-indexed (default: 1)")
    parser.add_argument("--end-page", type=int, default=None, help="Last page inclusive (default: last)")
    parser.add_argument("--dpi", type=int, default=DPI, help=f"Render DPI (default: {DPI})")
    parser.add_argument("--resume", action="store_true", help="Skip slides already present in output directory")
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        help=(
            "Delete all *_extracted/ directories (and legacy *_extracted.md files) in the current directory. "
            "If PDF(s) are also given, only clear those specific extractions."
        ),
    )
    args = parser.parse_args()

    if args.clear_cache:
        specific = [Path(p).resolve() for p in args.pdfs] if args.pdfs else None
        clear_cache(Path.cwd(), specific)
        if not args.pdfs:
            return  # cache-clear only, nothing else to do

    if not args.pdfs:
        parser.print_help()
        sys.exit(0)

    missing = check_dependencies()
    if missing:
        print("Missing dependencies — install them first:")
        for m in missing:
            print(f"  [FAIL] {m}")
        sys.exit(1)

    # Validate all paths upfront
    pdf_paths = []
    for p in args.pdfs:
        path = Path(p).resolve()
        if not path.exists():
            print(f"Error: File not found: {path}")
            sys.exit(1)
        pdf_paths.append(path)

    # Process one PDF at a time (sequential)
    for pdf_path in pdf_paths:
        print(f"\n{'=' * 60}")
        print(f"Processing: {pdf_path.name}")
        print(f"{'=' * 60}")
        try:
            process_single_pdf(pdf_path, args)
        except RuntimeError as exc:
            print(f"\n[FATAL] {exc}")
            sys.exit(1)

    if len(pdf_paths) > 1:
        print(f"\nAll {len(pdf_paths)} PDF(s) processed successfully.")


if __name__ == "__main__":
    main()
