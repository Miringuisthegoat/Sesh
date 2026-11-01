"""Run the right converter for a validated source file and verify the PDF it produced."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .engine.converters.docx_conv import convert_docx
from .engine.converters.images import convert_image
from .engine.converters.xlsx_conv import convert_xlsx
from .engine.errors import ConversionError
from .validation import PdfInfo, validate_pdf, validate_source

CONVERTERS = {"docx": convert_docx, "xlsx": convert_xlsx, "jpg": convert_image, "jpeg": convert_image, "png": convert_image}


@dataclass
class ConversionResult:
    pdf: PdfInfo
    warnings: list[str] = field(default_factory=list)


def convert_file(src: Path, ext: str, dst: Path, *, max_bytes: int, max_pixels: int) -> ConversionResult:
    """Validate again (never trust earlier checks), convert, then verify the output PDF."""
    validate_source(src, ext, max_bytes=max_bytes, max_pixels=max_pixels)
    try:
        warnings = CONVERTERS[ext.lower()](src, dst)
    except ConversionError:
        raise
    except MemoryError as e:
        raise ConversionError("OUT_OF_MEMORY", "The document is too complex to convert.") from e
    except Exception as e:  # converter bug on a hostile/odd file: not worth retrying
        raise ConversionError("CONVERSION_FAILED", "The document could not be converted. It may use features that are not supported.") from e
    return ConversionResult(pdf=validate_pdf(dst), warnings=list(dict.fromkeys(warnings))[:20])
