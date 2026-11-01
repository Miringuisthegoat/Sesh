"""Convert every file in poc/corpus (and corpus/real), validate, rasterize, write results/report.md"""
import time
from pathlib import Path

from .convert import convert
from .errors import ConversionError
from .validate import rasterize, validate_pdf

ROOT = Path(__file__).parent
CORPUS, RESULTS = ROOT / "corpus", ROOT / "results"
EXTS = {".docx", ".xlsx", ".jpg", ".jpeg", ".png", ".pptx", ".doc", ".xls"}


def main():
    RESULTS.mkdir(exist_ok=True)
    files = sorted(p for p in list(CORPUS.glob("*")) + list((CORPUS / "real").glob("*")) if p.is_file() and p.suffix.lower() in EXTS)
    if not files:
        print("No corpus found. Run: python -m poc.make_corpus"); return 1
    rows, bad_unexpected = [], 0
    for f in files:
        expect_fail = f.name.startswith("bad_")
        t0 = time.perf_counter()
        out = RESULTS / (f.stem + ".pdf")
        out.unlink(missing_ok=True)
        status, detail, pages, warnings, png = "", "", "", [], ""
        try:
            warnings = convert(f, out)
            v = validate_pdf(out)
            if not v["ok"]:
                status, detail = "INVALID_PDF", v["error"]
            else:
                status, pages = "OK", f'{v["pages"]} ({v["page_sizes"][0][0]}x{v["page_sizes"][0][1]}pt)'
                detail = f'{v["size"] // 1024} KB, text chars {v["text_chars"]}, sha256 {v["sha256"][:12]}…'
                pngs = rasterize(out, RESULTS / "png")
                png = ", ".join(p.name for p in pngs)
        except ConversionError as e:
            status, detail = "REJECTED", f"{e.code}: {e.message}"
        except Exception as e:  # unexpected = bug
            status, detail = "CRASH", repr(e)[:150]
        dt = time.perf_counter() - t0
        ok_outcome = (status == "REJECTED") if expect_fail else (status == "OK")
        if not ok_outcome:
            bad_unexpected += 1
        rows.append((f.name, status, "expected" if ok_outcome else "UNEXPECTED", pages, f"{dt:.2f}s", detail, "; ".join(warnings), png))
        print(f"{'  ' if ok_outcome else '!!'} {f.name:40s} {status:9s} {pages:18s} {dt:5.2f}s {detail}")
    lines = ["# Conversion PoC report", "", "| File | Result | Outcome | Pages | Time | Detail | Warnings (known limitations hit) | PNG preview |", "|---|---|---|---|---|---|---|---|"]
    lines += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in rows]
    lines += ["", f"Unexpected outcomes: **{bad_unexpected}**", "", "A passing run means 'a valid PDF was produced'. It does NOT mean the layout matches Word/Excel. Inspect results/png/*.png by eye."]
    (RESULTS / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {RESULTS / 'report.md'}   Unexpected outcomes: {bad_unexpected}")
    return 1 if bad_unexpected else 0


if __name__ == "__main__":
    raise SystemExit(main())
