#!/usr/bin/env python3
"""
Layout-preserving PDF page composition helpers.

These helpers move page sections by copying cropped bands from the original
page as PDF vector content. That preserves existing fonts, colors, images,
rules, shapes, and spacing far better than editing only text operators.

Coordinates use pdfplumber convention: y=0 at the top of the page.
"""

from copy import copy

from pypdf import PageObject


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def merge_page_band(
    target: PageObject,
    source,
    page_width: float,
    page_height: float,
    y_top: float,
    y_bottom: float,
    shift_y: float = 0.0,
) -> None:
    """
    Merge a cropped source band into target.

    y_top/y_bottom are pdfplumber y coordinates in the source. shift_y is also
    in pdfplumber coordinates: positive moves the band down, negative moves it
    up.
    """
    y_top = _clamp(y_top, 0.0, page_height)
    y_bottom = _clamp(y_bottom, 0.0, page_height)
    if y_bottom <= y_top:
        return

    band = copy(source)
    pdf_low = page_height - y_bottom
    pdf_high = page_height - y_top
    band.cropbox.lower_left = (0, pdf_low)
    band.cropbox.upper_right = (page_width, pdf_high)

    # PDF y grows upward; pdfplumber y grows downward.
    target.merge_translated_page(band, 0, -shift_y, expand=False)


def continuation_pages_from_band(
    source,
    page_width: float,
    page_height: float,
    overflow_start: float,
    top_margin: float = 50.0,
    bottom_margin: float = 50.0,
) -> list[PageObject]:
    """Carry source content from overflow_start onward to continuation pages."""
    overflow_start = _clamp(overflow_start, 0.0, page_height)
    if overflow_start >= page_height:
        return []

    usable_height = page_height - top_margin - bottom_margin
    if usable_height <= 0:
        raise ValueError("top_margin + bottom_margin must be less than page height")

    pages: list[PageObject] = []
    band_top = overflow_start
    while band_top < page_height:
        band_bottom = min(page_height, band_top + usable_height)
        page = PageObject.create_blank_page(width=page_width, height=page_height)
        merge_page_band(
            page,
            source,
            page_width,
            page_height,
            y_top=band_top,
            y_bottom=band_bottom,
            shift_y=top_margin - band_top,
        )
        pages.append(page)
        band_top = band_bottom

    return pages


def compose_shifted_page(
    source,
    page_width: float,
    page_height: float,
    cut_y: float,
    shift_y: float,
    top_margin: float = 50.0,
    bottom_margin: float = 50.0,
    paginate_overflow: bool = True,
    overflow_start: float | None = None,
) -> tuple[PageObject, list[PageObject]]:
    """
    Return a page where content below cut_y is shifted by shift_y.

    Positive shift_y moves lower content down and creates continuation pages
    for any bottom content that would leave the page.
    """
    page = PageObject.create_blank_page(width=page_width, height=page_height)
    cut_y = _clamp(cut_y, 0.0, page_height)

    merge_page_band(page, source, page_width, page_height, 0.0, cut_y, 0.0)

    continuations: list[PageObject] = []
    if shift_y > 0:
        if paginate_overflow:
            visible_bottom = overflow_start if overflow_start is not None else page_height - bottom_margin - shift_y
            visible_bottom = max(cut_y, _clamp(visible_bottom, 0.0, page_height))
        else:
            visible_bottom = page_height
        merge_page_band(page, source, page_width, page_height, cut_y, visible_bottom, shift_y)
        if paginate_overflow and visible_bottom < page_height:
            continuations = continuation_pages_from_band(
                source,
                page_width,
                page_height,
                overflow_start=visible_bottom,
                top_margin=top_margin,
                bottom_margin=bottom_margin,
            )
    else:
        merge_page_band(page, source, page_width, page_height, cut_y, page_height, shift_y)

    return page, continuations


def compose_removed_region_page(
    source,
    page_width: float,
    page_height: float,
    y_top: float,
    y_bottom: float,
) -> PageObject:
    """Return a page with [y_top, y_bottom] removed and lower content shifted up."""
    y_top = _clamp(y_top, 0.0, page_height)
    y_bottom = _clamp(y_bottom, y_top, page_height)
    removed_height = y_bottom - y_top

    page = PageObject.create_blank_page(width=page_width, height=page_height)
    merge_page_band(page, source, page_width, page_height, 0.0, y_top, 0.0)
    merge_page_band(page, source, page_width, page_height, y_bottom, page_height, -removed_height)
    return page
