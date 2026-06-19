#!/usr/bin/env python3
"""
Reflow page content: shift all text below a given y-position up or down by
modifying the PDF content stream directly — so original embedded fonts,
colors, and spacing are perfectly preserved.

Usage:
    python scripts/reflow_page.py <input.pdf> <output.pdf> \\
        --page <N> --below-y <y> --shift <delta>

Arguments:
    --page     1-based page number
    --below-y  Shift text blocks whose absolute y < (page_height - below_y).
               Uses pdfplumber convention: y=0 at page top.
    --shift    Points to shift: positive=down (increases pdfplumber y),
               negative=up.

How it works:
    Parses BT...ET blocks in the PDF content stream. For each block whose
    first absolute position falls in the affected zone, adjusts the y
    coordinate of the absolute Td/Tm operator. No text is erased or redrawn —
    the original font subsets and glyph encodings are untouched.

Example:
    python scripts/reflow_page.py in.pdf out.pdf --page 1 --below-y 400 --shift 30
    python scripts/reflow_page.py in.pdf out.pdf --page 1 --below-y 300 --shift -20
"""

import argparse
import io
import os
import re
import sys

from pypdf import PdfReader, PdfWriter
import pypdf.generic as generic


# ---------------------------------------------------------------------------
# Content stream helpers
# ---------------------------------------------------------------------------

def _get_stream_bytes(page) -> bytes:
    """Extract the raw (decompressed) content stream bytes from a page."""
    contents = page.get("/Contents")
    if contents is None:
        return b""
    obj = contents.get_object()
    if isinstance(obj, generic.ArrayObject):
        return b" ".join(item.get_object().get_data() for item in obj)
    return obj.get_data()


_NUM = r"[-+]?(?:\d+\.\d+|\d+|\.\d+)"


def _read_pdf_string(text: str, start: int) -> int:
    """Return the end offset for a PDF literal string starting at ``start``."""
    depth = 1
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _read_pdf_array(text: str, start: int) -> int:
    """Return the end offset for a PDF array, skipping strings inside it."""
    depth = 1
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == "(":
            i = _read_pdf_string(text, i)
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(text)


def _iter_content_tokens(text: str):
    """
    Yield non-whitespace PDF content tokens with source offsets.

    This deliberately stays small: it is not a full PDF parser, but it safely
    skips literal strings and TJ arrays so operator-like text inside strings is
    not mistaken for an operator.
    """
    delimiters = set("()<>[]{}/%")
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "%":
            end = text.find("\n", i)
            i = len(text) if end == -1 else end + 1
            continue
        if ch == "(":
            end = _read_pdf_string(text, i)
            yield {"text": text[i:end], "start": i, "end": end, "kind": "string"}
            i = end
            continue
        if ch == "[":
            end = _read_pdf_array(text, i)
            yield {"text": text[i:end], "start": i, "end": end, "kind": "array"}
            i = end
            continue
        if ch == "/":
            j = i + 1
            while j < len(text) and (not text[j].isspace()) and text[j] not in delimiters:
                j += 1
            yield {"text": text[i:j], "start": i, "end": j, "kind": "name"}
            i = j
            continue

        j = i
        while j < len(text) and (not text[j].isspace()) and text[j] not in delimiters:
            j += 1
        if j == i:
            j += 1
        token = text[i:j]
        kind = "number" if re.fullmatch(_NUM, token) else "word"
        yield {"text": token, "start": i, "end": j, "kind": kind}
        i = j


def _num(token) -> float | None:
    try:
        return float(token["text"])
    except Exception:
        return None


def _shift_text_shows_in_block(
    block_inner: str,
    threshold_pdf_y: float,
    pdf_shift: float,
    initial_leading: float = 0.0,
    initial_rise: float = 0.0,
) -> tuple[str, float, float]:
    """
    Shift individual text-show operations below the threshold using text rise.

    The old implementation moved only the first absolute Td/Tm in a BT...ET
    block. That misses a very common layout shape where one text object draws a
    heading and several following lines via T*. In that case inserting under
    the heading left the later lines in place and the new text overprinted them.

    Text rise (Ts) moves glyph rendering without changing the text matrix, so it
    is safe around Tj/TJ/'/" shows and does not disturb horizontal advances.
    """
    tokens = list(_iter_content_tokens(block_inner))
    if not tokens:
        return block_inner, initial_leading, initial_rise

    insertions: list[tuple[int, str]] = []
    stack = []
    current_y: float | None = None
    leading = initial_leading
    rise = initial_rise

    def show_text(op, operand_count: int, advance_line: bool = False) -> None:
        nonlocal current_y
        if advance_line and current_y is not None:
            current_y -= leading

        if current_y is None or current_y >= threshold_pdf_y:
            return

        if len(stack) >= operand_count:
            start = stack[-operand_count]["start"]
        else:
            start = op["start"]

        shifted_rise = rise + pdf_shift
        insertions.append((start, f" {shifted_rise:.4f} Ts "))
        insertions.append((op["end"], f" {rise:.4f} Ts "))

    for token in tokens:
        text = token["text"]

        if text == "Tm" and len(stack) >= 6:
            y = _num(stack[-1])
            if y is not None:
                current_y = y
            stack = []
        elif text in ("Td", "TD") and len(stack) >= 2:
            ty = _num(stack[-1])
            if ty is not None:
                current_y = ty if current_y is None else current_y + ty
                if text == "TD":
                    leading = -ty
            stack = []
        elif text == "TL" and stack:
            value = _num(stack[-1])
            if value is not None:
                leading = value
            stack = []
        elif text == "Ts" and stack:
            value = _num(stack[-1])
            if value is not None:
                rise = value
            stack = []
        elif text == "T*":
            if current_y is not None:
                current_y -= leading
            stack = []
        elif text in ("Tj", "TJ"):
            show_text(token, 1)
            stack = []
        elif text == "'":
            show_text(token, 1, advance_line=True)
            stack = []
        elif text == '"':
            show_text(token, 3, advance_line=True)
            stack = []
        elif token["kind"] in ("number", "string", "array", "name"):
            stack.append(token)
        else:
            stack = []

    if not insertions:
        return block_inner, leading, rise

    insertions.sort(key=lambda item: item[0])
    out = []
    last = 0
    for pos, snippet in insertions:
        out.append(block_inner[last:pos])
        out.append(snippet)
        last = pos
    out.append(block_inner[last:])
    return "".join(out), leading, rise


