#!/usr/bin/env python3
"""
Replace text in a PDF, preserving position, size, and color.

Two-step approach so BOTH the visible page and the underlying text layer end
up correct:
  1. The original glyphs of the matched text are removed from the content
     stream (so copy/paste and text extraction no longer return the old text
     or interleaved garbage).
  2. The new text is drawn as an overlay at the same position, in a standard
     Helvetica variant (bold/italic approximated) with the original color and
     size, auto-shrinking to fit the original width.

Usage:
    python scripts/replace_text.py <input.pdf> <output.pdf> <old_text> <new_text>

Example:
    python scripts/replace_text.py in.pdf out.pdf "Something smart" "No pain no gain"

Why Helvetica for the new text:
    Most PDFs embed *subset* fonts containing only the glyphs the document
    actually uses, so the original font usually cannot render new characters.
    Helvetica (a standard PDF font) renders any Latin text reliably. If exact
    font fidelity matters, render the result and review it visually.

Matching:
    Space-insensitive and case-insensitive. Many PDFs store text without real
    spaces between glyphs, so matching is done on the concatenated, space-
    stripped character sequence of each text line.
"""

import sys
import os
import io
import re
import argparse

import pdfplumber
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from pypdf import PdfReader, PdfWriter
import pypdf.generic as generic

from pdf_style_utils import rl_font_name, normalize_color


def _norm(s: str) -> str:
    """Lowercase and strip all whitespace — for space-insensitive matching."""
    return "".join(s.lower().split())


def find_text_occurrences(pdf_path: str, search_text: str):
    """
    Find every occurrence of search_text by matching the concatenated,
    space-stripped character sequence of each text line.

    Returns occurrence dicts with the precise bounding box of the matched
    glyphs plus sampled font size / color, and the exact matched substring
    text so the content-stream editor can locate and remove it.
    """
    target = _norm(search_text)
    if not target:
        return []

    results = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_num, page in enumerate(pdf.pages):
            chars = sorted(page.chars, key=lambda c: (round(c["top"], 1), c["x0"]))

            # Bucket characters into lines (within ~3pt vertically)
            lines = []
            current_line = []
            current_top = None
            for c in chars:
                if current_top is None or abs(c["top"] - current_top) <= 3:
                    current_line.append(c)
                    if current_top is None:
                        current_top = c["top"]
                else:
                    lines.append(current_line)
                    current_line = [c]
                    current_top = c["top"]
            if current_line:
                lines.append(current_line)

            for line_chars in lines:
                line_chars.sort(key=lambda c: c["x0"])
                norm_chars = [
                    (c["text"].lower(), idx)
                    for idx, c in enumerate(line_chars)
                    if c["text"].strip() != ""
                ]
                norm_str = "".join(nc[0] for nc in norm_chars)

                start = 0
                while True:
                    pos = norm_str.find(target, start)
                    if pos == -1:
                        break
                    match_indices = [norm_chars[pos + k][1] for k in range(len(target))]
                    matched = [line_chars[mi] for mi in match_indices]

                    x0 = min(m["x0"] for m in matched)
                    x1 = max(m["x1"] for m in matched)
                    top = min(m["top"] for m in matched)
                    bottom = max(m["bottom"] for m in matched)

                    sc = matched[0]
                    results.append({
                        "page": page_num,
                        "x0": x0, "top": top, "x1": x1, "bottom": bottom,
                        "page_height": page.height,
                        "page_width": page.width,
                        "font_size": sc["size"],
                        "color": normalize_color(sc.get("non_stroking_color")),
                        "rl_font": rl_font_name(sc.get("fontname", "")),
                        # the literal matched glyphs, in document order
                        "matched_text": "".join(line_chars[mi]["text"] for mi in match_indices),
                    })
                    start = pos + len(target)

    return results


# ---------------------------------------------------------------------------
# Content-stream editing: remove the original glyphs of the matched text
# ---------------------------------------------------------------------------

def _get_stream_bytes(page) -> bytes:
    contents = page.get("/Contents")
    if contents is None:
        return b""
    obj = contents.get_object()
    if isinstance(obj, generic.ArrayObject):
        return b" ".join(item.get_object().get_data() for item in obj)
    return obj.get_data()


def _set_stream_bytes(page, new_bytes: bytes) -> None:
    contents = page.get("/Contents")
    if contents is None:
        return
    obj = contents.get_object()
    if isinstance(obj, generic.ArrayObject):
        first = obj[0].get_object()
        first.set_data(new_bytes)
        if "/Filter" in first:
            del first["/Filter"]
        # Blank out remaining stream parts so they don't duplicate content
        for item in obj[1:]:
            so = item.get_object()
            so.set_data(b"")
            if "/Filter" in so:
                del so["/Filter"]
    else:
        obj.set_data(new_bytes)
        if "/Filter" in obj:
            del obj["/Filter"]


