#!/usr/bin/env python3
"""
Shared helpers for sampling original font/color from PDF character data.
Used by PDF edit scripts when redrawing new text spans.
"""

from collections import Counter
from statistics import median


def rl_font_name(fontname: str) -> str:
    """Map a PDF font name to the closest built-in ReportLab PDF font."""
    low = (fontname or "").lower()
    bold = "bold" in low
    italic = any(x in low for x in ("italic", "oblique", "it", "slant"))

    if any(x in low for x in ("courier", "mono", "consolas", "menlo")):
        family = "Courier"
        bold_italic = "Courier-BoldOblique"
        bold_font = "Courier-Bold"
        italic_font = "Courier-Oblique"
    elif any(x in low for x in ("times", "serif", "georgia", "garamond")):
        family = "Times-Roman"
        bold_italic = "Times-BoldItalic"
        bold_font = "Times-Bold"
        italic_font = "Times-Italic"
    else:
        family = "Helvetica"
        bold_italic = "Helvetica-BoldOblique"
        bold_font = "Helvetica-Bold"
        italic_font = "Helvetica-Oblique"

    if bold and italic:
        return bold_italic
    if bold:
        return bold_font
    if italic:
        return italic_font
    return family


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


def dominant_text_style(chars: list[dict]) -> dict:
    """Return a stable style sampled across several PDF chars."""
    visible = [c for c in chars if c.get("text", "").strip()]
    if not visible:
        return {
            "font": "Helvetica",
            "font_size": 12.0,
            "color": (0.0, 0.0, 0.0),
        }

    font = Counter(rl_font_name(c.get("fontname", "")) for c in visible).most_common(1)[0][0]
    sizes = [float(c["size"]) for c in visible if "size" in c]
    colors = [
        tuple(round(v, 4) for v in normalize_color(c.get("non_stroking_color")))
        for c in visible
    ]
    color = Counter(colors).most_common(1)[0][0] if colors else (0.0, 0.0, 0.0)

    return {
        "font": font,
        "font_size": float(median(sizes)) if sizes else 12.0,
        "color": tuple(float(v) for v in color),
    }


def is_bold_font(fontname: str) -> bool:
    return "bold" in (fontname or "").lower()


def is_italic_font(fontname: str) -> bool:
    low = (fontname or "").lower()
    return any(x in low for x in ("italic", "oblique", "it", "slant"))
