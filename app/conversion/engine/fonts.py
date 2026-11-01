"""Font discovery. ReportLab needs TTF files for Unicode text; we never bundle Microsoft fonts."""
import os
from pathlib import Path
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

_CANDIDATES = [
    # (regular, bold, italic, bolditalic) searched in these dirs
    ("calibri.ttf", "calibrib.ttf", "calibrii.ttf", "calibriz.ttf"),
    ("arial.ttf", "arialbd.ttf", "ariali.ttf", "arialbi.ttf"),
    ("Carlito-Regular.ttf", "Carlito-Bold.ttf", "Carlito-Italic.ttf", "Carlito-BoldItalic.ttf"),
    ("LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Italic.ttf", "LiberationSans-BoldItalic.ttf"),
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf"),
]
_DIRS = [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
    "/usr/share/fonts", "/usr/local/share/fonts", str(Path.home() / ".fonts"),
]
_FAMILY = None


def _find(name: str):
    for d in _DIRS:
        if os.path.isdir(d):
            for root, _, files in os.walk(d):
                if name in files:
                    return os.path.join(root, name)
    return None


def register_fonts() -> str:
    """Register the first available family as 'Body' (+ -Bold/-Italic/-BoldItalic). Returns the family file used."""
    global _FAMILY
    if _FAMILY:
        return _FAMILY
    for files in _CANDIDATES:
        paths = [_find(f) for f in files]
        if paths[0]:
            reg, bold, ital, bi = [p or paths[0] for p in paths]
            for suffix, p in (("", reg), ("-Bold", bold), ("-Italic", ital), ("-BoldItalic", bi)):
                pdfmetrics.registerFont(TTFont("Body" + suffix, p))
            pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body-Italic", boldItalic="Body-BoldItalic")
            _FAMILY = files[0]
            return _FAMILY
    # Last resort: built-in Helvetica (no non-Latin glyphs)
    pdfmetrics.registerFontFamily("Helvetica", normal="Helvetica", bold="Helvetica-Bold", italic="Helvetica-Oblique", boldItalic="Helvetica-BoldOblique")
    _FAMILY = "Helvetica"
    return _FAMILY


def body_font(bold=False, italic=False) -> str:
    fam = register_fonts()
    if fam == "Helvetica":
        return {(0, 0): "Helvetica", (1, 0): "Helvetica-Bold", (0, 1): "Helvetica-Oblique", (1, 1): "Helvetica-BoldOblique"}[(int(bold), int(italic))]
    return "Body" + ("-Bold" if bold and not italic else "-Italic" if italic and not bold else "-BoldItalic" if bold and italic else "")
