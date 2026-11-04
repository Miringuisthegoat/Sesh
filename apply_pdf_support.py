"""Run once from the project root after extracting the zip:  python apply_pdf_support.py
Adds PDF upload (printed as-is) and the drag-and-drop styles. Idempotent; stops loudly if an anchor is missing."""
import re
from pathlib import Path

PDF_VALIDATOR = '''MAX_PDF_PAGES = 300


def _validate_pdf_source(path: Path) -> None:
    """A PDF upload is printed as-is, so it must be a readable, unencrypted PDF with a sane page count."""
    with open(path, "rb") as f:
        head = f.read(5)
    if head != b"%PDF-":  # the agent checks the same signature before printing
        raise ConversionError("PDF_MALFORMED", "The file is not a valid PDF.")
    try:
        r = PdfReader(str(path), strict=False)
        if r.is_encrypted:
            raise ConversionError("PDF_ENCRYPTED", "This PDF is password-protected. Remove the password and upload it again.")
        pages = len(r.pages)
        if pages < 1:
            raise ConversionError("PDF_MALFORMED", "The PDF has no pages.")
        if pages > MAX_PDF_PAGES:
            raise ConversionError("PDF_TOO_LARGE", f"The PDF has {pages} pages. The limit is {MAX_PDF_PAGES}.")
        r.pages[0].mediabox  # forces a real parse of the first page
    except ConversionError:
        raise
    except Exception as e:
        raise ConversionError("PDF_MALFORMED", "The PDF is corrupt or could not be read.") from e


'''

VAL_ANCHOR = "def validate_source(path: Path, ext: str,"
SUP_ANCHOR = '    "jpg": ("image/jpeg", None), "jpeg": ("image/jpeg", None), "png": ("image/png", None),\n'
DISPATCH = "    if required:\n        _validate_zip(path, ext, required)\n"

EDITS = [
    ("app/conversion/validation.py", SUP_ANCHOR, '    "pdf": ("application/pdf", None),\n' + SUP_ANCHOR),
    ("app/conversion/validation.py", VAL_ANCHOR, PDF_VALIDATOR + VAL_ANCHOR),
    ("app/conversion/validation.py", DISPATCH,
     '    if ext == "pdf":\n        _validate_pdf_source(path)\n    elif required:\n        _validate_zip(path, ext, required)\n'),
    ("app/conversion/validation.py", "Use .docx, .xlsx, .jpg, .jpeg or .png.", "Use .docx, .xlsx, .pdf, .jpg, .jpeg or .png."),
    ("app/conversion/runner.py", "from .engine.converters.images import convert_image\n",
     "from .engine.converters.images import convert_image\nfrom .engine.converters.pdf_passthrough import convert_pdf\n"),
    ("app/conversion/runner.py", '"png": convert_image}', '"png": convert_image, "pdf": convert_pdf}'),
    ("app/services/documents.py", "Upload a .docx, .xlsx, .jpg, .jpeg or .png file.", "Upload a .docx, .xlsx, .pdf, .jpg, .jpeg or .png file."),
    ("config.py", '"docx,xlsx,jpg,jpeg,png"', '"docx,xlsx,pdf,jpg,jpeg,png"'),
    (".env.example", "ALLOWED_EXTENSIONS=docx,xlsx,jpg,jpeg,png", "ALLOWED_EXTENSIONS=docx,xlsx,pdf,jpg,jpeg,png"),
]

CSS = '''
/* Drag-and-drop upload */
.dropzone { position: relative; background: #fcfbf9; border: 1.5px dashed #cfccd6; border-radius: 12px; transition: border-color .15s ease, background .15s ease, box-shadow .15s ease; }
.dropzone:hover { border-color: #a8a3b8; }
.dropzone:focus-within { border-color: #8c86a1; box-shadow: 0 0 0 .2rem rgba(86, 78, 115, .13); }
.dropzone.is-dragover { background: var(--sesh-plum-soft); border-color: var(--sesh-plum); box-shadow: 0 0 0 4px rgba(49, 47, 64, .08); }
.dropzone-input { position: absolute; width: 1px; height: 1px; opacity: 0; overflow: hidden; pointer-events: none; }
.dropzone-label { display: flex; flex-direction: column; align-items: center; gap: 4px; margin: 0; padding: 34px 20px; color: var(--sesh-ink-soft); font-size: 12px; text-align: center; cursor: pointer; }
.dropzone-label strong { color: var(--sesh-ink); font-size: 14px; font-weight: 650; }
.dropzone-icon { display: grid; width: 40px; height: 40px; margin-bottom: 8px; place-items: center; color: var(--sesh-plum); background: var(--sesh-plum-soft); border-radius: 50%; font-size: 18px; font-weight: 700; }
.dropzone.is-dragover .dropzone-icon { color: #fff; background: var(--sesh-plum); }
.dropzone-file { display: flex; align-items: center; gap: 10px; padding: 18px 16px; }
.dropzone-file[hidden], .dropzone-label[hidden] { display: none; }
.dropzone-file-name { min-width: 0; flex: 1; overflow: hidden; color: var(--sesh-ink); font-size: 13px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.dropzone-file-size { color: var(--sesh-muted); font-size: 11px; white-space: nowrap; }
.dropzone-clear { display: grid; width: 28px; height: 28px; place-items: center; color: #817e88; background: #fff; border: 1px solid var(--sesh-line); border-radius: 50%; font-size: 16px; line-height: 1; }
.dropzone-clear:hover { color: #9c4545; border-color: #e6caca; }
'''

ROW = "| 53 | 2026-11-04 | PDF upload is accepted and printed exactly as uploaded: validated at upload (signature at byte 0, readable, unencrypted, at most 300 pages), then a byte-for-byte copy goes through the normal conversion queue so previews, checksums and printing need no special case | Scope addition requested by the user (printing an existing PDF is not PDF editing). Cost: the file is stored twice (upload and converted copy) |\n"


def main():
    for path, old, new in EDITS:
        p = Path(path)
        s = p.read_text(encoding="utf-8")
        if new in s:
            print("already applied:", path, repr(old[:45]))
            continue
        if s.count(old) != 1:
            raise SystemExit(f"ANCHOR NOT FOUND (or not unique) in {path}: {old[:70]!r}")
        p.write_text(s.replace(old, new), encoding="utf-8")
        print("patched:", path)

    css = Path("app/static/app.css")
    s = css.read_text(encoding="utf-8")
    if "/* Drag-and-drop upload */" in s:
        print("already applied: app.css")
    else:
        css.write_text(s + ("" if s.endswith("\n") else "\n") + CSS, encoding="utf-8")
        print("appended: app/static/app.css")

    env = Path(".env")  # a copied .env overrides the new default, so fix it too
    if env.exists():
        s = env.read_text(encoding="utf-8")
        m = re.search(r"^ALLOWED_EXTENSIONS=(.*)$", s, re.M)
        if m and "pdf" not in m.group(1).lower():
            value = m.group(1).split("#")[0].strip()
            env.write_text(s[:m.start()] + f"ALLOWED_EXTENSIONS={value},pdf" + s[m.end():], encoding="utf-8")
            print("patched: .env (added pdf to ALLOWED_EXTENSIONS)")

    d = Path("docs/DECISIONS.md")
    s = d.read_text(encoding="utf-8")
    if "| 53 |" in s:
        print("already applied: docs/DECISIONS.md")
    else:
        d.write_text(s + ("" if s.endswith("\n") else "\n") + ROW, encoding="utf-8")
        print("DECISIONS.md updated")


if __name__ == "__main__":
    main()