def _strip_chars_from_tj(stream: bytes, matched_text: str) -> bytes:
    """
    Remove ONLY the glyphs belonging to matched_text from TJ/Tj arrays,
    leaving the rest of each array (other words sharing the same array) intact.

    Approach: within a TJ array whose letters contain the target, walk each
    ( ) string literal, track a running letter offset, and empty only those
    literals whose letters fall inside the matched span. Literals that are
    partially inside the span are split at character granularity.
    """
    text = stream.decode("latin-1")
    target = _norm(matched_text)
    if not target:
        return stream

    tj_array = re.compile(r"\[(.*?)\]\s*TJ", re.DOTALL)
    lit_pat = re.compile(r"\((?:[^()\\]|\\.)*\)")
    # Tokenize a literal's inner bytes into glyph units (escapes count as one char)
    glyph_pat = re.compile(r"\\[0-7]{1,3}|\\.|[^\\]")

    def lit_letters(lit_inner: str):
        """Return list of (glyph_token, normalized_letter_or_empty)."""
        units = glyph_pat.findall(lit_inner)
        out = []
        for u in units:
            if u.startswith("\\") and re.match(r"\\[0-7]{1,3}", u):
                letter = ""  # octal-coded non-ascii glyph → not a match letter
            elif u.startswith("\\"):
                letter = ""  # escape like \( \) \\ — not a counted letter
            else:
                letter = _norm(u)
            out.append((u, letter))
        return out

    def array_letters(segment: str) -> str:
        out = []
        for m in lit_pat.finditer(segment):
            for _, letter in lit_letters(m.group(0)[1:-1]):
                out.append(letter)
        return "".join(out)

    def repl(m):
        segment = m.group(1)
        letters = array_letters(segment)
        span_start = letters.find(target)
        if span_start == -1:
            return m.group(0)
        span_end = span_start + len(target)  # [start, end) in letter index

        # Rebuild the array, blanking glyphs whose letter-index is in the span
        rebuilt = []
        last = 0
        letter_idx = 0
        for lm in lit_pat.finditer(segment):
            rebuilt.append(segment[last:lm.start()])  # kerning numbers, whitespace
            inner = lm.group(0)[1:-1]
            kept = []
            for token, letter in lit_letters(inner):
                if letter:
                    if not (span_start <= letter_idx < span_end):
                        kept.append(token)
                    letter_idx += 1
                else:
                    # Non-letter glyph (punctuation/space code): keep unless it
                    # sits strictly inside the matched span
                    if not (span_start <= letter_idx < span_end):
                        kept.append(token)
            rebuilt.append("(" + "".join(kept) + ")")
            last = lm.end()
        rebuilt.append(segment[last:])
        return "[" + "".join(rebuilt) + "] TJ"

    return tj_array.sub(repl, text).encode("latin-1")


# ---------------------------------------------------------------------------
# Overlay: draw the new text
# ---------------------------------------------------------------------------

def _fit_font_size(text: str, font: str, base_size: float, max_width: float) -> float:
    if max_width <= 0:
        return base_size
    w = stringWidth(text, font, base_size)
    if w <= max_width:
        return base_size
    return base_size * (max_width / w)


def make_overlay(occurrences: list, new_text: str) -> dict:
    by_page = {}
    for occ in occurrences:
        by_page.setdefault(occ["page"], []).append(occ)

    overlays = {}
    for pn, occs in by_page.items():
        ph = occs[0]["page_height"]
        pw = occs[0]["page_width"]
        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(pw, ph))

        for occ in occs:
            x0, top, x1, bottom = occ["x0"], occ["top"], occ["x1"], occ["bottom"]
            r, g, b = occ["color"]
            rl_font = occ["rl_font"]
            orig_width = x1 - x0
            rl_y_bottom = ph - bottom
            text_h = bottom - top

            # White cover (belt-and-suspenders; stream glyphs are already removed)
            pad = 1.5
            c.setFillColorRGB(1, 1, 1)
            c.rect(x0 - pad, rl_y_bottom - pad,
                   orig_width + pad * 2, text_h + pad * 2,
                   fill=1, stroke=0)

            draw_size = _fit_font_size(new_text, rl_font, occ["font_size"], orig_width)
            c.setFillColorRGB(r, g, b)
            c.setFont(rl_font, draw_size)
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

    # Which pages need their text layer cleaned, and with what matched strings
    matched_by_page = {}
    for occ in occurrences:
        matched_by_page.setdefault(occ["page"], []).append(occ["matched_text"])

    reader = PdfReader(input_pdf)
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        if i in matched_by_page:
            raw = _get_stream_bytes(page)
            for matched in matched_by_page[i]:
                raw = _strip_chars_from_tj(raw, matched)
            _set_stream_bytes(page, raw)
        if i in overlays:
            overlay_reader = PdfReader(overlays[i])
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    print(f"Saved → {output_pdf}")
    return len(occurrences)


def main():
    parser = argparse.ArgumentParser(
        description="Replace text in a PDF, preserving position, size, and color."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("old_text")
    parser.add_argument("new_text")

    args = parser.parse_args()

    if not os.path.isfile(args.input_pdf):
        print(f"Error: file not found: {args.input_pdf}")
        sys.exit(1)

    count = replace_text(args.input_pdf, args.output_pdf, args.old_text, args.new_text)
    sys.exit(0 if count > 0 else 1)


if __name__ == "__main__":
    main()
