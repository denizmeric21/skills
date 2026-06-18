#!/usr/bin/env python3
"""
Replace text in a PDF by covering the old text with a white box
and drawing the new text on top at the same position.

Usage:
    python scripts/replace_text.py <input.pdf> <output.pdf> <old_text> <new_text>

Example:
    python scripts/replace_text.py input.pdf output.pdf "No pain" "No gain"

Notes:
- Matches are case-sensitive.
- Replaces all occurrences across all pages.
- Works best on PDFs with selectable (non-scanned) text.
- Font is matched approximately (Helvetica). Size is estimated from word height.
- The replacement text color is black; background cover is white.
"""

import sys
import os
import pdfplumber
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.lib.pagesizes import letter
from pypdf import PdfReader, PdfWriter
import io


def find_text_occurrences(pdf_path: str, search_text: str):
    """
    Return a list of {page, x0, top, x1, bottom} dicts for every
    occurrence of search_text (word-level, joined by space).
    """
    results = []
    search_lower = search_text.lower()
    search_words = search_lower.split()
    n = len(search_words)

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            words = page.extract_words()
            word_texts = [w["text"].lower() for w in words]

            for i in range(len(words) - n + 1):
                if [w.lower() for w in word_texts[i:i+n]] == search_words:
                    # Bounding box spanning all matched words
                    match_words = words[i:i+n]
                    x0 = min(w["x0"] for w in match_words)
                    x1 = max(w["x1"] for w in match_words)
                    top = min(w["top"] for w in match_words)
                    bottom = max(w["bottom"] for w in match_words)
                    results.append({
                        "page": page_num,
                        "x0": x0,
                        "top": top,
                        "x1": x1,
                        "bottom": bottom,
                        "page_height": page.height,
                        "page_width": page.width,
                    })

    return results


def make_overlay(occurrences: list, new_text: str) -> dict:
    """
    Build one in-memory overlay PDF per page that contains matches.
    Returns {page_num: bytes}.
    """
    pages = {}
    for occ in occurrences:
        pn = occ["page"]
        if pn not in pages:
            pages[pn] = []
        pages[pn].append(occ)

    overlays = {}
    for pn, occs in pages.items():
        buf = io.BytesIO()
        ph = occs[0]["page_height"]
        pw = occs[0]["page_width"]
        c = rl_canvas.Canvas(buf, pagesize=(pw, ph))

        for occ in occs:
            x0 = occ["x0"]
            top = occ["top"]
            x1 = occ["x1"]
            bottom = occ["bottom"]
            text_height = bottom - top

            # pdfplumber y=0 is top; reportlab y=0 is bottom
            rl_bottom = ph - bottom
            rl_top = ph - top

            # White rectangle to cover old text (with small padding)
            pad = 2
            c.setFillColorRGB(1, 1, 1)
            c.rect(x0 - pad, rl_bottom - pad,
                   (x1 - x0) + pad * 2, text_height + pad * 2,
                   fill=1, stroke=0)

            # Draw new text
            font_size = max(6, text_height * 0.85)
            c.setFillColorRGB(0, 0, 0)
            c.setFont("Helvetica", font_size)
            c.drawString(x0, rl_bottom + 1, new_text)

        c.save()
        buf.seek(0)
        overlays[pn] = buf

    return overlays


def replace_text(input_pdf: str, output_pdf: str, old_text: str, new_text: str):
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
    if count == 0:
        sys.exit(1)
