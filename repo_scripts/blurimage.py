#!/usr/bin/env python3
r"""
blurimage.py — OCR-based image redaction tool for blurring sensitive text in screenshots.

Designed primarily for terminal/K9s screenshots where secrets, usernames, session IDs,
device IDs, and other sensitive information need to be redacted before sharing.

== How it works ==

1. The image is loaded and preprocessed for OCR (Tesseract).
2. Tesseract runs OCR and returns bounding boxes for each detected word.
3. Each word (and each reconstructed line) is checked against user-supplied patterns.
4. Matching regions are blurred with a strong Gaussian blur on the original image.
5. The result is saved as a PNG.

== Preprocessing: why multi-pass with upscaling? ==

Terminal screenshots have colored text on dark backgrounds. Tesseract expects dark text
on white backgrounds. Naive grayscale conversion (cv2.COLOR_BGR2GRAY) uses weights
0.114*B + 0.587*G + 0.299*R — this means BLUE text (common in terminals) contributes
only ~11% to the grayscale value and becomes nearly invisible to Tesseract.

To handle this, we run three OCR passes with different preprocessing:

  Pass 1 — Weighted grayscale + OTSU threshold + invert
    Standard approach. Works well for white and yellow text which are bright across all
    channels. Catches config tables, URLs, timestamps, and other high-contrast text.

  Pass 2 — Max-channel grayscale + OTSU threshold + invert
    Takes np.max(B, G, R) per pixel instead of weighted average. This preserves ANY
    colored text equally: blue (B=255) → 255, green (G=255) → 255, etc. Catches text
    that Pass 1 misses due to color weighting, but may introduce more noise from colored
    UI elements (borders, highlights).

  Pass 3 — Blue channel only + OTSU threshold + invert
    Extracts just the blue channel (img[:,:,0] in BGR). Specifically targets blue/cyan
    terminal text like log output lines. Blue text has high values in this channel while
    the dark background stays low, giving good contrast.

Each pass uses OTSU thresholding (cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU) which
automatically finds the optimal threshold for clean black/white separation, rather than
simple inversion which preserves anti-aliasing noise.

Additionally, the image is upscaled (default 2x) before OCR. Tesseract works best at
~300 DPI; terminal screenshots are typically lower resolution. Upscaling gives Tesseract
more pixels to work with, dramatically improving recognition of small monospaced fonts.
Bounding boxes are scaled back down before applying blur to the original image.

The three passes produce separate OCR results that are merged. Block numbers are offset
per pass to avoid collisions in line grouping. Duplicate detections (same text region
found by multiple passes) just result in that region being blurred multiple times, which
is harmless — double blur is just more blur.

== Two-level matching: word-level and line-level ==

Tesseract returns bounding boxes per WORD. Patterns are matched at two levels:

  Word-level matching:
    Each OCR word is individually checked against the pattern. This catches simple
    cases like a username ("henning") or domain ("elasticc.io") appearing within a
    single word token. Only the matching word's bounding box is blurred.

  Line-level matching:
    Words are grouped into lines using Tesseract's (block_num, par_num, line_num)
    metadata. The words are joined with spaces and the combined line text is checked
    against the pattern. This catches multi-word patterns like "session id \S+" that
    span word boundaries.

    IMPORTANT: Line-level matching does NOT blur the entire line. Instead, it maps
    the regex match span (character positions) back to the individual words that
    overlap with the match, and only blurs those specific words. This prevents
    over-redaction where non-sensitive parts of a line (like "Received a undecryptable
    Megolm event from") would be unnecessarily blurred.

== Pattern types ==

  --blur (literal phrases, CASE-INSENSITIVE):
    Escaped with re.escape() so special characters are treated literally.
    Wrapped in (?i:...) for case-insensitive matching.
    Example: --blur "elasticc.io" matches "elasticc.io", "Elasticc.IO", etc.

  --blur-regex (regex patterns, CASE-SENSITIVE by default):
    Used as-is in the regex alternation. The user controls case sensitivity — add (?i)
    within the pattern if case-insensitive matching is desired.
    Example: --blur-regex "rVFe\S+" matches "rVFeSJeIA6kb5Kwi0TjE5rWq7su4zrb6"
    Example: --blur-regex "session id \S+" matches "session id +1TqgiGcW4h5bQ0G..."
    Example: --blur-regex "[A-Z]{8,}" matches ONLY uppercase device IDs like "UZTBBJTHZW"
             (does NOT match "vroomfondel" — no global IGNORECASE on regex patterns)

  Hardcoded patterns (always active):
    PXL.*              — PXL-prefixed camera file names
    .*\.png$           — PNG filenames
    .*\.jpg$           — JPEG filenames
    .*\.mp4$           — MP4 filenames
    .*\.json$          — JSON filenames

== Example usage ==

  # Blur usernames, domain, client secret, session IDs, and device IDs:
  python blurimage.py \\
    --blur matrixadmin henning elasticc.io \\
    --blur-regex "rVFe\\S+" "session id \\S+" "[A-Z]{8,}" \\
    screenshot.png

  # Debug mode — show what Tesseract detects (grouped by line):
  python blurimage.py --debug --blur myuser screenshot.png

  # Skip inversion for light-background images:
  python blurimage.py --no-invert --blur myuser screenshot.png

  # Higher upscaling for very small text (slower but better recognition):
  python blurimage.py --scale 3 --blur myuser screenshot.png

== Dependencies ==

  System:  tesseract-ocr (apt install tesseract-ocr)
  Python:  pytesseract, opencv-python (auto-installed if missing)
"""


