#!/usr/bin/env python3
"""
Shared helpers for sampling original font/color from PDF character data
and building reportlab overlay operations.

All y-coordinates follow pdfplumber convention: y=0 at page top.
"""

import io
from reportlab.pdfgen import canvas as rl_canvas


def rl_font_name(fontname: str) -> str:
    """Map an embedded PDF font name to the closest standard Helvetica variant."""
    low = fontname.lower()
    bold = "bold" in low
    italic = any(x in low for x in ("italic", "oblique", "it", "slant"))
    if bold and italic:
        return "Helvetica-BoldOblique"
    if bold:
        return "Helvetica-Bold"
    if italic:
        return "Helvetica-Oblique"
    return "Helvetica"


def normalize_color(color) -> tuple:
    """Normalize a pdfplumber color value to an (r, g, b) float tuple."""
    if color is None:
        return (0.0, 0.0, 0.0)
    if isinstance(color, (int, float)):
        v = float(color)
        return (v, v, v)
    if len(color) == 3:
        return tuple(float(x) for x in color)
    if len(color) == 4:
        c, m, y, k = color
        return (
            (1 - c) * (1 - k),
            (1 - m) * (1 - k),
            (1 - y) * (1 - k),
        )
    return (0.0, 0.0, 0.0)


def word_style(word: dict, page_chars: list) -> dict:
    """
    Sample font name, size, and color from the PDF characters that belong to
    *word*. Falls back to geometric approximations when char data is absent.

    Returns a dict with keys: font, font_size, color (rgb tuple).
    """
    sample = [
        c for c in page_chars
        if abs(c["top"] - word["top"]) < 3
        and c["x0"] >= word["x0"] - 1
        and c["x1"] <= word["x1"] + 1
    ]
    if sample:
        sc = sample[0]
        return {
            "font": rl_font_name(sc.get("fontname", "")),
            "font_size": float(sc["size"]),
            "color": normalize_color(sc.get("non_stroking_color")),
        }
    font_size = float(word["bottom"] - word["top"])
    return {
        "font": "Helvetica",
        "font_size": font_size,
        "color": (0.0, 0.0, 0.0),
    }


def build_overlay(pw: float, ph: float, operations: list) -> io.BytesIO:
    """
    Render a list of draw operations into an in-memory single-page PDF overlay.

    Supported operation types:

      white_rect — erase a rectangle with a white fill:
        {"type": "white_rect", "x0": f, "x1": f, "y_top": f, "y_bottom": f}

      text_word — draw a single word at its original baseline:
        {"type": "text_word", "x": f, "y_top": f,
         "text": str, "font": str, "font_size": f, "color": (r,g,b)}

      text_block — draw multiple lines (for inserted blocks):
        {"type": "text_block", "x": f, "y_top": f, "lines": [str, ...],
         "font": str, "font_size": f, "line_height": f, "color": (r,g,b)}

    All y values use pdfplumber convention (y=0 at page top).
    """
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(pw, ph))

    for op in operations:
        t = op["type"]

        if t == "white_rect":
            rl_y = ph - op["y_bottom"]
            h = op["y_bottom"] - op["y_top"]
            c.setFillColorRGB(1, 1, 1)
            c.rect(op["x0"], rl_y, op["x1"] - op["x0"], h, fill=1, stroke=0)

        elif t == "text_word":
            r, g, b = op["color"]
            c.setFillColorRGB(r, g, b)
            c.setFont(op["font"], op["font_size"])
            # baseline = page_height - y_top - font_size  (pdfplumber → RL)
            y_rl = ph - op["y_top"] - op["font_size"]
            c.drawString(op["x"], y_rl, op["text"])

        elif t == "text_block":
            r, g, b = op["color"]
            c.setFillColorRGB(r, g, b)
            c.setFont(op["font"], op["font_size"])
            lh = op["line_height"]
            y_cursor = ph - op["y_top"] - op["font_size"]
            for line in op["lines"]:
                c.drawString(op["x"], y_cursor, line)
                y_cursor -= lh

    c.save()
    buf.seek(0)
    return buf
