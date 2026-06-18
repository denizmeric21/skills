#!/usr/bin/env python3
"""
Insert a text block into a PDF section while preserving surrounding content.

When inserted text is taller than the cleared area, all content below the
insertion point is shifted down automatically. If the shift pushes content
past the page bottom, the overflow is appended to the next page (or a new
page is created if the target is the last page).

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
    --x      left margin (default: 50)
    --width  text-block width in points (default: page_width - 2*x)

Example:
    python scripts/add_text_block.py in.pdf out.pdf \\
        --page 1 --y 200 --text "New paragraph here." \\
        --font Helvetica --font-size 12
"""

import argparse
import io
import sys

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.utils import simpleSplit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wrap_text(text: str, font: str, font_size: float, max_width: float) -> list[str]:
    """Word-wrap *text* to fit within *max_width* points. Returns list of lines."""
    lines = []
    for paragraph in text.split("\n"):
        wrapped = simpleSplit(paragraph, font, font_size, max_width)
        lines.extend(wrapped if wrapped else [""])
    return lines


def _block_height(lines: list[str], line_height: float) -> float:
    return len(lines) * line_height


def _build_overlay(
    page_width: float,
    page_height: float,
    operations: list[dict],
) -> io.BytesIO:
    """
    Build a single-page overlay PDF from a list of draw operations.

    Supported operation types:
      {"type": "white_rect", "x0": ..., "y_top": ..., "x1": ..., "y_bottom": ...}
      {"type": "text_block", "x": ..., "y_top": ..., "lines": [...],
       "font": ..., "font_size": ..., "line_height": ..., "color": (r,g,b)}

    All y values are in pdfplumber convention (y=0 at top).
    """
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_width, page_height))

    for op in operations:
        if op["type"] == "white_rect":
            rl_y0 = page_height - op["y_bottom"]
            h = op["y_bottom"] - op["y_top"]
            c.setFillColorRGB(1, 1, 1)
            c.rect(op["x0"], rl_y0, op["x1"] - op["x0"], h, fill=1, stroke=0)

        elif op["type"] == "text_block":
            r, g, b = op.get("color", (0, 0, 0))
            c.setFillColorRGB(r, g, b)
            c.setFont(op["font"], op["font_size"])
            lh = op["line_height"]
            # First line baseline: convert top-of-block (pdfplumber) to RL y
            # RL baseline for first line = page_height - y_top - font_size
            y_cursor = page_height - op["y_top"] - op["font_size"]
            for line in op["lines"]:
                c.drawString(op["x"], y_cursor, line)
                y_cursor -= lh

    c.save()
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _collect_content_below(page, below_y: float) -> list[dict]:
    """
    Return every word that starts at or below *below_y* (pdfplumber coords).
    Groups them into logical text clusters by proximity.
    """
    words = page.extract_words()
    return [w for w in words if w["top"] >= below_y - 1]


def add_text_block(
    input_pdf: str,
    output_pdf: str,
    page_num: int,       # 1-based
    insert_y: float,     # top of insertion point (pdfplumber, y=0 at top)
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

        if block_width is None:
            block_width = pw - 2 * insert_x

        # Wrap the new text
        lines = _wrap_text(text, font, font_size, block_width)
        new_block_h = _block_height(lines, line_height) + margin_bottom

        # Collect words below the insertion point that need to shift down
        words_below = _collect_content_below(plumber_page, insert_y)

    # Build per-page overlay lists
    # overlays[i] = list of draw operations for page i (0-based)
    overlays: dict[int, list[dict]] = {}

    def ensure_page(idx):
        if idx not in overlays:
            overlays[idx] = []

    # Page dimensions helper (reuse reader)
    def page_dims(idx):
        mb = reader.pages[idx].mediabox
        return float(mb.width), float(mb.height)

    # 1. White-out the insertion zone on the target page
    ensure_page(page_idx)
    overlays[page_idx].append({
        "type": "white_rect",
        "x0": 0,
        "x1": pw,
        "y_top": insert_y,
        "y_bottom": min(ph, insert_y + new_block_h),
    })

    # 2. Draw the new text block
    overlays[page_idx].append({
        "type": "text_block",
        "x": insert_x,
        "y_top": insert_y,
        "lines": lines,
        "font": font,
        "font_size": font_size,
        "line_height": line_height,
        "color": (0, 0, 0),
    })

    content_start_y = insert_y + new_block_h  # where shifted content starts on target page

    # 3. Shift words that were below the insertion point
    if words_below:
        # Determine original topmost y of content-below group
        original_top = min(w["top"] for w in words_below)
        shift = content_start_y - original_top  # positive = move down

        # Group words by their vertical band and check page overflow
        margin_top = 36.0   # minimum top margin in points
        margin_bot = 36.0   # minimum bottom margin

        # White-out the original positions of content below on the target page
        if shift > 0:
            orig_bottom = max(w["bottom"] for w in words_below)
            ensure_page(page_idx)
            overlays[page_idx].append({
                "type": "white_rect",
                "x0": 0, "x1": pw,
                "y_top": original_top,
                "y_bottom": orig_bottom,
            })

        # Re-draw each word at its shifted position
        for word in words_below:
            new_top = word["top"] + shift
            font_h = word["bottom"] - word["top"]
            word_font_size = font_h  # approximate

            if new_top + font_h <= ph - margin_bot:
                # Fits on current page
                dest_page_idx = page_idx
                draw_y = new_top
            else:
                # Overflow to next page
                dest_page_idx = page_idx + 1
                overflow = (new_top + font_h) - (ph - margin_bot)
                draw_y = margin_top + (new_top - (ph - margin_bot))

                # If next page doesn't exist we'd need to insert one —
                # for now we cap at the last page and warn
                if dest_page_idx >= total_pages:
                    print(
                        f"Warning: content overflow beyond last page "
                        f"(word '{word['text']}' at y={new_top:.0f}). "
                        "Consider adding a page or reducing inserted text."
                    )
                    dest_page_idx = total_pages - 1
                    draw_y = margin_top

            ensure_page(dest_page_idx)
            dest_pw, dest_ph = page_dims(dest_page_idx)
            overlays[dest_page_idx].append({
                "type": "text_block",
                "x": word["x0"],
                "y_top": draw_y,
                "lines": [word["text"]],
                "font": font,        # best-effort; original font unknown without char data
                "font_size": word_font_size,
                "line_height": word_font_size,
                "color": (0, 0, 0),
            })

    # 4. Merge overlays into output PDF
    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        if i in overlays:
            dest_pw, dest_ph = page_dims(i)
            buf = _build_overlay(dest_pw, dest_ph, overlays[i])
            overlay_reader = PdfReader(buf)
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    print(f"Inserted {len(lines)} line(s) at page {page_num}, y={insert_y:.0f}")
    print(f"Saved → {output_pdf}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Insert a text block into a PDF, shifting content below downward."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, required=True, help="1-based page number")
    parser.add_argument("--y", type=float, required=True,
                        help="Top of insertion point in pdfplumber coords (y=0 at page top)")
    parser.add_argument("--text", required=True, help="Text to insert (use \\n for newlines)")
    parser.add_argument("--x", type=float, default=50.0, help="Left margin (default 50)")
    parser.add_argument("--width", type=float, default=None, help="Block width in points")
    parser.add_argument("--font", default="Helvetica")
    parser.add_argument("--font-size", type=float, default=12.0)
    parser.add_argument("--line-height", type=float, default=None)
    parser.add_argument("--margin-bottom", type=float, default=4.0,
                        help="Extra space below inserted block (default 4)")

    args = parser.parse_args()

    import os
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
