#!/usr/bin/env python3
"""
Insert a text block into a PDF section while preserving surrounding content.

When inserted text is taller than the cleared area, all content below the
insertion point is shifted down automatically. Words below are redrawn with
their original font, size, and color sampled from the PDF character data.
If overflow pushes content past the page bottom, it spills to the next page.

Usage:
    python scripts/add_text_block.py <input.pdf> <output.pdf> \\
        --page <N> --y <top_y> \\
        --text "Line 1\\nLine 2" \\
        [--x <left_x>] [--width <w>] \\
        [--font Helvetica] [--font-size 12] \\
        [--line-height <lh>] [--margin-bottom <m>]

Coordinates:
    --page   1-based page number
    --y      y position from the TOP of the page (pdfplumber convention)
    --x      left margin for the inserted block (default: 50)
    --width  width of the inserted text block in points (default: page_width - 2*x)

Note: --font and --font-size apply only to the newly inserted text.
      Existing words shifted below it keep their original appearance.

Example:
    python scripts/add_text_block.py in.pdf out.pdf \\
        --page 1 --y 200 --text "New paragraph here."
"""

import argparse
import os
import sys

import pdfplumber
from reportlab.lib.utils import simpleSplit
from pypdf import PdfReader, PdfWriter

from pdf_style_utils import word_style, build_overlay


def _wrap_text(text: str, font: str, font_size: float, max_width: float) -> list:
    lines = []
    for paragraph in text.split("\n"):
        wrapped = simpleSplit(paragraph, font, font_size, max_width)
        lines.extend(wrapped if wrapped else [""])
    return lines


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
    margin_bottom: float = 4.0,
) -> None:
    if line_height is None:
        line_height = font_size * 1.2

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
        chars = plumber_page.chars

        if block_width is None:
            block_width = pw - 2 * insert_x

        lines = _wrap_text(text, font, font_size, block_width)
        new_block_h = len(lines) * line_height + margin_bottom

        words_below = [w for w in plumber_page.extract_words() if w["top"] >= insert_y - 1]

    def page_dims(idx):
        mb = reader.pages[idx].mediabox
        return float(mb.width), float(mb.height)

    overlays: dict[int, list] = {}

    def ensure(idx):
        if idx not in overlays:
            overlays[idx] = []

    # Erase the insertion zone
    ensure(page_idx)
    overlays[page_idx].append({
        "type": "white_rect",
        "x0": 0, "x1": pw,
        "y_top": insert_y,
        "y_bottom": min(ph, insert_y + new_block_h),
    })

    # Draw the new block (user-specified font/size/color)
    overlays[page_idx].append({
        "type": "text_block",
        "x": insert_x,
        "y_top": insert_y,
        "lines": lines,
        "font": font,
        "font_size": font_size,
        "line_height": line_height,
        "color": (0.0, 0.0, 0.0),
    })

    content_start_y = insert_y + new_block_h

    if words_below:
        original_top = min(w["top"] for w in words_below)
        shift = content_start_y - original_top

        if shift > 0:
            orig_bottom = max(w["bottom"] for w in words_below)
            ensure(page_idx)
            overlays[page_idx].append({
                "type": "white_rect",
                "x0": 0, "x1": pw,
                "y_top": original_top,
                "y_bottom": orig_bottom,
            })

        margin_top = 36.0
        margin_bot = 36.0

        for word in words_below:
            style = word_style(word, chars)
            new_top = word["top"] + shift
            fs = style["font_size"]

            if new_top + fs <= ph - margin_bot:
                dest_idx = page_idx
                draw_y = new_top
            else:
                dest_idx = page_idx + 1
                if dest_idx >= total_pages:
                    print(
                        f"Warning: '{word['text']}' overflows past last page "
                        f"(y={new_top:.0f}). Consider adding a page."
                    )
                    dest_idx = total_pages - 1
                    draw_y = margin_top
                else:
                    draw_y = margin_top + (new_top - (ph - margin_bot))

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

    print(f"Inserted {len(lines)} line(s) at page {page_num}, y={insert_y:.0f}")
    print(f"Saved → {output_pdf}")


def main():
    parser = argparse.ArgumentParser(
        description="Insert a text block into a PDF, shifting content below downward."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--y", type=float, required=True,
                        help="Top of insertion point (pdfplumber, y=0 at page top)")
    parser.add_argument("--text", required=True, help="Text to insert (use \\n for newlines)")
    parser.add_argument("--x", type=float, default=50.0)
    parser.add_argument("--width", type=float, default=None)
    parser.add_argument("--font", default="Helvetica",
                        help="Font for the inserted block (default: Helvetica)")
    parser.add_argument("--font-size", type=float, default=12.0)
    parser.add_argument("--line-height", type=float, default=None)
    parser.add_argument("--margin-bottom", type=float, default=4.0)

    args = parser.parse_args()

    if not os.path.isfile(args.input_pdf):
        print(f"Error: file not found: {args.input_pdf}")
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
        margin_bottom=args.margin_bottom,
    )


if __name__ == "__main__":
    main()
