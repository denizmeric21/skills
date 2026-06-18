#!/usr/bin/env python3
"""
Reflow page content: shift everything below a given y-position up or down,
preserving the original font, size, and color of every word.

Overflow to the next page is handled automatically when a shifted word
falls below the bottom margin (36pt).

Usage:
    python scripts/reflow_page.py <input.pdf> <output.pdf> \\
        --page <N> --below-y <y> --shift <delta> \\
        [--x0 <x0>] [--x1 <x1>]

Arguments:
    --below-y   Shift words whose top >= this y (pdfplumber: y=0 at top)
    --shift     Points to shift: positive=down, negative=up
    --x0/--x1   Horizontal band to restrict shifting (default: full width)

Example — push everything below y=400 down by 30 points on page 1:
    python scripts/reflow_page.py in.pdf out.pdf --page 1 --below-y 400 --shift 30

Example — pull content up by 20 points (close a gap):
    python scripts/reflow_page.py in.pdf out.pdf --page 1 --below-y 300 --shift -20
"""

import argparse
import os
import sys

import pdfplumber
from pypdf import PdfReader, PdfWriter

from pdf_style_utils import word_style, build_overlay


MARGIN_TOP = 36.0
MARGIN_BOT = 36.0


def reflow_page(
    input_pdf: str,
    output_pdf: str,
    page_num: int,
    below_y: float,
    shift: float,
    x0: float | None = None,
    x1: float | None = None,
) -> None:
    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)

    if page_num < 1 or page_num > total_pages:
        print(f"Error: page {page_num} out of range (1–{total_pages})")
        sys.exit(1)

    page_idx = page_num - 1

    with pdfplumber.open(input_pdf) as plumber_pdf:
        plumber_page = plumber_pdf.pages[page_idx]
        pw = float(plumber_page.width)
        ph = float(plumber_page.height)

        bx0 = x0 if x0 is not None else 0.0
        bx1 = x1 if x1 is not None else pw

        words = plumber_page.extract_words()
        chars = plumber_page.chars

    affected = [
        w for w in words
        if w["top"] >= below_y - 1
        and w["x0"] >= bx0 - 1
        and w["x1"] <= bx1 + 1
    ]

    if not affected:
        print(f"No words found below y={below_y:.0f} in the specified band.")
        sys.exit(0)

    overlays: dict[int, list] = {}

    def ensure(idx):
        if idx not in overlays:
            overlays[idx] = []

    def page_dims(idx):
        mb = reader.pages[idx].mediabox
        return float(mb.width), float(mb.height)

    # Erase all affected words from the source page in one rectangle
    ensure(page_idx)
    orig_top = min(w["top"] for w in affected)
    orig_bot = max(w["bottom"] for w in affected)
    overlays[page_idx].append({
        "type": "white_rect",
        "x0": bx0, "x1": bx1,
        "y_top": orig_top, "y_bottom": orig_bot,
    })

    overflow_count = 0

    for word in affected:
        style = word_style(word, chars)
        new_top = word["top"] + shift
        fs = style["font_size"]

        if new_top >= MARGIN_TOP and (new_top + fs) <= (ph - MARGIN_BOT):
            dest_idx = page_idx
            draw_y = new_top
        elif new_top < MARGIN_TOP and shift < 0:
            dest_idx = page_idx
            draw_y = MARGIN_TOP
        else:
            dest_idx = page_idx + 1
            if dest_idx >= total_pages:
                print(f"Warning: '{word['text']}' overflows past last page — dropped.")
                overflow_count += 1
                continue
            overshoot = new_top - (ph - MARGIN_BOT)
            draw_y = MARGIN_TOP + overshoot

        ensure(dest_idx)
        overlays[dest_idx].append({
            "type": "text_word",
            "x": word["x0"],
            "y_top": draw_y,
            "text": word["text"],
            **style,
        })

    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i in overlays:
            dpw, dph = page_dims(i)
            buf = build_overlay(dpw, dph, overlays[i])
            overlay_reader = PdfReader(buf)
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    direction = "down" if shift > 0 else "up"
    moved = len(affected) - overflow_count
    print(f"Shifted {moved} word(s) {direction} by {abs(shift):.0f}pt on page {page_num}")
    if overflow_count:
        print(f"Dropped {overflow_count} word(s) that overflowed past the last page")
    print(f"Saved → {output_pdf}")


def main():
    parser = argparse.ArgumentParser(
        description="Shift content below a y-position up or down, preserving original styles."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--below-y", type=float, required=True,
                        help="Shift words whose top >= this y (pdfplumber, y=0 at top)")
    parser.add_argument("--shift", type=float, required=True,
                        help="Points to shift: positive=down, negative=up")
    parser.add_argument("--x0", type=float, default=None)
    parser.add_argument("--x1", type=float, default=None)

    args = parser.parse_args()

    if not os.path.isfile(args.input_pdf):
        print(f"Error: file not found: {args.input_pdf}")
        sys.exit(1)

    reflow_page(
        input_pdf=args.input_pdf,
        output_pdf=args.output_pdf,
        page_num=args.page,
        below_y=args.below_y,
        shift=args.shift,
        x0=args.x0,
        x1=args.x1,
    )


if __name__ == "__main__":
    main()
