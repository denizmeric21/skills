"""
PDF Skill Test Suite
Demonstrates what the pdf skill can do:
  1. Create a PDF (reportlab)
  2. Read & extract text (pypdf)
  3. Extract metadata (pypdf)
  4. Merge PDFs (pypdf)
  5. Split PDF (pypdf)
  6. Rotate a page (pypdf)
  7. Add a watermark (pypdf)
  8. Extract tables (pdfplumber)
  9. Password-protect a PDF (pypdf)
"""

import os
import sys

RESULTS_DIR = "/tmp/pdf_skill_test"
os.makedirs(RESULTS_DIR, exist_ok=True)

def p(label, msg="OK"):
    print(f"  [{label}] {msg}")

# ── 1. CREATE PDFs ────────────────────────────────────────────────────────────
def test_create():
    print("\n[1] Creating PDFs with reportlab...")
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors

    styles = getSampleStyleSheet()

    # Main test document (2 pages)
    path = f"{RESULTS_DIR}/main.pdf"
    doc = SimpleDocTemplate(path, pagesize=letter)
    story = [
        Paragraph("PDF Skill Test Document", styles["Title"]),
        Spacer(1, 12),
        Paragraph("This document was created by the PDF skill test suite.", styles["Normal"]),
        Spacer(1, 12),
        Paragraph("Sample table below:", styles["Heading2"]),
        Spacer(1, 6),
        Table(
            [["Name", "Score", "Grade"],
             ["Alice", "95", "A"],
             ["Bob",   "82", "B"],
             ["Carol", "74", "C"]],
            style=TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.grey),
                ("TEXTCOLOR",  (0, 0), (-1, 0), colors.whitesmoke),
                ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
                ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
            ])
        ),
    ]
    doc.build(story)
    p("create main.pdf", f"saved → {path}")

    # Watermark page
    wm_path = f"{RESULTS_DIR}/watermark_page.pdf"
    from reportlab.pdfgen import canvas as rl_canvas
    c = rl_canvas.Canvas(wm_path, pagesize=letter)
    c.setFont("Helvetica-Bold", 48)
    c.setFillColorRGB(0.85, 0.85, 0.85)
    c.saveState()
    c.translate(300, 400)
    c.rotate(45)
    c.drawCentredString(0, 0, "CONFIDENTIAL")
    c.restoreState()
    c.save()
    p("create watermark_page.pdf", f"saved → {wm_path}")

    # Second document for merge test
    path2 = f"{RESULTS_DIR}/appendix.pdf"
    doc2 = SimpleDocTemplate(path2, pagesize=letter)
    doc2.build([
        Paragraph("Appendix", styles["Title"]),
        Spacer(1, 12),
        Paragraph("This page comes from a second PDF and will be merged.", styles["Normal"]),
    ])
    p("create appendix.pdf", f"saved → {path2}")

    return path, wm_path, path2

# ── 2. READ & EXTRACT TEXT ────────────────────────────────────────────────────
def test_read(pdf_path):
    print("\n[2] Reading & extracting text (pypdf)...")
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    p("page count", f"{len(reader.pages)} page(s)")
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        preview = text[:80].replace("\n", " ")
        p(f"page {i+1} text", f'"{preview}..."')

# ── 3. METADATA ───────────────────────────────────────────────────────────────
def test_metadata(pdf_path):
    print("\n[3] Extracting metadata (pypdf)...")
    from pypdf import PdfReader

    meta = PdfReader(pdf_path).metadata
    p("title",   meta.title   or "(none)")
    p("author",  meta.author  or "(none)")
    p("creator", meta.creator or "(none)")

# ── 4. MERGE ──────────────────────────────────────────────────────────────────
def test_merge(pdf1, pdf2):
    print("\n[4] Merging two PDFs (pypdf)...")
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for path in [pdf1, pdf2]:
        for page in PdfReader(path).pages:
            writer.add_page(page)

    out = f"{RESULTS_DIR}/merged.pdf"
    with open(out, "wb") as f:
        writer.write(f)
    total = len(PdfReader(out).pages)
    p("merged", f"{total} pages → {out}")

# ── 5. SPLIT ──────────────────────────────────────────────────────────────────
def test_split(pdf_path):
    print("\n[5] Splitting PDF into single pages (pypdf)...")
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(pdf_path)
    for i, page in enumerate(reader.pages):
        writer = PdfWriter()
        writer.add_page(page)
        out = f"{RESULTS_DIR}/page_{i+1}.pdf"
        with open(out, "wb") as f:
            writer.write(f)
        p(f"page {i+1}", f"saved → {out}")

# ── 6. ROTATE ─────────────────────────────────────────────────────────────────
def test_rotate(pdf_path):
    print("\n[6] Rotating page 1 by 90° (pypdf)...")
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    page = reader.pages[0]
    page.rotate(90)
    writer.add_page(page)

    out = f"{RESULTS_DIR}/rotated.pdf"
    with open(out, "wb") as f:
        writer.write(f)
    p("rotated", f"saved → {out}")

# ── 7. WATERMARK ──────────────────────────────────────────────────────────────
def test_watermark(pdf_path, wm_path):
    print("\n[7] Applying watermark (pypdf)...")
    from pypdf import PdfReader, PdfWriter

    watermark = PdfReader(wm_path).pages[0]
    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for page in reader.pages:
        page.merge_page(watermark)
        writer.add_page(page)

    out = f"{RESULTS_DIR}/watermarked.pdf"
    with open(out, "wb") as f:
        writer.write(f)
    p("watermarked", f"saved → {out}")

# ── 8. TABLE EXTRACTION ───────────────────────────────────────────────────────
def test_tables(pdf_path):
    print("\n[8] Extracting tables (pdfplumber)...")
    import pdfplumber

    with pdfplumber.open(pdf_path) as pdf:
        found = 0
        for i, page in enumerate(pdf.pages):
            tables = page.extract_tables()
            for j, table in enumerate(tables):
                found += 1
                p(f"page {i+1} table {j+1}", f"{len(table)} rows × {len(table[0])} cols")
                for row in table:
                    print(f"       {row}")
        if not found:
            p("tables", "no tables detected on any page")

# ── 9. PASSWORD PROTECTION ────────────────────────────────────────────────────
def test_password(pdf_path):
    print("\n[9] Password-protecting a PDF (pypdf)...")
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(pdf_path)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt("user123", "owner456")

    out = f"{RESULTS_DIR}/encrypted.pdf"
    with open(out, "wb") as f:
        writer.write(f)

    # Verify: must supply password to read
    locked = PdfReader(out)
    needs_pass = locked.is_encrypted
    p("encrypted", f"is_encrypted={needs_pass} → {out}")
    locked.decrypt("user123")
    p("decrypt",   f"{len(locked.pages)} page(s) accessible after decrypt")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    print("=" * 55)
    print("  PDF Skill Test Suite")
    print(f"  Output directory: {RESULTS_DIR}")
    print("=" * 55)

    try:
        main_pdf, wm_pdf, appendix_pdf = test_create()
        test_read(main_pdf)
        test_metadata(main_pdf)
        test_merge(main_pdf, appendix_pdf)
        test_split(main_pdf)
        test_rotate(main_pdf)
        test_watermark(main_pdf, wm_pdf)
        test_tables(main_pdf)
        test_password(main_pdf)

        print("\n" + "=" * 55)
        print("  All tests passed!")
        print(f"  Output files are in: {RESULTS_DIR}")
        print("=" * 55)
    except Exception as e:
        print(f"\n  ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
