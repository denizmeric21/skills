#!/usr/bin/env python3
"""
Change the visual style of existing selectable PDF text.

This is a convenience wrapper around replace_text.py for requests like:
  - make this text bigger
  - make this text smaller
  - change this text to red
  - make this phrase Helvetica-Bold

It keeps the text content the same, erases the original visual text, and draws
the same text with explicit style overrides. Existing content below can reflow
when the styled text grows enough to need more room.

Usage:
    python scripts/change_text_style.py in.pdf out.pdf "Some text" \
        --scale 1.2 --color "#7A1F12" --cover line
"""

import argparse
import os
import sys

from pdf_style_utils import parse_color
from replace_text import replace_text


def change_text_style(
    input_pdf: str,
    output_pdf: str,
    text: str,
    font: str | None = None,
    font_size: float | None = None,
    scale: float | None = None,
    color: tuple | None = None,
    mode: str = "fit",
    cover: str = "auto",
    shrink_to_fit: bool = False,
) -> int:
    return replace_text(
        input_pdf=input_pdf,
        output_pdf=output_pdf,
        old_text=text,
        new_text=text,
        mode=mode,
        cover=cover,
        font=font,
        font_size=font_size,
        scale=scale,
        color=color,
        shrink_to_fit=shrink_to_fit,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Change font, size, or color of existing selectable PDF text."
    )
    parser.add_argument("input_pdf")
    parser.add_argument("output_pdf")
    parser.add_argument("text")
    parser.add_argument("--font", default=None,
                        help="ReportLab/PDF font, e.g. Helvetica-Bold or Times-Italic")
    parser.add_argument("--font-size", type=float, default=None,
                        help="Absolute font size")
    parser.add_argument("--scale", type=float, default=None,
                        help="Scale sampled font size, e.g. 1.2 or 0.8")
    parser.add_argument("--color", default=None,
                        help="Color as name, #RRGGBB, RRGGBB, or r,g,b")
    parser.add_argument("--mode", choices=("auto", "fit", "reflow"), default="fit",
                        help="Use reflow when larger text should move lower sections")
    parser.add_argument("--cover", choices=("auto", "span", "line"), default="auto",
                        help="Visual erase region before drawing styled text")
    parser.add_argument("--shrink-to-fit", action="store_true",
                        help="Shrink styled text if it exceeds the old span width")

    args = parser.parse_args()

    if not os.path.isfile(args.input_pdf):
        print(f"Error: file not found: {args.input_pdf}")
        sys.exit(1)

    if args.font_size is None and args.scale is None and args.color is None and args.font is None:
        print("Error: provide at least one of --font, --font-size, --scale, or --color")
        sys.exit(1)

    try:
        color = parse_color(args.color) if args.color is not None else None
    except Exception as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    count = change_text_style(
        input_pdf=args.input_pdf,
        output_pdf=args.output_pdf,
        text=args.text,
        font=args.font,
        font_size=args.font_size,
        scale=args.scale,
        color=color,
        mode=args.mode,
        cover=args.cover,
        shrink_to_fit=args.shrink_to_fit,
    )
    sys.exit(0 if count else 1)


if __name__ == "__main__":
    main()
