"""Small valid sample files built in memory for tests."""
import io
import zipfile

import openpyxl
from docx import Document as Docx
from PIL import Image
from pypdf import PdfWriter


def make_docx(paragraphs=("Hello from Sesh",), table=False) -> bytes:
    d = Docx()
    for p in paragraphs:
        d.add_paragraph(p)
    if table:
        t = d.add_table(rows=2, cols=2)
        t.cell(0, 0).text, t.cell(0, 1).text, t.cell(1, 0).text, t.cell(1, 1).text = "a", "b", "c", "d"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Item", "Qty"])
    for i in range(30):
        ws.append([f"Item {i}", i])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_png(size=(120, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "navy").save(buf, "PNG")
    return buf.getvalue()


def make_jpg(size=(120, 80)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, "teal").save(buf, "JPEG")
    return buf.getvalue()


def make_pdf(*, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("test-password")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_zip_bomb(inner="word/document.xml", mb=300) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(inner, b"0" * (mb * 1024 * 1024))
    return buf.getvalue()


OLE_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600
