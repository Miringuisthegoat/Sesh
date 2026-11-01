"""Generate a synthetic test corpus into poc/corpus/. Real office documents are the true go/no-go test: drop them into poc/corpus/real/."""
import io
import os
import random
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_BREAK
from docx.shared import Inches, Pt, RGBColor
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from PIL import Image, ImageDraw

OUT = Path(__file__).parent / "corpus"


def _png_bytes(w=600, h=300, text="Test image"):
    im = Image.new("RGB", (w, h), (230, 240, 255))
    d = ImageDraw.Draw(im)
    d.rectangle([5, 5, w - 6, h - 6], outline=(0, 60, 160), width=4)
    d.text((20, 20), text, fill=(0, 0, 0))
    b = io.BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def docx_simple():
    d = Document()
    d.add_heading("Simple Letter", 1)
    d.add_paragraph("Dear colleague,")
    p = d.add_paragraph("This is a ")
    r = p.add_run("bold"); r.bold = True
    p.add_run(" and ")
    r = p.add_run("italic"); r.italic = True
    p.add_run(" test with unicode: café, naïve, Ωmega, Nairobi — KSh 1,500.")
    d.add_paragraph("First bullet", style="List Bullet")
    d.add_paragraph("Second bullet", style="List Bullet")
    d.save(OUT / "docx_simple.docx")


def docx_rich():
    d = Document()
    s = d.sections[0]
    s.header.paragraphs[0].text = "ACME Ltd — Confidential"
    s.footer.paragraphs[0].text = "Quarterly report"
    d.add_heading("Quarterly Report", 0)
    d.add_paragraph("Intro paragraph. " * 40)
    t = d.add_table(rows=4, cols=3)
    t.style = "Table Grid"
    for i, row in enumerate(t.rows):
        for j, c in enumerate(row.cells):
            c.text = f"R{i}C{j} value"
    d.add_paragraph()
    d.add_picture(io.BytesIO(_png_bytes()), width=Inches(4))
    d.add_paragraph("After picture. " * 30)
    d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    d.add_heading("Section Two", 1)
    for i in range(60):
        d.add_paragraph(f"Line {i}: body text that fills multiple pages for pagination checks.")
    d.add_page_break()
    d.add_heading("Final page", 1).paragraph_format.page_break_before = False
    m = d.add_table(rows=2, cols=3)
    m.style = "Table Grid"
    a = m.cell(0, 0).merge(m.cell(0, 2)); a.text = "Merged header"
    d.save(OUT / "docx_rich_multipage.docx")


def docx_landscape_letter():
    d = Document()
    s = d.sections[0]
    s.orientation = WD_ORIENT.LANDSCAPE
    s.page_width, s.page_height = Inches(11), Inches(8.5)
    d.add_heading("Landscape US Letter", 1)
    d.add_paragraph("Page size and orientation must be honoured. " * 10)
    d.save(OUT / "docx_landscape_letter.docx")


def docx_unsupported_features():
    d = Document()
    d.add_paragraph("Numbered:")
    d.add_paragraph("one", style="List Number")
    d.add_paragraph("two", style="List Number")
    s = d.add_paragraph().add_run("Coloured big"); s.font.size = Pt(20); s.font.color.rgb = RGBColor(200, 0, 0)
    d.save(OUT / "docx_numbered_lists.docx")


def xlsx_simple():
    wb = Workbook(); ws = wb.active; ws.title = "Sales"
    ws.append(["Item", "Qty", "Price", "Total"])
    for i in range(1, 21):
        ws.append([f"Item {i}", i, i * 12.5, i * i * 12.5])
    for c in ws[1]:
        c.font = Font(bold=True); c.fill = PatternFill("solid", fgColor="DDDDDD")
    for c in ws["C"][1:]: c.number_format = "#,##0.00"
    wb.save(OUT / "xlsx_simple.xlsx")


def xlsx_multi():
    wb = Workbook()
    ws = wb.active; ws.title = "Report"
    ws.merge_cells("A1:E1"); ws["A1"] = "Annual Report"; ws["A1"].font = Font(bold=True, size=14); ws["A1"].alignment = Alignment(horizontal="center")
    ws.append(["Region", "Q1", "Q2", "Q3", "Q4"])
    for i in range(80):
        ws.append([f"Region {i}", *[random.Random(i * 7 + q).randint(100, 9999) for q in range(4)]])
    ws.print_title_rows = "1:2"
    ws.print_area = "A1:E60"
    ws.page_setup.orientation = "landscape"
    ws2 = wb.create_sheet("Notes"); ws2["A1"] = "Second sheet"; ws2["B2"] = "Hidden-ish detail"
    ws3 = wb.create_sheet("Secret"); ws3["A1"] = "hidden"; ws3.sheet_state = "hidden"
    wb.save(OUT / "xlsx_multisheet_printarea.xlsx")


def xlsx_wide():
    wb = Workbook(); ws = wb.active; ws.title = "Wide"
    ws.append([f"Col {i}" for i in range(30)])
    for r in range(15):
        ws.append([r * c for c in range(30)])
    wb.save(OUT / "xlsx_wide_30cols.xlsx")


def xlsx_formulas():
    wb = Workbook(); ws = wb.active
    ws["A1"], ws["A2"], ws["A3"] = 10, 20, "=SUM(A1:A2)"
    ws["B1"] = 0.256; ws["B1"].number_format = "0.0%"
    wb.save(OUT / "xlsx_formulas_uncached.xlsx")  # openpyxl writes no cached values: expected warning


def xlsx_large():
    wb = Workbook(write_only=True); ws = wb.create_sheet("Big")
    for r in range(5000):
        ws.append([r, f"Row {r}", r * 1.5, "x" * 10])
    wb.save(OUT / "xlsx_large_5000rows.xlsx")


def images():
    (OUT / "image_photo.jpg").write_bytes(_to_jpeg(_png_bytes(1600, 1200, "JPEG landscape")))
    (OUT / "image_portrait.png").write_bytes(_png_bytes(900, 1400, "PNG portrait"))
    (OUT / "image_lowres.png").write_bytes(_png_bytes(200, 120, "low res"))
    (OUT / "bad_image_huge_dimensions.png").write_bytes(_bomb_png())  # header claims 20000x20000 px; tiny file


def _bomb_png(w=20000, h=20000):
    """Valid PNG header declaring huge dimensions with almost no data (instant to create, safe to store)."""
    import struct, zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 1, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"")) + chunk(b"IEND", b""))


def _to_jpeg(png):
    b = io.BytesIO(); Image.open(io.BytesIO(png)).convert("RGB").save(b, "JPEG", quality=90); return b.getvalue()


def bad_files():
    (OUT / "bad_malformed.docx").write_bytes(os.urandom(2048))
    (OUT / "bad_fake_zip.docx").write_bytes(b"PK\x03\x04" + os.urandom(500))
    (OUT / "bad_encrypted_like.xlsx").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + os.urandom(500))
    (OUT / "bad_png_is_text.png").write_bytes(b"this is not a png")
    (OUT / "bad_unsupported.pptx").write_bytes(b"PK\x03\x04")


def main():
    OUT.mkdir(exist_ok=True)
    for f in (docx_simple, docx_rich, docx_landscape_letter, docx_unsupported_features, xlsx_simple, xlsx_multi, xlsx_wide,
              xlsx_formulas, xlsx_large, images, bad_files):
        f()
    (OUT / "real").mkdir(exist_ok=True)
    print(f"Corpus written to {OUT}  (put your real Word/Excel/image files in {OUT / 'real'})")


if __name__ == "__main__":
    main()
