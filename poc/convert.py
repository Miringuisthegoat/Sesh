"""Dispatcher: python -m poc.convert <file> [-o out.pdf]"""
import argparse
import sys
import tempfile
from pathlib import Path

from .errors import ConversionError
from .converters.docx_conv import convert_docx
from .converters.images import convert_image
from .converters.xlsx_conv import convert_xlsx

SIGNATURES = {".docx": b"PK\x03\x04", ".xlsx": b"PK\x03\x04", ".jpg": b"\xff\xd8\xff", ".jpeg": b"\xff\xd8\xff", ".png": b"\x89PNG\r\n\x1a\n"}
CONVERTERS = {".docx": convert_docx, ".xlsx": convert_xlsx, ".jpg": convert_image, ".jpeg": convert_image, ".png": convert_image}
MAX_BYTES = 25 * 1024 * 1024


def convert(src: Path, dst: Path) -> list[str]:
    src, dst = Path(src), Path(dst)
    ext = src.suffix.lower()
    if ext not in CONVERTERS:
        raise ConversionError("UNSUPPORTED_TYPE", f"'{ext}' files are not supported.")
    if src.stat().st_size > MAX_BYTES:
        raise ConversionError("FILE_TOO_LARGE", f"File exceeds {MAX_BYTES // 1024 // 1024} MB.")
    with open(src, "rb") as f:
        if not f.read(8).startswith(SIGNATURES[ext]) and not (f.seek(0) or f.read(8)).startswith(b"\xd0\xcf\x11\xe0"):
            raise ConversionError("SIGNATURE_MISMATCH", "File content does not match its extension.")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="poc_")) / "out.pdf"
    try:
        warnings = CONVERTERS[ext](src, tmp)
        tmp.replace(dst) if tmp.parent.drive == dst.parent.drive else dst.write_bytes(tmp.read_bytes())
    finally:
        tmp.unlink(missing_ok=True)
        try:
            tmp.parent.rmdir()
        except OSError:
            pass
    return warnings


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    out = Path(a.out) if a.out else Path(a.file).with_suffix(".pdf")
    try:
        for w in convert(Path(a.file), out):
            print("warning:", w)
        print("wrote", out)
    except ConversionError as e:
        print(f"ERROR {e.code}: {e.message}")
        sys.exit(1)
