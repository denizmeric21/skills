#!/usr/bin/env python3
"""
Reflow page content: shift everything below a given y-position up or down,
optionally spilling overflow to the next page.

Use this after add_text_block or remove_text_block when you want fine-grained
control over vertical shifts without modifying the insertion/removal itself.
Also useful for adjusting section spacing or fixing crowded areas.

Usage:
    python scripts/reflow_page.py <input.pdf> <output.pdf> \\
        --page <N> --below-y <y> --shift <delta> \\
        [--x0 <x0>] [--x1 <x1>] \\
        [--font Helvetica]

Arguments:
    --below-y   Only words whose top >= this y are shifted (pdfplumber coords)
    --shift     Positive = shift DOWN, negative = shift UP (points)
    --x0/--x1   Horizontal band to restrict shifting (default: full width)
    --font      Fallback font for redrawn words (default: Helvetica)

Overflow handling:
    When a word's new position falls below the page bottom margin (36pt),
    it is placed on the next page at the same x, starting from the top margin.
    If there is no next page, a warning is printed and the word is dropped.

Example — push everything below y=400 down by 30 points on page 1:
    python scripts/reflow_page.py in.pdf out.pdf --page 1 --below-y 400 --shift 30

Example — pull content up by 20 points (fill a gap):
    python scripts/reflow_page.py in.pdf out.pdf --page 1 --below-y 300 --shift -20
"""

import argparse
import io
import os
import sys

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas


MARGIN_TOP = 36.0
MARGIN_BOT = 36.0


# ---------------------------------------------------------------------------
# Overlay builder
# ---------------------------------------------------------------------------

def _build_overlay(pw: float, ph: float, operations: list[dict]) -> io.BytesIO:
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))

    for op in operations:
        if op["type"] == "white_rect":
            rl_y = ph - op["y_bottom"]
            h = op["y_bottom"] - op["y_top"]
            c.setFillColorRGB(1, 1, 1)
            c.rect(op["x0"], rl_y, op["x1"] - op["x0"], h, fill=1, stroke=0)

        elif op["type"] == "text_block":
            r, g, b = op.get("color", (0, 0, 0))
            c.setFillColorRGB(r, g, b)
            c.setFont(op["font"], op["font_size"])
            y_rl = ph - op["y_top"] - op["font_size"]
            c.drawString(op["x"], y_rl, op["text"])

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def reflow_page(
    input_pdf: str,
    output_pdf: str,
    page_num: int,
    below_y: float,
    shift: float,
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

    with pdfplumber.open(input_pdf) as plumber_pdf:
        plumber_page = plumber_pdf.pages[page_idx]
        pw = float(plumber_page.width)
        ph = float(plumber_page.height)

        bx0 = x0 if x0 is not None else 0.0
        bx1 = x1 if x1 is not None else pw

        words = plumber_page.extract_words()

    # Words inside the horizontal band that are at or below below_y
    affected = [
        w for w in words
        if w["top"] >= below_y - 1
        and w["x0"] >= bx0 - 1
        and w["x1"] <= bx1 + 1
    ]

    if not affected:
        print(f"No words found below y={below_y:.0f} in the specified band.")
        sys.exit(0)

    # overlays[page_idx] = list of draw ops for that page
    overlays: dict[int, list[dict]] = {}

    def ensure(idx):
        if idx not in overlays:
            overlays[idx] = []

    def page_dims(idx):
        mb = reader.pages[idx].mediabox
        return float(mb.width), float(mb.height)

    # White-out original positions on source page
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
        new_top = word["top"] + shift
        font_size = max(1.0, word["bottom"] - word["top"])

        if new_top >= MARGIN_TOP and (new_top + font_size) <= (ph - MARGIN_BOT):
            # Fits on same page
            dest_idx = page_idx
            draw_y = new_top
        elif new_top < MARGIN_TOP and shift < 0:
            # Shifted too far up — clamp to margin
            dest_idx = page_idx
            draw_y = MARGIN_TOP
        else:
            # Overflow to next page
            dest_idx = page_idx + 1
            if dest_idx >= total_pages:
                print(
                    f"Warning: word '{word['text']}' overflows past the last page "
                    f"(new_top={new_top:.0f}). Dropping it."
                )
                overflow_count += 1
                continue
            # Map position onto next page starting from top margin
            overshoot = new_top - (ph - MARGIN_BOT)
            draw_y = MARGIN_TOP + overshoot

        ensure(dest_idx)
        overlays[dest_idx].append({
            "type": "text_block",
            "x": word["x0"],
            "y_top": draw_y,
            "text": word["text"],
            "font": font,
            "font_size": font_size,
            "color": (0, 0, 0),
        })

    # Merge overlays
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i in overlays:
            dpw, dph = page_dims(i)
            buf = _build_overlay(dpw, dph, overlays[i])
            overlay_reader = PdfReader(buf)
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    direction = "down" if shift > 0 else "up"
    print(
        f"Shifted {len(affected) - overflow_count} word(s) {direction} by {abs(shift):.0f}pt "
        f"on page {page_num}"
    )
    if overflow_count:
        print(f"Dropped {overflow_count} word(s) that overflowed past the last page")
    print(f"Saved → {output_pdf}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Shift all content below a y-position up or down, with overflow to next page."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, required=True, help="1-based page number")
    parser.add_argument("--below-y", type=float, required=True,
                        help="Shift words whose top >= this y (pdfplumber, y=0 at top)")
    parser.add_argument("--shift", type=float, required=True,
                        help="Points to shift: positive=down, negative=up")
    parser.add_argument("--x0", type=float, default=None, help="Left bound of affected band")
    parser.add_argument("--x1", type=float, default=None, help="Right bound of affected band")
    parser.add_argument("--font", default="Helvetica",
                        help="Fallback font for redrawn words (default: Helvetica)")

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
        font=args.font,
    )


if __name__ == "__main__":
    main()
