#!/usr/bin/env python3
"""
Replace text in a PDF by covering the old text with a white box
and drawing the new text on top at the same position.

Usage:
    python scripts/replace_text.py <input.pdf> <output.pdf> <old_text> <new_text> [--reflow]

Example:
    python scripts/replace_text.py input.pdf output.pdf "Something smart" "No pain no gain"
    python scripts/replace_text.py input.pdf output.pdf "Short" "Much longer replacement" --reflow

Notes:
- Matching is case-insensitive, word-level, spans consecutive words.
- Replaces all occurrences across all pages.
- Works on PDFs with selectable (non-scanned) text.
- Font color and size are sampled from the original text and preserved.
- Font style is approximated: bold+italic → Helvetica-BoldOblique, bold →
  Helvetica-Bold, italic → Helvetica-Oblique, plain → Helvetica.
- With --reflow: if the replacement text is taller than the original (wraps to
  more lines), content below is shifted down. If shorter, content below shifts
  up to close the gap.
- pdfplumber extract_words() on the output may show garbled text due to the
  white overlay — this is expected. The rendered PDF will look correct.
"""

import sys
import os
import io
import argparse
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


def _wrap_text(text: str, font: str, font_size: float, max_width: float) -> list:
    """Word-wrap text to fit within max_width. Returns list of lines."""
    # Manual word-wrap using reportlab's string width measurement
    from reportlab.pdfbase.pdfmetrics import stringWidth
    lines = []
    for paragraph in text.split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = words[0]
        for word in words[1:]:
            candidate = current + " " + word
            if stringWidth(candidate, font, font_size) <= max_width:
                current = candidate
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


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


def make_overlay(occurrences: list, new_text: str, reflow: bool = False) -> dict:
    """
    Build one in-memory overlay PDF per page. Returns {page_num: BytesIO}.

    When reflow=True and the replacement wraps to more/fewer lines than the
    original occupied, words below each occurrence are shifted to close or
    open the vertical gap.
    """
    by_page = {}
    for occ in occurrences:
        by_page.setdefault(occ["page"], []).append(occ)

    overlays = {}
    for pn, occs in by_page.items():
        ph = occs[0]["page_height"]
        pw = occs[0]["page_width"]

        # words_below for reflow are pre-populated in each occ dict by replace_text()
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(pw, ph))

        for occ in sorted(occs, key=lambda o: o["top"]):
            x0, top, x1, bottom = occ["x0"], occ["top"], occ["x1"], occ["bottom"]
            font_size = occ["font_size"]
            r, g, b = occ["color"]
            rl_font = occ["rl_font"]
            line_height = font_size * 1.2

            orig_height = bottom - top

            if reflow:
                max_width = x1 - x0
                lines = _wrap_text(new_text, rl_font, font_size, max_width)
            else:
                lines = [new_text]

            new_height = len(lines) * line_height
            height_delta = new_height - orig_height  # positive = grew, negative = shrank

            # Cover old text region
            pad = 3
            # Erase old text + enough vertical space for new text if it grew
            erase_bottom = max(bottom, top + new_height) + pad
            rl_erase_y = ph - erase_bottom
            c.setFillColorRGB(1, 1, 1)
            c.rect(x0 - pad, rl_erase_y,
                   (x1 - x0) + pad * 2, (erase_bottom - top) + pad,
                   fill=1, stroke=0)

            # Draw replacement lines
            c.setFillColorRGB(r, g, b)
            c.setFont(rl_font, font_size)
            y_cursor = ph - top - font_size  # RL baseline of first line
            for line in lines:
                c.drawString(x0, y_cursor, line)
                y_cursor -= line_height

            # Reflow: shift words below this occurrence
            if reflow and abs(height_delta) > 0.5 and "words_below" in occ:
                shift = height_delta  # positive = down, negative = up
                for word in occ["words_below"]:
                    wfont_size = word["bottom"] - word["top"]
                    new_top = word["top"] + shift
                    # Erase original position
                    c.setFillColorRGB(1, 1, 1)
                    c.rect(word["x0"] - 1, ph - word["bottom"] - 1,
                           (word["x1"] - word["x0"]) + 2, (word["bottom"] - word["top"]) + 2,
                           fill=1, stroke=0)
                    # Redraw at shifted position (warn if off-page)
                    if new_top + wfont_size > ph:
                        print(
                            f"  Warning: word '{word['text']}' shifted off page bottom "
                            f"(new_top={new_top:.0f}). Use reflow_page.py for multi-page reflow."
                        )
                        continue
                    c.setFillColorRGB(0, 0, 0)
                    c.setFont(rl_font, wfont_size)
                    c.drawString(word["x0"], ph - new_top - wfont_size, word["text"])

        c.save()
        buf.seek(0)
        overlays[pn] = buf

    return overlays


def replace_text(
    input_pdf: str,
    output_pdf: str,
    old_text: str,
    new_text: str,
    reflow: bool = False,
) -> int:
    occurrences = find_text_occurrences(input_pdf, old_text)

    if not occurrences:
        print(f'Text not found: "{old_text}"')
        return 0

    print(f'Found {len(occurrences)} occurrence(s) of "{old_text}"')

    # Attach words-below data for reflow
    if reflow:
        with pdfplumber.open(input_pdf) as pdf:
            for occ in occurrences:
                page = pdf.pages[occ["page"]]
                occ["words_below"] = [
                    w for w in page.extract_words()
                    if w["top"] >= occ["bottom"] - 1
                ]

    overlays = make_overlay(occurrences, new_text, reflow=reflow)

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
    parser = argparse.ArgumentParser(
        description="Replace text in a PDF, optionally reflowing content below."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("old_text")
    parser.add_argument("new_text")
    parser.add_argument(
        "--reflow", action="store_true",
        help="Shift content below each replacement up/down when height changes"
    )

    args = parser.parse_args()

    if not os.path.isfile(args.input_pdf):
        print(f"Error: file not found: {args.input_pdf}")
        sys.exit(1)

    count = replace_text(args.input_pdf, args.output_pdf, args.old_text, args.new_text,
                         reflow=args.reflow)
    sys.exit(0 if count > 0 else 1)