def _shift_stream(stream: bytes, page_height: float, below_plumb_y: float, shift_pts: float) -> bytes:
    """
    Modify y-coordinates in BT...ET blocks that fall below *below_plumb_y*
    (pdfplumber convention, y=0 at top).

    PDF y=0 is at the bottom, so:
      pdf_y  = page_height - plumb_y
      affected blocks have pdf_y  < page_height - below_plumb_y
      i.e. the block starts below the threshold line.

    shift_pts > 0 means shift DOWN in pdfplumber terms → DECREASE pdf_y.
    shift_pts < 0 means shift UP   in pdfplumber terms → INCREASE pdf_y.

    We adjust only the *first* absolute Td or Tm in each BT...ET block
    (subsequent Td are relative moves within the block, which are correct).
    """
    threshold_pdf_y = page_height - below_plumb_y
    pdf_shift = -shift_pts  # pdfplumber down = pdf y decrease

    text = stream.decode("latin-1")
    result = []
    pos = 0
    leading = 0.0
    rise = 0.0

    bt_pattern = re.compile(r"\bBT\b")
    et_pattern = re.compile(r"\bET\b")

    while pos < len(text):
        bt_m = bt_pattern.search(text, pos)
        if bt_m is None:
            result.append(text[pos:])
            break

        # Everything before BT passes through unchanged
        result.append(text[pos:bt_m.start()])
        bt_start = bt_m.start()

        et_m = et_pattern.search(text, bt_m.end())
        if et_m is None:
            result.append(text[bt_start:])
            pos = len(text)
            break

        block_inner = text[bt_m.end(): et_m.start()]
        shifted_inner, leading, rise = _shift_text_shows_in_block(
            block_inner,
            threshold_pdf_y,
            pdf_shift,
            initial_leading=leading,
            initial_rise=rise,
        )
        block = text[bt_start: bt_m.end()] + shifted_inner + text[et_m.start(): et_m.end()]

        result.append(block)
        pos = et_m.end()

    return "".join(result).encode("latin-1")


def _set_stream_bytes(page, new_bytes: bytes) -> None:
    """Replace the page content stream with new_bytes (uncompressed)."""
    from pypdf.generic import DecodedStreamObject, NameObject

    contents = page.get("/Contents")
    if contents is None:
        return

    new_stream = DecodedStreamObject()
    new_stream.set_data(new_bytes)
    page[NameObject("/Contents")] = new_stream


# ---------------------------------------------------------------------------
# Also shift non-text graphics that use cm (coordinate transform) operators
# ---------------------------------------------------------------------------

def _shift_cm_blocks(stream: bytes, page_height: float, below_plumb_y: float, shift_pts: float) -> bytes:
    """
    Shift standalone graphics positioned with  `1 0 0 1 tx ty cm`  that fall
    in the affected zone (e.g. images, decorative rules placed via cm).
    """
    threshold_pdf_y = page_height - below_plumb_y
    pdf_shift = -shift_pts

    text = stream.decode("latin-1")

    # Match identity-scale translate: "1 0 0 1 tx ty cm"
    cm_pat = re.compile(
        r"\b1\s+0\s+0\s+1\s+([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+cm\b"
    )

    def replace_cm(m):
        tx = float(m.group(1))
        ty = float(m.group(2))
        if abs(tx) < 0.0001 and abs(ty) < 0.0001:
            return m.group(0)
        if ty < threshold_pdf_y:
            ty += pdf_shift
        return f"1 0 0 1 {tx:.4f} {ty:.4f} cm"

    return cm_pat.sub(replace_cm, text).encode("latin-1")


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def reflow_page(
    input_pdf: str,
    output_pdf: str,
    page_num: int,
    below_y: float,
    shift: float,
) -> None:
    reader = PdfReader(input_pdf)
    total_pages = len(reader.pages)

    if page_num < 1 or page_num > total_pages:
        print(f"Error: page {page_num} out of range (1–{total_pages})")
        sys.exit(1)

    page_idx = page_num - 1
    page = reader.pages[page_idx]
    mb = page.mediabox
    pw, ph = float(mb.width), float(mb.height)

    raw = _get_stream_bytes(page)
    modified = _shift_stream(raw, ph, below_y, shift)
    modified = _shift_cm_blocks(modified, ph, below_y, shift)

    _set_stream_bytes(page, modified)

    writer = PdfWriter()
    for i, p in enumerate(reader.pages):
        writer.add_page(p)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    direction = "down" if shift > 0 else "up"
    print(f"Shifted content below y={below_y:.0f} {direction} by {abs(shift):.0f}pt on page {page_num}")
    print(f"Saved → {output_pdf}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Shift PDF content below a y-position by editing the content stream directly."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, required=True)
    parser.add_argument("--below-y", type=float, required=True,
                        help="Shift content whose top >= this y (pdfplumber, y=0 at top)")
    parser.add_argument("--shift", type=float, required=True,
                        help="Points to shift: positive=down, negative=up")

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
    )


if __name__ == "__main__":
    main()
