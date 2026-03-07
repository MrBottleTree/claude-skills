#!/usr/bin/env python3
"""
Convert PPT/PPTX (and other Office formats) to PDF using Microsoft PowerPoint
via COM automation. No internet, no LibreOffice — just PowerPoint on this machine.

Requirements:
    pip install comtypes
    Microsoft PowerPoint must be installed

Usage:
    python convert_to_pdf.py slides.pptx
    python convert_to_pdf.py slides1.ppt slides2.pptx --workers 4
    python convert_to_pdf.py --scan-dir .              # find + convert all in CWD tree
    python convert_to_pdf.py --scan-dir ./lectures
    python convert_to_pdf.py slides.pptx --output-dir ./pdfs
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


SUPPORTED_EXTENSIONS = {".ppt", ".pptx", ".pps", ".ppsx", ".odp"}
PP_SAVE_AS_PDF = 32  # PowerPoint SaveAs format constant for PDF


def _check_comtypes():
    try:
        import comtypes  # noqa: F401
        return True
    except ImportError:
        return False


def convert_one(input_path: Path, output_path: Path) -> Path:
    """
    Convert a single presentation to PDF using PowerPoint COM.
    Each call creates its own COM-initialized PowerPoint instance so that
    parallel workers don't share state.
    """
    import comtypes
    import comtypes.client

    # Must initialize COM per thread (STA model)
    comtypes.CoInitialize()
    powerpoint = None
    deck = None
    try:
        powerpoint = comtypes.client.CreateObject("PowerPoint.Application")
        # Note: Visible cannot be set to False via COM — window will briefly appear
        powerpoint.Visible = True

        deck = powerpoint.Presentations.Open(
            str(input_path),
            ReadOnly=True,
            Untitled=False,
            WithWindow=False,
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        deck.SaveAs(str(output_path), PP_SAVE_AS_PDF)
        print(f"  [done] {input_path.name} -> {output_path.name}", flush=True)
        return output_path
    finally:
        if deck is not None:
            try:
                deck.Close()
            except Exception:
                pass
        if powerpoint is not None:
            try:
                powerpoint.Quit()
            except Exception:
                pass
        # Small delay so PowerPoint fully releases before next worker picks up
        time.sleep(0.5)
        comtypes.CoUninitialize()


def scan_for_slides(root: Path) -> list[Path]:
    found = []
    for ext in SUPPORTED_EXTENSIONS:
        found.extend(root.rglob(f"*{ext}"))
    return sorted(found)


def main():
    parser = argparse.ArgumentParser(
        description="Convert PPT/PPTX to PDF via PowerPoint COM — no internet required",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("files", nargs="*", help="Files to convert (.ppt, .pptx, etc.)")
    parser.add_argument(
        "--scan-dir", "-s", metavar="DIR",
        help="Recursively scan a directory and convert ALL slide files found",
    )
    parser.add_argument(
        "--skip-existing", action="store_true", default=False,
        help="Skip conversion if a PDF with the same name already exists (auto-enabled with --scan-dir)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        help="Output directory for PDFs (default: same directory as each input file)",
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=2,
        help="Parallel workers (default: 2; each spawns its own PowerPoint instance)",
    )
    parser.add_argument(
        "--timeout", "-t", type=int, default=300,
        help="Seconds per file before giving up (default: 300)",
    )
    args = parser.parse_args()

    if not _check_comtypes():
        print("[FAIL] comtypes not installed.")
        print("       Fix: pip install comtypes")
        sys.exit(1)

    # Collect input files
    input_paths: list[Path] = []

    # --scan-dir always skips existing PDFs automatically
    skip_existing = args.skip_existing or bool(args.scan_dir)

    if args.scan_dir:
        scan_root = Path(args.scan_dir).resolve()
        found = scan_for_slides(scan_root)
        if found:
            print(f"Found {len(found)} slide file(s) under {scan_root}:")
            for f in found:
                print(f"  {f}")
        else:
            print(f"No supported slide files found under {scan_root}")
        input_paths.extend(found)

    for f in args.files:
        p = Path(f).resolve()
        if not p.exists():
            print(f"[WARN] File not found, skipping: {p}")
            continue
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            print(f"[WARN] Unsupported format '{p.suffix}', skipping: {p.name}")
            continue
        input_paths.append(p)

    # Filter out files whose PDF already exists (when skip_existing is on)
    if skip_existing:
        before = len(input_paths)
        input_paths = [
            p for p in input_paths
            if not ((Path(args.output_dir).resolve() if args.output_dir else p.parent) / (p.stem + ".pdf")).exists()
        ]
        skipped = before - len(input_paths)
        if skipped:
            print(f"Skipping {skipped} file(s) — PDF already exists.")

    if not input_paths:
        print("No files to convert.")
        sys.exit(0)

    print(f"\nConverting {len(input_paths)} file(s) with {args.workers} parallel worker(s)...\n")

    def _job(p: Path) -> Path:
        out_dir = Path(args.output_dir).resolve() if args.output_dir else p.parent
        out_pdf = out_dir / (p.stem + ".pdf")
        return convert_one(p, out_pdf)

    converted: list[Path] = []
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(_job, p): p for p in input_paths}
        for future in as_completed(futures):
            src = futures[future]
            try:
                converted.append(future.result())
            except Exception as e:
                errors.append(f"{src.name}: {e}")
                print(f"  [FAIL] {src.name}: {e}", flush=True)

    print(f"\nDone. {len(converted)} converted, {len(errors)} failed.")
    if converted:
        print("\nConverted PDFs:")
        for p in sorted(converted):
            print(f"  {p}")
    if errors:
        print("\nErrors:")
        for e in errors:
            print(f"  [FAIL] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
