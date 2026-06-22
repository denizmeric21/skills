#!/usr/bin/env python3
"""
Remove a rectangular text region from a PDF and shift content below upward,
preserving the original fonts, colors, and layout of all shifted content.

Strategy:
  1. The page is recomposed from cropped original PDF vector bands.
  2. The removed band is omitted, and content below it is moved upward as a
     whole section, preserving original fonts, colors, images, lines, and
     shapes.

Usage:
    python scripts/remove_text_block.py <input.pdf> <output.pdf> \\
        --page <N> --y-top <t> --y-bottom <b>

Coordinates use pdfplumber convention: y=0 at page top.

Example:
    python scripts/remove_text_block.py in.pdf out.pdf \\
        --page 2 --y-top 300 --y-bottom 360
"""

import argparse
import os
import sys

from pypdf import PdfReader, PdfWriter

from pdf_layout_utils import compose_removed_region_page


def remove_text_block(
    input_pdf: str,
    output_pdf: str,
    page_num: int,
    y_top: float,
    y_bottom: float,
) -> None:
    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)

    if page_num < 1 or page_num > total_pages:
        print(f"Error: page {page_num} out of range (1–{total_pages})")
        sys.exit(1)

    page_idx = page_num - 1
    page = reader.pages[page_idx]
    mb = page.mediabox
    pw, ph = float(mb.width), float(mb.height)

    removed_height = y_bottom - y_top

    edited_page = compose_removed_region_page(page, pw, ph, y_top, y_bottom)

    writer = PdfWriter()
    for i, p in enumerate(reader.pages):
        writer.add_page(edited_page if i == page_idx else p)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    print(f"Removed y=[{y_top:.0f},{y_bottom:.0f}] on page {page_num}, "
          f"shifted content below up by {removed_height:.0f}pt")
    print(f"Saved → {output_pdf}")


def main():
    parser = argparse.ArgumentParser(
        description="Remove a text region and shift content below upward (preserves original fonts)."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--y-top", type=float, required=True,
                        help="Top of removal region (pdfplumber y=0 at top)")
    parser.add_argument("--y-bottom", type=float, required=True,
                        help="Bottom of removal region")

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
    )


if __name__ == "__main__":
    main()
