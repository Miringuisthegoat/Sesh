"""Quick performance check of conversion and preview rendering on THIS machine.
Run from the project root (venv active):  python -m scripts.perf_check
Builds sample files in memory, converts each with the real converter, renders page 1 as a PNG, prints timings.
Flags anything slower than --limit seconds (default 30). A guide, not a benchmark: run it on the production server too."""
import argparse
import io
import sys
import tempfile
import time
from pathlib import Path

import openpyxl
from PIL import Image

from app.conversion.runner import convert_file
from app.services.preview import render_page_png
from tests.factories import make_docx, make_png, make_xlsx


def big_xlsx(rows: int) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Row", "Name", "Amount", "Note"])
    for i in range(rows):
        ws.append([i, f"Item {i}", i * 1.5, "x" * 20])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def big_png() -> bytes:
    buf = io.BytesIO()
    Image.effect_noise((3200, 2200), 64).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


CASES = [
    ("docx 1 paragraph", "docx", make_docx()),
    ("docx 400 paragraphs", "docx", make_docx([f"Paragraph {i} " + "lorem ipsum " * 20 for i in range(400)], table=True)),
    ("xlsx 30 rows", "xlsx", make_xlsx()),
    ("xlsx 5000 rows", "xlsx", big_xlsx(5000)),
    ("png 120x80", "png", make_png()),
    ("png 3200x2200 noise", "png", big_png()),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=float, default=30.0)
    limit = ap.parse_args().limit
    slow = 0
    print(f"{'case':26} {'bytes':>10} {'convert s':>10} {'pages':>6} {'preview s':>10}")
    with tempfile.TemporaryDirectory() as tmp:
        for name, ext, data in CASES:
            src, dst = Path(tmp) / f"s.{ext}", Path(tmp) / "o.pdf"
            src.write_bytes(data)
            t0 = time.perf_counter()
            res = convert_file(src, ext, dst, max_bytes=25 * 1024 * 1024, max_pixels=50_000_000)
            t1 = time.perf_counter()
            render_page_png(dst.read_bytes(), 0, 900)
            t2 = time.perf_counter()
            flag = "  <-- SLOW" if (t1 - t0) > limit or (t2 - t1) > limit else ""
            slow += bool(flag)
            print(f"{name:26} {len(data):>10} {t1 - t0:>10.2f} {res.pdf.pages:>6} {t2 - t1:>10.2f}{flag}")
    return 1 if slow else 0


if __name__ == "__main__":
    sys.exit(main())
