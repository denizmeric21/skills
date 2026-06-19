#!/usr/bin/env python3
"""
Remove a rectangular text region from a PDF and shift content below upward,
preserving the original fonts, colors, and layout of all shifted content.

Strategy:
  1. A white rectangle overlay erases the removed zone (via reportlab merge).
  2. Content below the removed zone is shifted up by editing the PDF content
     stream directly — so no text is redrawn and original embedded fonts are
     kept intact.

Usage:
    python scripts/remove_text_block.py <input.pdf> <output.pdf> \\
        --page <N> --y-top <t> --y-bottom <b>

Coordinates use pdfplumber convention: y=0 at page top.

Example:
    python scripts/remove_text_block.py in.pdf out.pdf \\
        --page 2 --y-top 300 --y-bottom 360
"""

import argparse
import io
import os
import sys

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas

from reflow_page import _get_stream_bytes, _shift_stream, _shift_cm_blocks, _set_stream_bytes


def _white_rect_overlay(pw: float, ph: float, y_top: float, y_bottom: float) -> io.BytesIO:
    """Build a single-page overlay PDF with a white rectangle covering the removal zone."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
    # pdfplumber y → reportlab y
    rl_y = ph - y_bottom
    h = y_bottom - y_top
    c.setFillColorRGB(1, 1, 1)
    c.rect(0, rl_y, pw, h + 2, fill=1, stroke=0)  # +2pt padding
    c.save()
    buf.seek(0)
    return buf


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

    # 1. Shift content below y_bottom upward in the content stream
    raw = _get_stream_bytes(page)
    modified = _shift_stream(raw, ph, y_bottom, -removed_height)
    modified = _shift_cm_blocks(modified, ph, y_bottom, -removed_height)
    _set_stream_bytes(page, modified)

    # 2. White-out the removal zone with an overlay
    overlay_buf = _white_rect_overlay(pw, ph, y_top, y_bottom)
    overlay_reader = PdfReader(overlay_buf)
    page.merge_page(overlay_reader.pages[0])

    writer = PdfWriter()
    for i, p in enumerate(reader.pages):
        writer.add_page(p)

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
