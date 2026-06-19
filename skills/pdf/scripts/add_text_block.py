#!/usr/bin/env python3
"""
Insert a new text block into a PDF section while preserving the original
fonts, colors, and layout of all existing content.

Strategy:
  1. Content below the insertion point is shifted down in the content stream
     directly — original embedded fonts are untouched.
  2. The new text block is drawn as a reportlab overlay on top, at the
     insertion point.

The new block uses standard Helvetica (or any font you specify). If you need
the new text to exactly match the surrounding font, extract the font name from
the PDF first (e.g. with pdfplumber page.chars) and pass it as --font,
bearing in mind that embedded subset fonts may not render correctly in new
overlay content — Helvetica is the safe default.

Usage:
    python scripts/add_text_block.py <input.pdf> <output.pdf> \\
        --page <N> --y <top_y> \\
        --text "Line 1\\nLine 2" \\
        [--x <left_x>] [--width <w>] \\
        [--font Helvetica] [--font-size 12] \\
        [--line-height <lh>] [--color "r,g,b"]

Coordinates use pdfplumber convention: y=0 at page top.

Example:
    python scripts/add_text_block.py in.pdf out.pdf \\
        --page 1 --y 200 --text "New paragraph here." \\
        --font-size 11
"""

import argparse
import io
import os
import sys

import pdfplumber
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import simpleSplit
from pypdf import PdfReader, PdfWriter

from reflow_page import _get_stream_bytes, _shift_stream, _shift_cm_blocks, _set_stream_bytes


def _wrap_text(text: str, font: str, font_size: float, max_width: float) -> list:
    lines = []
    for paragraph in text.split("\n"):
        wrapped = simpleSplit(paragraph, font, font_size, max_width)
        lines.extend(wrapped if wrapped else [""])
    return lines


def _text_block_overlay(
    pw: float,
    ph: float,
    x: float,
    y_top: float,
    lines: list,
    font: str,
    font_size: float,
    line_height: float,
    color: tuple,
) -> io.BytesIO:
    """Draw the new text block as a reportlab overlay PDF page."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
    r, g, b = color
    c.setFillColorRGB(r, g, b)
    c.setFont(font, font_size)
    # RL baseline for first line: ph - y_top - font_size
    y_cursor = ph - y_top - font_size
    for line in lines:
        c.drawString(x, y_cursor, line)
        y_cursor -= line_height
    c.save()
    buf.seek(0)
    return buf


def add_text_block(
    input_pdf: str,
    output_pdf: str,
    page_num: int,
    insert_y: float,
    text: str,
    insert_x: float = 50.0,
    block_width: float | None = None,
    font: str = "Helvetica",
    font_size: float = 12.0,
    line_height: float | None = None,
    color: tuple = (0.0, 0.0, 0.0),
) -> None:
    if line_height is None:
        line_height = font_size * 1.2

    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)

    if page_num < 1 or page_num > total_pages:
        print(f"Error: page {page_num} out of range (1–{total_pages})")
        sys.exit(1)

    page_idx = page_num - 1
    page = reader.pages[page_idx]
    mb = page.mediabox
    pw, ph = float(mb.width), float(mb.height)

    if block_width is None:
        block_width = pw - 2 * insert_x

    lines = _wrap_text(text, font, font_size, block_width)
    block_height = len(lines) * line_height

    # 1. Shift all content at or below insert_y down by block_height
    raw = _get_stream_bytes(page)
    modified = _shift_stream(raw, ph, insert_y, block_height)
    modified = _shift_cm_blocks(modified, ph, insert_y, block_height)
    _set_stream_bytes(page, modified)

    # 2. Overlay the new text block at insert_y
    overlay_buf = _text_block_overlay(pw, ph, insert_x, insert_y, lines,
                                      font, font_size, line_height, color)
    overlay_reader = PdfReader(overlay_buf)
    page.merge_page(overlay_reader.pages[0])

    writer = PdfWriter()
    for i, p in enumerate(reader.pages):
        writer.add_page(p)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    print(f"Inserted {len(lines)} line(s) ({block_height:.0f}pt) at page {page_num}, y={insert_y:.0f}")
    print(f"Shifted existing content below down by {block_height:.0f}pt")
    print(f"Saved → {output_pdf}")


def main():
    parser = argparse.ArgumentParser(
        description="Insert a text block into a PDF, shifting existing content down (preserves original fonts)."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--y", type=float, required=True,
                        help="Top of insertion point (pdfplumber, y=0 at page top)")
    parser.add_argument("--text", required=True, help="Text to insert (use \\n for newlines)")
    parser.add_argument("--x", type=float, default=50.0, help="Left margin (default 50)")
    parser.add_argument("--width", type=float, default=None, help="Block width in points")
    parser.add_argument("--font", default="Helvetica",
                        help="Font for inserted block (default: Helvetica)")
    parser.add_argument("--font-size", type=float, default=12.0)
    parser.add_argument("--line-height", type=float, default=None)
    parser.add_argument("--color", default="0,0,0",
                        help="Text color as r,g,b floats 0-1 (default: 0,0,0 = black)")

    args = parser.parse_args()

    if not os.path.isfile(args.input_pdf):
        print(f"Error: file not found: {args.input_pdf}")
        sys.exit(1)

    try:
        color = tuple(float(v) for v in args.color.split(","))
        assert len(color) == 3
    except Exception:
        print("Error: --color must be three comma-separated floats, e.g. 0,0,0")
        sys.exit(1)

    add_text_block(
        input_pdf=args.input_pdf,
        output_pdf=args.output_pdf,
        page_num=args.page,
        insert_y=args.y,
        text=args.text.replace("\\n", "\n"),
        insert_x=args.x,
        block_width=args.width,
        font=args.font,
        font_size=args.font_size,
        line_height=args.line_height,
        color=color,
    )


if __name__ == "__main__":
    main()
