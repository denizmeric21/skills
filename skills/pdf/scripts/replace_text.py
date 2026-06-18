#!/usr/bin/env python3
"""
Replace text in a PDF by covering the old text with a white box
and drawing the new text on top at the same position.

Usage:
    python scripts/replace_text.py <input.pdf> <output.pdf> <old_text> <new_text>

Example:
    python scripts/replace_text.py input.pdf output.pdf "Something smart" "No pain no gain"

Notes:
- Matching is case-insensitive, word-level, spans consecutive words.
- Replaces all occurrences across all pages.
- Works on PDFs with selectable (non-scanned) text.
- Font color and size are sampled from the original text and preserved.
- Font style is approximated: bold+italic → Helvetica-BoldOblique, bold →
  Helvetica-Bold, italic → Helvetica-Oblique, plain → Helvetica.
- pdfplumber extract_words() on the output may show garbled text due to the
  white overlay — this is expected. The rendered PDF will look correct.
"""

import sys
import os
import io
import pdfplumber
from reportlab.pdfgen import canvas as rl_canvas
from pypdf import PdfReader, PdfWriter


def _font_name(fontname: str) -> str:
    """Map an embedded font name to the closest standard Helvetica variant."""
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


def find_text_occurrences(pdf_path: str, search_text: str):
    """
    Return a list of occurrence dicts for every match of search_text.
    Each dict contains position, size, color, and font style info
    sampled from the first character of the match.
    """
    results = []
    search_words = search_text.lower().split()
    n = len(search_words)

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            words = page.extract_words()
            word_texts = [w["text"].lower() for w in words]

            for i in range(len(words) - n + 1):
                if word_texts[i:i+n] == search_words:
                    match_words = words[i:i+n]
                    x0 = min(w["x0"] for w in match_words)
                    x1 = max(w["x1"] for w in match_words)
                    top = min(w["top"] for w in match_words)
                    bottom = max(w["bottom"] for w in match_words)

                    # Sample font info from chars overlapping the first word
                    first_word = match_words[0]
                    sample_chars = [
                        c for c in page.chars
                        if abs(c["top"] - first_word["top"]) < 3
                        and c["x0"] >= first_word["x0"] - 1
                        and c["x1"] <= first_word["x1"] + 1
                    ]

                    if sample_chars:
                        sc = sample_chars[0]
                        font_size = sc["size"]
                        color = sc.get("non_stroking_color") or (0, 0, 0)
                        rl_font = _font_name(sc.get("fontname", ""))
                    else:
                        font_size = bottom - top
                        color = (0, 0, 0)
                        rl_font = "Helvetica"

                    # Normalize color to RGB tuple of floats 0-1
                    if isinstance(color, (int, float)):
                        color = (color, color, color)  # greyscale
                    elif len(color) == 4:
                        # CMYK → RGB
                        c2, m, y, k = color
                        color = (
                            (1 - c2) * (1 - k),
                            (1 - m) * (1 - k),
                            (1 - y) * (1 - k),
                        )

                    results.append({
                        "page": page_num,
                        "x0": x0, "top": top, "x1": x1, "bottom": bottom,
                        "page_height": page.height,
                        "page_width": page.width,
                        "font_size": font_size,
                        "color": color,
                        "rl_font": rl_font,
                    })

    return results


def make_overlay(occurrences: list, new_text: str) -> dict:
    """Build one in-memory overlay PDF per page. Returns {page_num: BytesIO}."""
    by_page = {}
    for occ in occurrences:
        by_page.setdefault(occ["page"], []).append(occ)

    overlays = {}
    for pn, occs in by_page.items():
        buf = io.BytesIO()
        ph = occs[0]["page_height"]
        pw = occs[0]["page_width"]
        c = rl_canvas.Canvas(buf, pagesize=(pw, ph))

        for occ in occs:
            x0, top, x1, bottom = occ["x0"], occ["top"], occ["x1"], occ["bottom"]
            font_size = occ["font_size"]
            r, g, b = occ["color"]

            # pdfplumber: y=0 at top → reportlab: y=0 at bottom
            rl_y_bottom = ph - bottom
            text_h = bottom - top

            # Cover old text with a white rectangle (generous padding)
            pad = 3
            c.setFillColorRGB(1, 1, 1)
            c.rect(x0 - pad, rl_y_bottom - pad,
                   (x1 - x0) + pad * 2, text_h + pad * 2,
                   fill=1, stroke=0)

            # Draw replacement text with original color and closest font
            c.setFillColorRGB(r, g, b)
            c.setFont(occ["rl_font"], font_size)
            # Baseline sits at rl_y_bottom (bottom of the glyph box)
            c.drawString(x0, rl_y_bottom, new_text)

        c.save()
        buf.seek(0)
        overlays[pn] = buf

    return overlays


def replace_text(input_pdf: str, output_pdf: str, old_text: str, new_text: str) -> int:
    occurrences = find_text_occurrences(input_pdf, old_text)

    if not occurrences:
        print(f'Text not found: "{old_text}"')
        return 0

    print(f'Found {len(occurrences)} occurrence(s) of "{old_text}"')

    overlays = make_overlay(occurrences, new_text)

    reader = PdfReader(input_pdf)
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        if i in overlays:
            overlay_reader = PdfReader(overlays[i])
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    print(f'Saved → {output_pdf}')
    return len(occurrences)


if __name__ == "__main__":
    if len(sys.argv) != 5:
        print("Usage: python scripts/replace_text.py <input.pdf> <output.pdf> <old_text> <new_text>")
        sys.exit(1)

    _, input_pdf, output_pdf, old_text, new_text = sys.argv

    if not os.path.isfile(input_pdf):
        print(f"Error: file not found: {input_pdf}")
        sys.exit(1)

    count = replace_text(input_pdf, output_pdf, old_text, new_text)
    sys.exit(0 if count > 0 else 1)