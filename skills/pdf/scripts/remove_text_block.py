#!/usr/bin/env python3
"""
Remove a rectangular text region from a PDF and shift content below upward
to fill the gap, preserving everything else on the page.

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
is erased. Content that overlaps the region only partially is left untouched.
Everything strictly below y_bottom is shifted upward by (y_bottom - y_top).

Example:
    python scripts/remove_text_block.py in.pdf out.pdf \\
        --page 2 --y-top 300 --y-bottom 360
"""

import argparse
import io
import os
import sys

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas


# ---------------------------------------------------------------------------
# Overlay builder (same coordinate conventions as add_text_block)
# ---------------------------------------------------------------------------

def _build_overlay(pw: float, ph: float, operations: list[dict]) -> io.BytesIO:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))

    for op in operations:
        if op["type"] == "white_rect":
            rl_y0 = ph - op["y_bottom"]
            h = op["y_bottom"] - op["y_top"]
            c.setFillColorRGB(1, 1, 1)
            c.rect(op["x0"], rl_y0, op["x1"] - op["x0"], h, fill=1, stroke=0)

        elif op["type"] == "text_block":
            r, g, b = op.get("color", (0, 0, 0))
            c.setFillColorRGB(r, g, b)
            c.setFont(op["font"], op["font_size"])
            # RL baseline from pdfplumber top-of-word
            y_rl = ph - op["y_top"] - op["font_size"]
            c.drawString(op["x"], y_rl, op["text"])

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def remove_text_block(
    input_pdf: str,
    output_pdf: str,
    page_num: int,
    y_top: float,
    y_bottom: float,
    x0: float | None = None,
    x1: float | None = None,
    font: str = "Helvetica",
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

    operations: list[dict] = []

    # 1. White-out the removal zone
    operations.append({
        "type": "white_rect",
        "x0": rx0, "x1": rx1,
        "y_top": y_top, "y_bottom": y_bottom,
    })

    # 2. Identify words strictly below the removal zone
    words_below = [
        w for w in words
        if w["top"] >= y_bottom - 1          # below (or at) bottom of removed region
        and w["x0"] >= rx0 - 1               # within horizontal bounds
        and w["x1"] <= rx1 + 1
    ]

    if words_below:
        # White-out their original positions
        orig_top = min(w["top"] for w in words_below)
        orig_bottom = max(w["bottom"] for w in words_below)
        operations.append({
            "type": "white_rect",
            "x0": rx0, "x1": rx1,
            "y_top": orig_top,
            "y_bottom": orig_bottom,
        })

        # Re-draw them shifted up
        for word in words_below:
            new_top = word["top"] - removed_height
            font_size = word["bottom"] - word["top"]
            operations.append({
                "type": "text_block",
                "x": word["x0"],
                "y_top": new_top,
                "text": word["text"],
                "font": font,
                "font_size": font_size,
                "color": (0, 0, 0),
            })

    # 3. Merge overlay
    buf = _build_overlay(pw, ph, operations)
    overlay_reader = PdfReader(buf)

    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i == page_idx:
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    region_desc = f"y=[{y_top:.0f},{y_bottom:.0f}]"
    print(f"Removed region {region_desc} on page {page_num}, shifted {len(words_below)} word(s) up")
    print(f"Saved → {output_pdf}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Remove a text region from a PDF and shift content below upward."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--y-top", type=float, required=True,
                        help="Top of removal region (pdfplumber y=0 at top)")
    parser.add_argument("--y-bottom", type=float, required=True,
                        help="Bottom of removal region")
    parser.add_argument("--x0", type=float, default=None, help="Left bound (default: 0)")
    parser.add_argument("--x1", type=float, default=None, help="Right bound (default: page width)")
    parser.add_argument("--font", default="Helvetica",
                        help="Font to use when redrawing shifted text (default: Helvetica)")

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
        font=args.font,
    )


if __name__ == "__main__":
    main()
