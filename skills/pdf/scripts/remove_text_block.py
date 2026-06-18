#!/usr/bin/env python3
"""
Remove a rectangular text region from a PDF and shift content below upward
to fill the gap, preserving the original font, size, and color of every word.

Usage:
    python scripts/remove_text_block.py <input.pdf> <output.pdf> \\
        --page <N> --y-top <t> --y-bottom <b> \\
        [--x0 <x0>] [--x1 <x1>]

Coordinates:
    --page     1-based page number
    --y-top    top of the region to remove (pdfplumber: y=0 at top)
    --y-bottom bottom of the region to remove
    --x0       left edge of the removal region (default: 0)
    --x1       right edge of the removal region (default: page width)

All content whose bounding box is entirely within [x0,x1] x [y_top,y_bottom]
is erased. Everything strictly below y_bottom is shifted upward by
(y_bottom - y_top), redrawn with its original font and color.

Example:
    python scripts/remove_text_block.py in.pdf out.pdf \\
        --page 2 --y-top 300 --y-bottom 360
"""

import argparse
import os
import sys

import pdfplumber
from pypdf import PdfReader, PdfWriter

from pdf_style_utils import word_style, build_overlay


def remove_text_block(
    input_pdf: str,
    output_pdf: str,
    page_num: int,
    y_top: float,
    y_bottom: float,
    x0: float | None = None,
    x1: float | None = None,
) -> None:
    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)

    if page_num < 1 or page_num > total_pages:
        print(f"Error: page {page_num} out of range (1–{total_pages})")
        sys.exit(1)

    page_idx = page_num - 1
    removed_height = y_bottom - y_top

    with pdfplumber.open(input_pdf) as plumber_pdf:
        plumber_page = plumber_pdf.pages[page_idx]
        pw = float(plumber_page.width)
        ph = float(plumber_page.height)

        rx0 = x0 if x0 is not None else 0.0
        rx1 = x1 if x1 is not None else pw

        words = plumber_page.extract_words()
        chars = plumber_page.chars

    operations = []

    # 1. Erase the removal zone
    operations.append({
        "type": "white_rect",
        "x0": rx0, "x1": rx1,
        "y_top": y_top, "y_bottom": y_bottom,
    })

    # 2. Words strictly below the removal zone (within horizontal bounds)
    words_below = [
        w for w in words
        if w["top"] >= y_bottom - 1
        and w["x0"] >= rx0 - 1
        and w["x1"] <= rx1 + 1
    ]

    if words_below:
        orig_top = min(w["top"] for w in words_below)
        orig_bottom = max(w["bottom"] for w in words_below)

        # Erase original positions
        operations.append({
            "type": "white_rect",
            "x0": rx0, "x1": rx1,
            "y_top": orig_top,
            "y_bottom": orig_bottom,
        })

        # Redraw each word shifted up, preserving its original style
        for word in words_below:
            style = word_style(word, chars)
            operations.append({
                "type": "text_word",
                "x": word["x0"],
                "y_top": word["top"] - removed_height,
                "text": word["text"],
                **style,
            })

    buf = build_overlay(pw, ph, operations)
    overlay_reader = PdfReader(buf)

    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i == page_idx:
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    print(f"Removed y=[{y_top:.0f},{y_bottom:.0f}] on page {page_num}, "
          f"shifted {len(words_below)} word(s) up")
    print(f"Saved → {output_pdf}")


def main():
    parser = argparse.ArgumentParser(
        description="Remove a text region and shift content below upward."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--y-top", type=float, required=True)
    parser.add_argument("--y-bottom", type=float, required=True)
    parser.add_argument("--x0", type=float, default=None)
    parser.add_argument("--x1", type=float, default=None)

    args = parser.parse_args()

    if not os.path.isfile(args.input_pdf):
        print(f"Error: file not found: {args.input_pdf}")
        sys.exit(1)

    remove_text_block(
        input_pdf=args.input_pdf,
        output_pdf=args.output_pdf,
        page_num=args.page,
        y_top=args.y_top,
        y_bottom=args.y_bottom,
        x0=args.x0,
        x1=args.x1,
    )


if __name__ == "__main__":
    main()
