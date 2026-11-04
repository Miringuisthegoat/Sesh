"""Run once from the project root:  python fix_pdf_overlap.py
Reconciles the PDF-upload overlay with the PDF support the project already had. Idempotent."""
import re
from pathlib import Path

CSS_MARK = "/* Drag-and-drop upload */"
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


def read(p):
    return Path(p).read_text(encoding="utf-8")


def write(p, s):
    Path(p).write_text(s, encoding="utf-8")


def main():
    # 1. validation.py: drop the duplicate "pdf" entry, and make uploads use the strict PDF check
    p = "app/conversion/validation.py"
    s = read(p)
    dup = '    "pdf": ("application/pdf", None),\n}'
    if dup in s:
        s = s.replace(dup, "}")
        print("removed duplicate 'pdf' entry")
    old = '    if ext == "pdf":\n        validate_pdf(path)\n'
    new = '    if ext == "pdf":\n        _validate_pdf_source(path)\n'
    if old in s:
        s = s.replace(old, new)
        print("upload now uses _validate_pdf_source")
    elif new not in s:
        raise SystemExit("ANCHOR NOT FOUND: the 'if ext == \"pdf\"' block in validation.py")
    write(p, s)

    # 2. the passthrough module duplicates convert_pdf that runner.py already defines
    f = Path("app/conversion/engine/converters/pdf_passthrough.py")
    if f.exists():
        f.unlink()
        print("deleted unused pdf_passthrough.py")
    if "pdf_passthrough" in read("app/conversion/runner.py"):
        raise SystemExit("runner.py imports pdf_passthrough: remove that import line, then run this again")

    # 3. wording expected by tests/test_upload_validation.py
    p = "app/templates/documents/upload.html"
    s = read(p)
    if "Drag and drop a file here" in s:
        write(p, s.replace("Drag and drop a file here", "Drag and drop your file here"))
        print("patched upload.html wording")

    # 4. dropzone styles (the first script stopped before reaching this)
    p = "app/static/app.css"
    s = read(p)
    if CSS_MARK not in s and ".dropzone {" not in s:
        write(p, s + ("" if s.endswith("\n") else "\n") + CSS)
        print("appended dropzone CSS")

    # 5. a copied .env overrides the default allowed types
    env = Path(".env")
    if env.exists():
        s = env.read_text(encoding="utf-8")
        m = re.search(r"^ALLOWED_EXTENSIONS=(.*)$", s, re.M)
        if m and "pdf" not in m.group(1).lower():
            value = m.group(1).split("#")[0].strip()
            env.write_text(s[:m.start()] + f"ALLOWED_EXTENSIONS={value},pdf" + s[m.end():], encoding="utf-8")
            print("patched .env (added pdf)")
    print("done")


if __name__ == "__main__":
    main()
