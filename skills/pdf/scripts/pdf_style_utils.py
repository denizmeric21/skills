#!/usr/bin/env python3
"""
Shared helpers for sampling original font/color from PDF character data.
Used by replace_text.py when redrawing a replaced text span.
"""


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
