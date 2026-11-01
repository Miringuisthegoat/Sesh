"""DOCX -> PDF using python-docx + ReportLab Platypus.

This RE-FLOWS content; it does not render Word layout. See docs/LIMITATIONS in poc/README.md.
"""
import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph as DocxParagraph
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph,
                                Spacer, Table as RLTable, TableStyle, Image as RLImage, KeepTogether)

from ..errors import ConversionError
from ..fonts import body_font, register_fonts

MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_PARAGRAPHS = 50_000
EMU_PER_PT = 12700
ALIGN = {0: TA_LEFT, 1: TA_CENTER, 2: TA_RIGHT, 3: TA_JUSTIFY}
HEADING_SIZES = {"Title": 24, "Heading 1": 18, "Heading 2": 15, "Heading 3": 13, "Heading 4": 12}


def _check_zip(src: Path):
    with open(src, "rb") as f:
        head = f.read(8)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise ConversionError("DOCX_ENCRYPTED_OR_LEGACY", "File is password-protected or a legacy Office format, which is not supported.")
    if not zipfile.is_zipfile(src):
        raise ConversionError("DOCX_MALFORMED", "File is not a valid .docx document.")
    with zipfile.ZipFile(src) as z:
        if "word/document.xml" not in z.namelist():
            raise ConversionError("DOCX_MALFORMED", "File is not a valid .docx document.")
        if sum(i.file_size for i in z.infolist()) > MAX_UNCOMPRESSED:
            raise ConversionError("DOCX_TOO_LARGE_UNCOMPRESSED", "Document expands to an unsafe size.")


def _run_markup(run) -> str:
    text = run.text
    if not text:
        return ""
    t = escape(text).replace("\t", "&nbsp;&nbsp;&nbsp;&nbsp;")
    f = run.font
    if f.bold:
        t = f"<b>{t}</b>"
    if f.italic:
        t = f"<i>{t}</i>"
    if f.underline:
        t = f"<u>{t}</u>"
    if f.strike:
        t = f"<strike>{t}</strike>"
    attrs = ""
    if f.size:
        attrs += f' size="{f.size.pt:.1f}"'
    try:
        if f.color is not None and f.color.rgb is not None:
            attrs += f' color="#{f.color.rgb}"'
    except Exception:
        pass
    return f"<font{attrs}>{t}</font>" if attrs else t


def _run_images(run, doc, max_w):
    out = []
    for blip in run._r.findall(".//" + qn("a:blip")):
        rid = blip.get(qn("r:embed"))
        if not rid or rid not in doc.part.related_parts:
            continue
        data = doc.part.related_parts[rid].blob
        try:
            with Image.open(io.BytesIO(data)) as im:
                im.verify()
            with Image.open(io.BytesIO(data)) as im:
                w, h = im.size
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                buf = io.BytesIO()
                im.save(buf, "PNG")
        except Exception:
            continue
        w_pt = h_pt = None
        ext = run._r.find(".//" + qn("wp:extent"))
        if ext is not None:
            w_pt, h_pt = int(ext.get("cx")) / EMU_PER_PT, int(ext.get("cy")) / EMU_PER_PT
        if not w_pt:
            w_pt, h_pt = w * 0.75, h * 0.75
        if w_pt > max_w:
            h_pt *= max_w / w_pt
            w_pt = max_w
        buf.seek(0)
        out.append(RLImage(buf, width=w_pt, height=h_pt))
    return out


def _has_page_break(run) -> bool:
    return any(br.get(qn("w:type")) == "page" for br in run._r.findall(qn("w:br")))


def _para_style(p: DocxParagraph, cache: dict) -> ParagraphStyle:
    sname = p.style.name if p.style is not None else "Normal"
    size = HEADING_SIZES.get(sname)
    bold = size is not None
    if size is None:
        size = 11
        try:
            if p.style.font.size:
                size = p.style.font.size.pt
        except Exception:
            pass
    pf = p.paragraph_format
    align = ALIGN.get(p.alignment if p.alignment is not None else 0, TA_LEFT)
    left = pf.left_indent.pt if pf.left_indent else 0
    if sname.startswith("List"):
        left = max(left, 18)
    key = (sname, size, align, left)
    if key not in cache:
        cache[key] = ParagraphStyle(
            f"s{len(cache)}", fontName=body_font(bold=bold), fontSize=size, leading=size * 1.25,
            alignment=align, leftIndent=left,
            spaceBefore=(pf.space_before.pt if pf.space_before else (8 if bold else 0)),
            spaceAfter=(pf.space_after.pt if pf.space_after else (4 if not bold else 6)),
        )
    return cache[key]


def _build_para(p, doc, max_w, cache, flow, warnings):
    if p._p.findall(".//" + qn("w:drawing")) and not p._p.findall(".//" + qn("a:blip")):
        warnings.append("A drawing/shape/chart was skipped (not supported).")
    if p._p.findall(".//" + qn("w:txbxContent")):
        warnings.append("A text box was skipped (not supported).")
    if p.paragraph_format.page_break_before:
        flow.append(PageBreak())
    markup = ""
    pending_images = []
    for run in p.runs:
        markup += _run_markup(run)
        pending_images += _run_images(run, doc, max_w)
        if _has_page_break(run):
            if markup.strip():
                flow.append(Paragraph(markup, _para_style(p, cache)))
                markup = ""
            flow.extend(pending_images)
            pending_images = []
            flow.append(PageBreak())
    if p.style is not None and p.style.name.startswith("List Bullet") and markup:
        markup = "&bull;&nbsp;&nbsp;" + markup
    elif p.style is not None and p.style.name.startswith("List Number") and markup:
        markup = "&ndash;&nbsp;&nbsp;" + markup
        warnings.append("Numbered lists are shown with dashes, not numbers.")
    if markup.strip():
        flow.append(Paragraph(markup, _para_style(p, cache)))
    elif not pending_images and not p.runs:
        flow.append(Spacer(1, 8))
    flow.extend(pending_images)


