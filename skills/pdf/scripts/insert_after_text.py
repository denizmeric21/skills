#!/usr/bin/env python3
"""
Insert a text block immediately after matched text in a PDF.

Use this when the user says "add this under/after <section>" instead of
replacing the section heading with heading-plus-new-body text. The script finds
the anchor text, inserts the new block below it, and shifts existing content
down to make room.

Usage:
    python scripts/insert_after_text.py <input.pdf> <output.pdf> \
        <anchor_text> --text "New paragraph here"

Options let you choose a page, occurrence, gap, x/width, and explicit style.
Coordinates and matching are handled with the same pdfplumber conventions as
replace_text.py and add_text_block.py.
"""

import argparse
import os
import sys

import pdfplumber

from add_text_block import add_text_block
from pdf_style_utils import dominant_text_style, is_bold_font
from replace_text import find_text_occurrences


def _bucket_lines(chars: list[dict], tolerance: float = 3.0) -> list[list[dict]]:
    lines: list[list[dict]] = []
    current: list[dict] = []
    current_top = None

    for char in sorted(chars, key=lambda c: (round(c["top"], 1), c["x0"])):
        if current_top is None or abs(char["top"] - current_top) <= tolerance:
            current.append(char)
            if current_top is None:
                current_top = char["top"]
        else:
            current.sort(key=lambda c: c["x0"])
            lines.append(current)
            current = [char]
            current_top = char["top"]

    if current:
        current.sort(key=lambda c: c["x0"])
        lines.append(current)

    return lines


def _line_text(chars: list[dict]) -> str:
    out = []
    prev = None
    for char in chars:
        if prev is not None:
            gap = char["x0"] - prev["x1"]
            space_width = max(prev.get("size", 10.0), char.get("size", 10.0)) * 0.28
            if gap > space_width:
                out.append(" ")
        text = char["text"]
        out.append("•" if text.startswith("(cid:") else text)
        prev = char
    return "".join(out).strip()


def _list_prefix(text: str) -> str:
    stripped = text.lstrip()
    for marker in ("•", "-", "*", "–", "—"):
        if stripped.startswith(marker + " "):
            return marker + " "
    return ""


def _add_prefix_if_needed(text: str, prefix: str) -> str:
    if not prefix:
        return text
    out = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if not stripped:
            out.append(line)
        elif _list_prefix(stripped):
            out.append(line)
        else:
            out.append(prefix + stripped)
    return "\n".join(out)


