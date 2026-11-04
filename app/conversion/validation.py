"""Content validation for uploads and generated PDFs. Used at upload time AND again in the worker."""
from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from pypdf import PdfReader

from .engine.errors import ConversionError

SUPPORTED = {
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "word/document.xml"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xl/workbook.xml"),
    "pdf": ("application/pdf", None),
    "jpg": ("image/jpeg", None), "jpeg": ("image/jpeg", None), "png": ("image/png", None),
}
# Browsers often send a generic type; anything else that contradicts the extension is rejected.
GENERIC_MIMES = {"", "application/octet-stream", "application/zip", "application/x-zip-compressed"}
OLE_MAGIC = b"\xd0\xcf\x11\xe0"  # legacy Office files AND password-protected modern Office files

MAX_ZIP_ENTRIES = 5000
MAX_ZIP_UNCOMPRESSED = 200 * 1024 * 1024
MAX_ENTRY_RATIO = 100  # per-entry expansion ratio (only checked for entries over 10 MB)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mime_acceptable(ext: str, client_mime: str | None) -> bool:
    expected = SUPPORTED[ext][0]
    m = (client_mime or "").split(";")[0].strip().lower()
    if m in GENERIC_MIMES or m == expected:
        return True
    return ext in ("jpg", "jpeg") and m in ("image/jpg", "image/pjpeg")


@dataclass
class SourceInfo:
    ext: str
    mime: str


def _validate_zip(path: Path, ext: str, required: str) -> None:
    label = ext.upper()
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(OLE_MAGIC):
        raise ConversionError("PASSWORD_PROTECTED_OR_LEGACY",
                              "This file is password-protected or in an old Office format. Remove the password or save it as .docx/.xlsx and upload again.")
    if not zipfile.is_zipfile(path):
        raise ConversionError(f"{label}_MALFORMED", f"The file is not a valid .{ext} file.")
    try:
        with zipfile.ZipFile(path) as z:
            infos = z.infolist()
            if len(infos) > MAX_ZIP_ENTRIES:
                raise ConversionError("ARCHIVE_TOO_COMPLEX", "The file contains too many internal parts to process safely.")
            total = 0
            for i in infos:
                total += i.file_size
                if i.file_size > 10 * 1024 * 1024 and i.compress_size and i.file_size / i.compress_size > MAX_ENTRY_RATIO:
                    raise ConversionError("ARCHIVE_UNSAFE", "The file expands to an unsafe size and was rejected.")
            if total > MAX_ZIP_UNCOMPRESSED:
                raise ConversionError("ARCHIVE_UNSAFE", "The file expands to an unsafe size and was rejected.")
            if required not in z.namelist():
                raise ConversionError(f"{label}_MALFORMED", f"The file is not a valid .{ext} file.")
    except zipfile.BadZipFile as e:
        raise ConversionError(f"{label}_MALFORMED", f"The file is not a valid .{ext} file.") from e


def _validate_image(path: Path, ext: str, max_pixels: int) -> None:
    want = "PNG" if ext == "png" else "JPEG"
    try:
        with Image.open(path) as img:  # header only; does not decode pixels
            if img.format != want:
                raise ConversionError("IMAGE_TYPE_MISMATCH", f"The file content does not match its .{ext} extension.")
            w, h = img.size
            if w * h > max_pixels:
                raise ConversionError("IMAGE_TOO_LARGE", f"The image is too large ({w}x{h} pixels). Reduce its dimensions and try again.")
            img.verify()
    except ConversionError:
        raise
    except Image.DecompressionBombError as e:
        raise ConversionError("IMAGE_TOO_LARGE", "The image dimensions are too large.") from e
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise ConversionError("IMAGE_MALFORMED", "The image file is corrupt or not a valid image.") from e


MAX_PDF_PAGES = 300


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


def validate_source(path: Path, ext: str, *, max_bytes: int, max_pixels: int = 50_000_000) -> SourceInfo:
    """Raises ConversionError (user-safe message, stable code) if the file is not an acceptable source document."""
    ext = ext.lower()
    if ext not in SUPPORTED:
        raise ConversionError("UNSUPPORTED_TYPE", f"'.{ext}' files are not supported. Use .docx, .xlsx, .jpg, .jpeg, .png or .pdf.")
    size = path.stat().st_size
    if size == 0:
        raise ConversionError("EMPTY_FILE", "The file is empty.")
    if size > max_bytes:
        raise ConversionError("FILE_TOO_LARGE", f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit.")
    mime, required = SUPPORTED[ext]
    if ext == "pdf":
        _validate_pdf_source(path)
    elif required:
        _validate_zip(path, ext, required)
    else:
        _validate_image(path, ext, max_pixels)
    return SourceInfo(ext=ext, mime=mime)


@dataclass
class PdfInfo:
    pages: int
    size: int
    sha256: str


def validate_pdf(path: Path) -> PdfInfo:
    """A generated PDF must be readable, unencrypted and have at least one page."""
    try:
        with open(path, "rb") as f:
            if f.read(5) != b"%PDF-":
                raise ValueError("missing header")
        r = PdfReader(str(path), strict=False)
        if r.is_encrypted:
            raise ValueError("encrypted")
        pages = len(r.pages)
        if pages < 1:
            raise ValueError("no pages")
        r.pages[0].mediabox  # forces a real parse of the first page
    except Exception as e:
        raise ConversionError("OUTPUT_INVALID", "The PDF could not be verified. Please try again.", retryable=True) from e
    return PdfInfo(pages=pages, size=path.stat().st_size, sha256=sha256_file(path))
