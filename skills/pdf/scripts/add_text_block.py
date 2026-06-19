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
from pypdf import PageObject, PdfReader, PdfWriter

from pdf_style_utils import normalize_color, rl_font_name
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


def _white_rect_overlay(pw: float, ph: float, y_top: float, y_bottom: float) -> io.BytesIO:
    """Create an overlay that hides a rectangular band using pdfplumber y coordinates."""
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))
    c.setFillColorRGB(1, 1, 1)
    c.rect(0, ph - y_bottom, pw, y_bottom - y_top, fill=1, stroke=0)
    c.save()
    buf.seek(0)
    return buf


def _line_text(chars: list[dict]) -> str:
    """Rebuild a readable line from pdfplumber chars, preserving obvious spaces."""
    if not chars:
        return ""

    pieces = []
    prev = None
    for char in chars:
        if prev is not None:
            gap = char["x0"] - prev["x1"]
            space_width = max(prev.get("size", 10.0), char.get("size", 10.0)) * 0.28
            if gap > space_width:
                pieces.append(" ")
        pieces.append(char["text"])
        prev = char
    return "".join(pieces).strip()


def _extract_overflow_lines(input_pdf: str, page_idx: int, y_min: float) -> list[dict]:
    """
    Extract text lines that would be pushed off the page.

    We keep per-line x, size, color, and closest standard font. This is a
    fallback for pagination: existing in-page content is still moved as vector
    PDF content, but overflow has to be redrawn on a continuation page.
    """
    lines: list[dict] = []
    with pdfplumber.open(input_pdf) as pdf:
        page = pdf.pages[page_idx]
        chars = [c for c in page.chars if c["top"] >= y_min]
        chars.sort(key=lambda c: (round(c["top"], 1), c["x0"]))

        current: list[dict] = []
        current_top = None
        for char in chars:
            if current_top is None or abs(char["top"] - current_top) <= 3:
                current.append(char)
                if current_top is None:
                    current_top = char["top"]
            else:
                current.sort(key=lambda c: c["x0"])
                text = _line_text(current)
                if text:
                    first = current[0]
                    lines.append({
                        "text": text,
                        "x": min(c["x0"] for c in current),
                        "top": min(c["top"] for c in current),
                        "bottom": max(c["bottom"] for c in current),
                        "font": rl_font_name(first.get("fontname", "")),
                        "font_size": float(first["size"]),
                        "color": normalize_color(first.get("non_stroking_color")),
                    })
                current = [char]
                current_top = char["top"]

        if current:
            current.sort(key=lambda c: c["x0"])
            text = _line_text(current)
            if text:
                first = current[0]
                lines.append({
                    "text": text,
                    "x": min(c["x0"] for c in current),
                    "top": min(c["top"] for c in current),
                    "bottom": max(c["bottom"] for c in current),
                    "font": rl_font_name(first.get("fontname", "")),
                    "font_size": float(first["size"]),
                    "color": normalize_color(first.get("non_stroking_color")),
                })

    return lines


def _continuation_pages(
    lines: list[dict],
    pw: float,
    ph: float,
    top_margin: float,
    bottom_margin: float,
) -> list[PageObject]:
    """Draw extracted overflow text onto one or more blank continuation pages."""
    if not lines:
        return []

    pages: list[PageObject] = []
    first_top = min(line["top"] for line in lines)
    current_page = None
    current_canvas = None
    current_buffer = None
    previous_top = first_top
    y_cursor = top_margin

    def start_page():
        buf = io.BytesIO()
        canvas = rl_canvas.Canvas(buf, pagesize=(pw, ph))
        return buf, canvas

    def finish_page(buf, canvas):
        canvas.save()
        buf.seek(0)
        page = PageObject.create_blank_page(width=pw, height=ph)
        page.merge_page(PdfReader(buf).pages[0])
        pages.append(page)

    current_buffer, current_canvas = start_page()
    for index, line in enumerate(lines):
        if index == 0:
            y_cursor = top_margin
        else:
            y_cursor += max(line["top"] - previous_top, line["font_size"] * 1.2)

        if y_cursor + line["font_size"] > ph - bottom_margin and current_canvas is not None:
            finish_page(current_buffer, current_canvas)
            current_buffer, current_canvas = start_page()
            y_cursor = top_margin

        r, g, b = line["color"]
        current_canvas.setFillColorRGB(r, g, b)
        current_canvas.setFont(line["font"], line["font_size"])
        current_canvas.drawString(line["x"], ph - y_cursor - line["font_size"], line["text"])
        previous_top = line["top"]

    if current_canvas is not None:
        finish_page(current_buffer, current_canvas)

    return pages


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
    after_gap: float | None = None,
    paginate_overflow: bool = True,
    top_margin: float = 50.0,
    bottom_margin: float = 50.0,
) -> None:
    if line_height is None:
        line_height = font_size * 1.2
    if after_gap is None:
        after_gap = max(4.0, font_size * 0.35)

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
    block_height = len(lines) * line_height + after_gap
    overflow_lines = []
    continuation_pages = []
    spill_start = ph - bottom_margin - block_height

    if paginate_overflow and spill_start > insert_y:
        overflow_lines = _extract_overflow_lines(input_pdf, page_idx, spill_start)
        continuation_pages = _continuation_pages(
            overflow_lines,
            pw,
            ph,
            top_margin=top_margin,
            bottom_margin=bottom_margin,
        )

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

    if continuation_pages:
        hide_buf = _white_rect_overlay(pw, ph, ph - bottom_margin, ph)
        page.merge_page(PdfReader(hide_buf).pages[0])

    writer = PdfWriter()
    for i, p in enumerate(reader.pages):
        writer.add_page(p)
        if i == page_idx:
            for continuation_page in continuation_pages:
                writer.add_page(continuation_page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    print(f"Inserted {len(lines)} line(s) ({block_height:.0f}pt) at page {page_num}, y={insert_y:.0f}")
    print(f"Shifted existing content below down by {block_height:.0f}pt")
    if continuation_pages:
        print(
            f"Moved {len(overflow_lines)} overflow line(s) to "
            f"{len(continuation_pages)} continuation page(s)"
        )
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
    parser.add_argument("--after-gap", type=float, default=None,
                        help="Extra space after the inserted block before shifted content")
    parser.add_argument("--no-paginate-overflow", action="store_true",
                        help="Allow shifted content to clip at the page bottom")
    parser.add_argument("--top-margin", type=float, default=50.0,
                        help="Top margin for continuation pages")
    parser.add_argument("--bottom-margin", type=float, default=50.0,
                        help="Bottom margin before content spills to a continuation page")
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
        after_gap=args.after_gap,
        paginate_overflow=not args.no_paginate_overflow,
        top_margin=args.top_margin,
        bottom_margin=args.bottom_margin,
    )


if __name__ == "__main__":
    main()
