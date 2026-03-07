#!/usr/bin/env python3
"""
Checks that all dependencies for study-buddy are installed and working.

Run this once before your first study session:
    python check_setup.py
"""

import subprocess
import sys


def _wrap(args: list[str]) -> list[str]:
    if sys.platform == "win32":
        return ["cmd", "/c"] + args
    return args


def check():
    ok = True

    # Python version
    major, minor = sys.version_info[:2]
    if (major, minor) < (3, 10):
        print(f"[WARN] Python {major}.{minor} detected. Recommend Python 3.10+.")
    else:
        print(f"[OK]   Python {major}.{minor}")

    # pymupdf
    try:
        import fitz
        print(f"[OK]   pymupdf {fitz.version[0]}")
    except ImportError:
        print("[FAIL] pymupdf not installed  ->  pip install pymupdf")
        ok = False

    # Pillow
    try:
        import PIL
        print(f"[OK]   Pillow {PIL.__version__}")
    except ImportError:
        print("[FAIL] Pillow not installed  ->  pip install Pillow")
        ok = False

    # Gemini CLI
    gemini_ok = False
    result = subprocess.run(_wrap(["gemini", "--version"]), capture_output=True, text=True)
    if result.returncode == 0:
        ver = (result.stdout + result.stderr).strip().splitlines()[0]
        print(f"[OK]   gemini CLI: {ver}")
        gemini_ok = True
    else:
        print("[FAIL] gemini CLI not found on PATH")
        print("       Install: npm install -g @google/gemini-cli")
        ok = False

    # Gemini CLI live connectivity test
    if gemini_ok:
        print("       Testing Gemini CLI (gemini-2.0-flash)...", end="", flush=True)
        try:
            result = subprocess.run(
                _wrap(["gemini", "--model", "gemini-2.0-flash", "--output-format", "text",
                       "-p", "Reply with exactly: READY"]),
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0 and result.stdout.strip():
                print(f" OK ({result.stdout.strip()[:30]})")
            else:
                print(f" FAIL\n       stderr: {result.stderr.strip()[:200]}")
                ok = False
        except subprocess.TimeoutExpired:
            print(" FAIL (timed out after 30s)")
            ok = False
        except Exception as e:
            print(f" FAIL ({e})")
            ok = False

    # Claude CLI
    claude_ok = False
    result = subprocess.run(_wrap(["claude", "--version"]), capture_output=True, text=True)
    if result.returncode == 0:
        ver = (result.stdout + result.stderr).strip().splitlines()[0]
        print(f"[OK]   claude CLI: {ver}")
        claude_ok = True
    else:
        print("[WARN] claude CLI not found on PATH (needed for fallback if Gemini fails)")
        print("       Claude Code must be installed and authenticated.")

    # Claude CLI live connectivity test
    if claude_ok:
        print("       Testing Claude CLI fallback...", end="", flush=True)
        try:
            result = subprocess.run(
                _wrap(["claude", "--model", "claude-sonnet-4-6", "-p",
                       "Reply with exactly: READY", "--dangerously-skip-permissions"]),
                capture_output=True, text=True, timeout=30,
                encoding="utf-8", errors="replace",
            )
            if result.returncode == 0 and result.stdout.strip():
                print(f" OK ({result.stdout.strip()[:30]})")
            else:
                print(f" FAIL\n       stderr: {result.stderr.strip()[:200]}")
        except subprocess.TimeoutExpired:
            print(" FAIL (timed out after 30s)")
        except Exception as e:
            print(f" FAIL ({e})")

    # comtypes (PPT/PPTX -> PDF)
    try:
        import comtypes  # noqa: F401
        print("[OK]   comtypes — PPT/PPTX conversion available")
    except ImportError:
        print("[WARN] comtypes not installed — PPT/PPTX conversion unavailable")
        print("       Fix: pip install comtypes")

    print()
    if ok:
        print("All required checks passed! You're ready to study.")
        print("\nExample usage:")
        print("  python process_pdf.py slides.pdf")
        print("  python process_pdf.py slides.pdf --end-page 41")
        print("  python process_pdf.py slides.pdf --resume      # continue interrupted run")
        print("  python process_pdf.py --clear-cache            # delete all cached extractions")
    else:
        print("Fix the issues marked [FAIL] above, then run this script again.")
        sys.exit(1)


if __name__ == "__main__":
    check()
