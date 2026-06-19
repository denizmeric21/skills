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

    # Find each BT...ET block
    bt_pattern = re.compile(r"\bBT\b")
    et_pattern = re.compile(r"\bET\b")

    # Absolute position: "x y Td" or "a b c d x y Tm"
    abs_td = re.compile(
        r"([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+Td"
    )
    abs_tm = re.compile(
        r"([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+"
        r"([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+([-+]?\d*\.?\d+)\s+Tm"
    )

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

        block = text[bt_start: et_m.end()]
        block_inner = text[bt_m.end(): et_m.start()]

        # Find the first absolute position inside this block
        first_abs = None
        tm_m = abs_tm.search(block_inner)
        td_m = abs_td.search(block_inner)

        # Pick whichever comes first
        if tm_m and (td_m is None or tm_m.start() < td_m.start()):
            first_abs = ("tm", tm_m)
        elif td_m:
            first_abs = ("td", td_m)

        if first_abs:
            kind, m = first_abs
            if kind == "tm":
                pdf_y = float(m.group(6))
            else:
                pdf_y = float(m.group(2))

            if pdf_y < threshold_pdf_y:
                # This block is below the cut line — shift it
                new_pdf_y = pdf_y + pdf_shift

                if kind == "tm":
                    new_op = (
                        f"{m.group(1)} {m.group(2)} {m.group(3)} "
                        f"{m.group(4)} {m.group(5)} {new_pdf_y:.4f} Tm"
                    )
                    block_inner = block_inner[: m.start()] + new_op + block_inner[m.end():]
                else:
                    new_op = f"{m.group(1)} {new_pdf_y:.4f} Td"
                    block_inner = block_inner[: m.start()] + new_op + block_inner[m.end():]

                block = text[bt_start: bt_m.end()] + block_inner + text[et_m.start(): et_m.end()]

        result.append(block)
        pos = et_m.end()

    return "".join(result).encode("latin-1")


def _set_stream_bytes(page, new_bytes: bytes) -> None:
    """Replace the page content stream with new_bytes (uncompressed)."""
    from pypdf.generic import DecodedStreamObject, NameObject, ByteStringObject
    contents = page.get("/Contents")
    if contents is None:
        return
    obj = contents.get_object()

    # Build a new uncompressed stream object
    new_stream = DecodedStreamObject()
    new_stream.set_data(new_bytes)
    # Remove any existing filter so pypdf doesn't try to decompress again
    if "/Filter" in new_stream:
        del new_stream["/Filter"]

    if isinstance(obj, generic.ArrayObject):
        # Collapse multi-stream to single for simplicity
        ref = contents  # IndirectObject
        ref.get_object().clear()
        # Replace in-place: write to the first stream, drop the rest
        first = obj[0].get_object()
        first.set_data(new_bytes)
        if "/Filter" in first:
            del first["/Filter"]
    else:
        obj.set_data(new_bytes)
        if "/Filter" in obj:
            del obj["/Filter"]


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
