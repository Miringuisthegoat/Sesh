import hashlib
from pathlib import Path
from pypdf import PdfReader


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_pdf(p: Path) -> dict:
    p = Path(p)
    info = {"ok": False, "pages": 0, "text_chars": 0, "size": p.stat().st_size, "sha256": sha256_file(p), "page_sizes": [], "error": None}
    try:
        with open(p, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise ValueError("missing %PDF header")
        r = PdfReader(str(p), strict=False)
        if r.is_encrypted:
            raise ValueError("PDF is encrypted")
        info["pages"] = len(r.pages)
        if info["pages"] < 1:
            raise ValueError("no pages")
        for pg in r.pages:
            info["text_chars"] += len((pg.extract_text() or "").strip())
            info["page_sizes"].append((round(float(pg.mediabox.width)), round(float(pg.mediabox.height))))
        info["ok"] = True
    except Exception as e:
        info["error"] = str(e)[:200]
    return info


def rasterize(pdf: Path, out_dir: Path, max_pages: int = 3, scale: float = 1.0) -> list[Path]:
    """Optional: render first pages to PNG for visual inspection (needs pypdfium2)."""
    try:
        import pypdfium2 as pdfium
    except ImportError:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pdfium.PdfDocument(str(pdf))
    paths = []
    for i in range(min(len(doc), max_pages)):
        img = doc[i].render(scale=scale).to_pil()
        p = out_dir / f"{Path(pdf).stem}_p{i + 1}.png"
        img.save(p)
        paths.append(p)
    doc.close()
    return paths