def _sample_body_style_below(
    input_pdf: str,
    page_idx: int,
    y_min: float,
    anchor_font_size: float,
) -> dict | None:
    """Sample the dominant body style below y_min on the same page."""
    with pdfplumber.open(input_pdf) as pdf:
        page = pdf.pages[page_idx]
        chars = [c for c in page.chars if c["top"] >= y_min]
        line_infos = []
        for line in _bucket_lines(chars):
            text = _line_text(line)
            if not text:
                continue
            style = dominant_text_style(line)
            line_infos.append({
                "text": text,
                "x": min(c["x0"] for c in line),
                "x1": max(c["x1"] for c in line),
                "top": min(c["top"] for c in line),
                "bottom": max(c["bottom"] for c in line),
                "bold": any(is_bold_font(c.get("fontname", "")) for c in line if c["text"].strip()),
                **style,
            })

        if not line_infos:
            return None

        candidates = [
            line for line in line_infos
            if line["font_size"] <= anchor_font_size * 0.98
        ] or line_infos

        non_bold = [line for line in candidates if not line["bold"]]
        if non_bold:
            candidates = non_bold

        def key(line):
            color_key = tuple(round(v, 3) for v in line["color"])
            return (round(line["font_size"], 1), line["font"], color_key)

        common_key = max(
            {key(line) for line in candidates},
            key=lambda item: sum(1 for line in candidates if key(line) == item),
        )
        styled_lines = [line for line in candidates if key(line) == common_key]
        sample = styled_lines[0]

        top_diffs = [
            b["top"] - a["top"]
            for a, b in zip(styled_lines, styled_lines[1:])
            if 0 < b["top"] - a["top"] < sample["font_size"] * 3
        ]
        line_height = (
            sorted(top_diffs)[len(top_diffs) // 2]
            if top_diffs else sample["font_size"] * 1.2
        )

        return {
            "x": sample["x"],
            "font": sample["font"],
            "font_size": sample["font_size"],
            "line_height": line_height,
            "color": sample["color"],
            "prefix": _list_prefix(sample["text"]),
            "width": page.width - sample["x"] - 50,
        }
    return None


def _anchor_style(anchor: dict) -> dict:
    return {
        "x": float(anchor["x0"]),
        "font": anchor["rl_font"],
        "font_size": float(anchor["font_size"]),
        "color": anchor["color"],
    }


def insert_after_text(
    input_pdf: str,
    output_pdf: str,
    anchor_text: str,
    text: str,
    page_num: int | None = None,
    occurrence: int = 1,
    gap: float = 4.0,
    x: float | None = None,
    width: float | None = None,
    font: str | None = None,
    font_size: float | None = None,
    line_height: float | None = None,
    color: tuple | None = None,
    style_source: str = "below",
    match_list_prefix: bool = True,
) -> int:
    matches = find_text_occurrences(input_pdf, anchor_text)
    if page_num is not None:
        matches = [m for m in matches if m["page"] == page_num - 1]

    if not matches:
        page_msg = "" if page_num is None else f" on page {page_num}"
        print(f'Text not found{page_msg}: "{anchor_text}"')
        return 0

    matches.sort(key=lambda m: (m["page"], m["top"], m["x0"]))
    if occurrence < 1 or occurrence > len(matches):
        print(f"Error: occurrence {occurrence} out of range (1-{len(matches)})")
        return 0

    anchor = matches[occurrence - 1]
    insert_y = float(anchor["bottom"]) + gap

    sampled = None
    if style_source == "below":
        sampled = _sample_body_style_below(
            input_pdf,
            anchor["page"],
            insert_y,
            anchor_font_size=float(anchor["font_size"]),
        )

    style = sampled or _anchor_style(anchor)
    insert_text = text
    if match_list_prefix:
        insert_text = _add_prefix_if_needed(insert_text, style.get("prefix", ""))

    add_text_block(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        page_num=anchor["page"] + 1,
        insert_y=insert_y,
        text=insert_text,
        insert_x=x if x is not None else style["x"],
        block_width=width if width is not None else style.get("width"),
        font=font or style["font"],
        font_size=font_size if font_size is not None else style["font_size"],
        line_height=line_height if line_height is not None else style.get("line_height"),
        color=color or style["color"],
    )

    print(
        f'Inserted text after occurrence {occurrence} of "{anchor_text}" '
        f'on page {anchor["page"] + 1}'
    )
    return 1


def main():
    parser = argparse.ArgumentParser(
        description="Insert text below matched anchor text and reflow content below it."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("anchor_text")
    parser.add_argument("--text", required=True, help="Text to insert (use \\n for newlines)")
    parser.add_argument("--page", type=int, default=None, help="Limit anchor search to this 1-based page")
    parser.add_argument("--occurrence", type=int, default=1, help="1-based occurrence among matches")
    parser.add_argument("--gap", type=float, default=4.0, help="Gap below anchor text in points")
    parser.add_argument("--x", type=float, default=None, help="Override left x-position")
    parser.add_argument("--width", type=float, default=None, help="Override text block width")
    parser.add_argument("--font", default=None, help="Override ReportLab font")
    parser.add_argument("--font-size", type=float, default=None, help="Override font size")
    parser.add_argument("--line-height", type=float, default=None, help="Override line height")
    parser.add_argument("--color", default=None, help="Override color as r,g,b floats 0-1")
    parser.add_argument("--no-match-list-prefix", action="store_true",
                        help="Do not copy the bullet/dash prefix from surrounding body text")
    parser.add_argument(
        "--style-source",
        choices=("below", "anchor"),
        default="below",
        help="Sample style from body text below the anchor, or from the anchor itself",
    )

    args = parser.parse_args()

    if not os.path.isfile(args.input_pdf):
        print(f"Error: file not found: {args.input_pdf}")
        sys.exit(1)

    color = None
    if args.color is not None:
        try:
            color = tuple(float(v) for v in args.color.split(","))
            assert len(color) == 3
        except Exception:
            print("Error: --color must be three comma-separated floats, e.g. 0,0,0")
            sys.exit(1)

    count = insert_after_text(
        input_pdf=args.input_pdf,
        output_pdf=args.output_pdf,
        anchor_text=args.anchor_text,
        text=args.text.replace("\\n", "\n"),
        page_num=args.page,
        occurrence=args.occurrence,
        gap=args.gap,
        x=args.x,
        width=args.width,
        font=args.font,
        font_size=args.font_size,
        line_height=args.line_height,
        color=color,
        style_source=args.style_source,
        match_list_prefix=not args.no_match_list_prefix,
    )
    sys.exit(0 if count else 1)


if __name__ == "__main__":
    main()