def install_and_import(packagename: str, pipname: str) -> None:
    """Auto-install a Python package if not available, then import it into globals."""
    import importlib

    try:
        importlib.import_module(packagename)
    except ImportError:
        import pip

        pip.main(["install", pipname])
    finally:
        globals()[packagename] = importlib.import_module(packagename)


install_and_import(packagename="pytesseract", pipname="pytesseract")
install_and_import(packagename="cv2", pipname="opencv-python")

import shutil

if shutil.which("tesseract") is None:
    raise SystemExit("tesseract is not installed or not in PATH. Install it, e.g.: apt install tesseract-ocr")

import argparse
import re
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
import pytesseract
from pytesseract import Output


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for blurimage."""
    parser = argparse.ArgumentParser(
        description="Blur sensitive text in an image using OCR detection. See module docstring for full documentation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --blur myuser elasticc.io screenshot.png\n"
            '  %(prog)s --blur myuser --blur-regex "secret\\S+" "[A-Z]{8,}" -- screenshot.png\n'
            "  %(prog)s --debug --blur myuser screenshot.png\n"
            "\n"
            "Note: When --blur-regex is the last flag before the image argument, use '--'\n"
            "to prevent argparse from treating the filename as a regex pattern.\n"
        ),
    )
    parser.add_argument("--blur", nargs="+", default=[], help="Literal phrases to blur (re.escape'd, case-insensitive)")
    parser.add_argument(
        "--blur-regex",
        nargs="+",
        default=[],
        help="Regex patterns to blur (used as-is, case-SENSITIVE — add (?i) in pattern for case-insensitive)",
    )
    parser.add_argument("--no-invert", action="store_true", help="Skip preprocessing (for light-background images)")
    parser.add_argument("--scale", type=int, default=2, help="Upscale factor before OCR (default: 2, 1=off)")
    parser.add_argument("--debug", action="store_true", help="Print all OCR-detected lines before blurring")
    parser.add_argument("image", help="Path to input image")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if not args.blur and not args.blur_regex:
        parser.error("at least one of --blur or --blur-regex is required")

    image_path = args.image
    p = Path(image_path)
    output_path = str(p.with_name(p.stem.removesuffix(".local") + "_blurred" + p.suffix))

    img = cv2.imread(image_path)
    if img is None:
        raise SystemExit(f"Could not read image: {image_path}")

    # Tesseract config: OEM 3 = default (LSTM), PSM 6 = assume uniform block of text
    custom_config = r"--oem 3 --psm 6"
    scale = args.scale

    if not args.no_invert:
        # --- Multi-pass OCR with preprocessing for dark terminal screenshots ---
        # See module docstring for detailed explanation of why this is needed.

        # Upscale for better recognition of small monospaced terminal fonts.
        # Bounding boxes are scaled back after OCR.
        if scale > 1:
            upscaled = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        else:
            upscaled = img

        passes = []

        # Pass 1: Weighted grayscale (0.114*B + 0.587*G + 0.299*R) + OTSU
        # Best for white/yellow text. Blue text is underrepresented (~11% weight).
        gray1 = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
        _, thresh1 = cv2.threshold(gray1, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        passes.append(thresh1)

        # Pass 2: Max-channel grayscale (max of B, G, R per pixel) + OTSU
        # Preserves ALL colored text equally. Blue(255,0,0) → 255 instead of → 29.
        gray2 = np.max(upscaled, axis=2).astype(np.uint8)
        _, thresh2 = cv2.threshold(gray2, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        passes.append(thresh2)

        # Pass 3: Blue channel only (img[:,:,0] in BGR) + OTSU
        # Specifically targets blue/cyan terminal text (log output, error messages).
        _, thresh3 = cv2.threshold(upscaled[:, :, 0], 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        passes.append(thresh3)

        # Run OCR on each preprocessed image and merge results.
        # Block numbers are offset per pass so line grouping doesn't collide across passes.
        all_results: list[dict[str, list]] = []  # type: ignore[type-arg]
        block_offset = 0
        for ocr_img in passes:
            d_pass = pytesseract.image_to_data(ocr_img, output_type=Output.DICT, config=custom_config)
            if block_offset > 0:
                d_pass["block_num"] = [b + block_offset for b in d_pass["block_num"]]
            # Scale bounding boxes back to original image coordinates
            if scale > 1:
                d_pass["left"] = [v // scale for v in d_pass["left"]]
                d_pass["top"] = [v // scale for v in d_pass["top"]]
                d_pass["width"] = [v // scale for v in d_pass["width"]]
                d_pass["height"] = [v // scale for v in d_pass["height"]]
            max_block = max(d_pass["block_num"]) if d_pass["block_num"] else 0
            block_offset = max_block + 1
            all_results.append(d_pass)

        # Merge all passes into a single OCR result dict
        d: dict[str, list] = {k: [] for k in all_results[0]}  # type: ignore[type-arg]
        for d_pass in all_results:
            for k in d:
                d[k].extend(d_pass[k])
    else:
        # No preprocessing — for images that already have dark text on light background
        d = pytesseract.image_to_data(img, output_type=Output.DICT, config=custom_config)

    # --- Build combined regex pattern ---
    # Literal phrases (--blur) are re.escape'd and wrapped in (?i:...) for case-insensitive matching.
    # Regex patterns (--blur-regex) are used AS-IS — the user controls case sensitivity
    # (e.g. [A-Z]{8,} should only match uppercase, not be forced case-insensitive).
    # Hardcoded filename patterns are also case-insensitive.
    parts = [rf"(?i:{re.escape(phrase)})" for phrase in args.blur]
    parts += list(args.blur_regex)
    parts += [r"(?i:PXL.*)", r"(?i:.*\.png$)", r"(?i:.*\.jpg$)", r"(?i:.*\.mp4$)", r"(?i:.*\.json$)"]
    pattern = re.compile(rf"({'|'.join(parts)})")

    # --- Group OCR words into lines ---
    # Tesseract assigns each word a (block_num, par_num, line_num) triple.
    # We group by this triple to reconstruct lines for multi-word pattern matching.
    n_boxes = len(d["text"])
    lines: dict[tuple[int, int, int], list[int]] = defaultdict(list)
    for i in range(n_boxes):
        text = d["text"][i].strip()
        if not text:
            continue
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        lines[key].append(i)

    if args.debug:
        print("--- OCR erkannte Zeilen ---")
        for key in sorted(lines):
            line_text = " ".join(d["text"][i].strip() for i in lines[key])
            print(f"  Zeile {key}: {line_text}")
        print("---")

    blur_indices: set[int] = set()

    # --- Stage 1: Word-level matching ---
    # Check each individual OCR word against the pattern. This catches single-word
    # matches like usernames, domains within compound tokens, etc.
    for i in range(n_boxes):
        text = d["text"][i].strip()
        if text and pattern.search(text):
            blur_indices.add(i)

    # --- Stage 2: Line-level matching (targeted, not whole-line) ---
    # For patterns that span multiple words (e.g. "session id \S+"), we join words
    # back into lines and match against the combined text.
    #
    # Unlike naive whole-line blurring, we map regex match character spans back to
    # the individual words that overlap with the match. This way only the actually
    # matched words are blurred, keeping non-sensitive context (timestamps, log
    # prefixes like "Received a undecryptable...") readable.
    for key, indices in lines.items():
        if all(i in blur_indices for i in indices):
            continue  # all words already matched at word level, skip

        # Track character position of each word in the joined line text
        # so we can map regex match spans back to word bounding boxes.
        word_spans: list[tuple[int, int, int]] = []  # (char_start, char_end, box_index)
        pos = 0
        for i in indices:
            text = d["text"][i].strip()
            word_spans.append((pos, pos + len(text), i))
            pos += len(text) + 1  # +1 for the space between words

        line_text = " ".join(d["text"][i].strip() for i in indices)

        # Find all pattern matches in the line and blur only overlapping words
        for match in pattern.finditer(line_text):
            ms, me = match.span()
            for ws, we, i in word_spans:
                if ws < me and we > ms:  # word overlaps with match span
                    blur_indices.add(i)

    # --- Apply Gaussian blur to all matched bounding boxes ---
    # Kernel size (31,31) and sigma 30 produce a strong blur that makes text unreadable.
    # The blur is applied on the ORIGINAL image (not the preprocessed OCR image).
    for i in sorted(blur_indices):
        x, y, w, h = (d["left"][i], d["top"][i], d["width"][i], d["height"][i])
        roi = img[y : y + h, x : x + w]
        blur = cv2.GaussianBlur(roi, (31, 31), 30)
        img[y : y + h, x : x + w] = blur
        print(f"Geblurrt: {d['text'][i].strip()}")

    # Save as PNG for lossless quality of the non-blurred regions
    cv2.imwrite(output_path, img)
    print(f"Fertig! Bild gespeichert unter {output_path}")


if __name__ == "__main__":
    main()
