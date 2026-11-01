from pathlib import Path
from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from ..errors import ConversionError

MAX_PIXELS = 50_000_000  # decompression-bomb guard
Image.MAX_IMAGE_PIXELS = MAX_PIXELS
MARGIN = 36  # points (0.5 inch)


def convert_image(src: Path, dst: Path, page_size=A4) -> list[str]:
    warnings: list[str] = []
    try:
        with Image.open(src) as probe:
            probe.verify()
        img = Image.open(src)
        img.load()
    except Image.DecompressionBombError as e:
        raise ConversionError("IMAGE_TOO_LARGE", f"Image dimensions exceed the {MAX_PIXELS:,} pixel limit.") from e
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as e:
        raise ConversionError("IMAGE_MALFORMED", "The image file is corrupt or not a valid image.") from e
    if img.format not in ("JPEG", "PNG"):
        raise ConversionError("IMAGE_TYPE_MISMATCH", f"File content is {img.format}, expected JPEG or PNG.")
    img = ImageOps.exif_transpose(img)
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    page = landscape(page_size) if w > h else page_size
    pw, ph = page
    scale = min((pw - 2 * MARGIN) / w, (ph - 2 * MARGIN) / h)
    dw, dh = w * scale, h * scale
    c = canvas.Canvas(str(dst), pagesize=page)
    c.drawImage(ImageReader(img), (pw - dw) / 2, (ph - dh) / 2, dw, dh)
    c.showPage()
    c.save()
    if scale > 1:
        dpi = 72 / scale
        if dpi < 150:
            warnings.append(f"Low resolution: image prints at about {dpi:.0f} DPI.")
    return warnings
