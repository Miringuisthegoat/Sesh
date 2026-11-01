"""Server-side page previews: render a stored PDF page to PNG with pypdfium2 (works in every browser, incl. mobile)."""
import io
import threading

import pypdfium2 as pdfium

_lock = threading.Lock()  # pdfium is not thread-safe


def render_page_png(pdf_bytes: bytes, index: int, width_px: int) -> bytes:
    with _lock:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            if index < 0 or index >= len(doc):
                raise IndexError(index)
            page = doc[index]
            scale = max(0.2, min(4.0, width_px / max(page.get_width(), 1)))
            img = page.render(scale=scale).to_pil().convert("RGB")
        finally:
            doc.close()
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