def _build_table(t: Table, doc, max_w, cache, warnings):
    rows = []
    ncols = max((len(r.cells) for r in t.rows), default=0)
    if ncols == 0:
        return None
    cell_style = ParagraphStyle("cell", fontName=body_font(), fontSize=9.5, leading=12)
    for r in t.rows:
        row, seen = [], set()
        for c in r.cells:
            if id(c._tc) in seen:  # merged cell repeated by python-docx
                row.append("")
                continue
            seen.add(id(c._tc))
            parts = []
            for p in c.paragraphs:
                m = "".join(_run_markup(run) for run in p.runs)
                if m.strip():
                    parts.append(Paragraph(m, cell_style))
            row.append(parts or "")
        row += [""] * (ncols - len(row))
        rows.append(row)
    try:
        widths = [c.width.pt if c.width else None for c in t.columns]
    except Exception:
        widths = [None] * ncols
    if None in widths or sum(widths) <= 0:
        widths = [max_w / ncols] * ncols
    total = sum(widths)
    if total > max_w:
        widths = [w * max_w / total for w in widths]
    tbl = RLTable(rows, colWidths=widths, repeatRows=0)
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    if any(len(set(id(c._tc) for c in r.cells)) < len(r.cells) for r in t.rows):
        warnings.append("Merged table cells are approximated (spans not reproduced).")
    return tbl


def convert_docx(src: Path, dst: Path) -> list[str]:
    _check_zip(src)
    try:
        doc = Document(str(src))
    except (PackageNotFoundError, KeyError, ValueError) as e:
        raise ConversionError("DOCX_MALFORMED", "File is not a valid .docx document.") from e
    except Exception as e:
        raise ConversionError("DOCX_MALFORMED", "The document could not be read.") from e

    register_fonts()
    warnings: list[str] = []
    sec = doc.sections[0]
    page_w = sec.page_width.pt if sec.page_width else A4[0]
    page_h = sec.page_height.pt if sec.page_height else A4[1]
    lm = sec.left_margin.pt if sec.left_margin is not None else 72
    rm = sec.right_margin.pt if sec.right_margin is not None else 72
    tm = sec.top_margin.pt if sec.top_margin is not None else 72
    bm = sec.bottom_margin.pt if sec.bottom_margin is not None else 72
    max_w = page_w - lm - rm
    if len(doc.sections) > 1:
        warnings.append("Only the first section's page size, margins and header/footer are applied to the whole document.")

    header_text = " ".join(p.text for p in sec.header.paragraphs if p.text.strip())
    footer_text = " ".join(p.text for p in sec.footer.paragraphs if p.text.strip())
    if sec.footer._element.findall(".//" + qn("w:fldSimple")) or sec.footer._element.findall(".//" + qn("w:instrText")):
        warnings.append("Header/footer fields (e.g. page numbers) are not evaluated; a plain page number is added instead.")
    if sec.header.is_linked_to_previous is False and sec.different_first_page_header_footer:
        warnings.append("Different first-page header/footer is not reproduced.")

    def on_page(canvas, d):
        canvas.saveState()
        canvas.setFont(body_font(), 9)
        canvas.setFillColor(colors.grey)
        if header_text:
            canvas.drawString(lm, page_h - tm / 2, header_text[:150])
        label = (footer_text[:110] + "   " if footer_text else "") + f"Page {d.page}"
        canvas.drawRightString(page_w - rm, bm / 2, label)
        canvas.restoreState()

    out = BaseDocTemplate(str(dst), pagesize=(page_w, page_h), leftMargin=lm, rightMargin=rm, topMargin=tm, bottomMargin=bm,
                          title=src.stem, author="")
    out.addPageTemplates([PageTemplate(id="main", frames=[Frame(lm, bm, page_w - lm - rm, page_h - tm - bm, id="f")], onPage=on_page)])

    flow, cache, count = [], {}, 0
    for child in doc.element.body.iterchildren():
        count += 1
        if count > MAX_PARAGRAPHS:
            raise ConversionError("DOCX_TOO_COMPLEX", "Document has too many elements to convert safely.")
        if child.tag == qn("w:p"):
            _build_para(DocxParagraph(child, doc), doc, max_w, cache, flow, warnings)
        elif child.tag == qn("w:tbl"):
            tbl = _build_table(Table(child, doc), doc, max_w, cache, warnings)
            if tbl is not None:
                flow.append(tbl)
                flow.append(Spacer(1, 8))
    if not flow:
        flow.append(Spacer(1, 12))
        warnings.append("Document contains no printable content.")
    try:
        out.build(flow)
    except Exception as e:
        raise ConversionError("DOCX_RENDER_FAILED", "The document could not be rendered to PDF.", retryable=False) from e
    return sorted(set(warnings))
