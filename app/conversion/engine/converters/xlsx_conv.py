"""XLSX -> PDF using openpyxl + ReportLab. Formulas show CACHED values only (openpyxl does not calculate)."""
import datetime as dt
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter, range_boundaries
from openpyxl.utils.exceptions import InvalidFileException
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Table, TableStyle

from ..errors import ConversionError
from ..fonts import body_font, register_fonts

MAX_CELLS = 300_000
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MARGIN = 36
MIN_FONT = 5.0


def _check_zip(src: Path):
    with open(src, "rb") as f:
        head = f.read(8)
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        raise ConversionError("XLSX_ENCRYPTED_OR_LEGACY", "File is password-protected or a legacy Office format, which is not supported.")
    if not zipfile.is_zipfile(src):
        raise ConversionError("XLSX_MALFORMED", "File is not a valid .xlsx workbook.")
    with zipfile.ZipFile(src) as z:
        if "xl/workbook.xml" not in z.namelist():
            raise ConversionError("XLSX_MALFORMED", "File is not a valid .xlsx workbook.")
        if sum(i.file_size for i in z.infolist()) > MAX_UNCOMPRESSED:
            raise ConversionError("XLSX_TOO_LARGE_UNCOMPRESSED", "Workbook expands to an unsafe size.")


def _fmt(cell) -> str:
    v = cell.value
    if v is None:
        return ""
    nf = cell.number_format or "General"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        if isinstance(v, dt.datetime):
            return v.strftime("%Y-%m-%d %H:%M") if (v.hour or v.minute) else v.strftime("%Y-%m-%d")
        return v.strftime("%Y-%m-%d") if isinstance(v, dt.date) else v.strftime("%H:%M")
    if isinstance(v, (int, float)):
        decimals = sum(1 for ch in (nf.split(".")[1].split(";")[0] if "." in nf else "") if ch in "0#") if "." in nf else 0
        if "%" in nf:
            return f"{v * 100:,.{decimals}f}%"
        sym = "$" if "$" in nf else ""
        if "#,##" in nf or sym:
            return f"{sym}{v:,.{decimals}f}"
        if nf == "General":
            return f"{v:.10g}"
        return f"{v:.{decimals}f}"
    return str(v)


def _cell_markup(cell, text: str) -> str:
    t = escape(text).replace("\n", "<br/>")
    f = cell.font
    if f and f.bold:
        t = f"<b>{t}</b>"
    if f and f.italic:
        t = f"<i>{t}</i>"
    if f and f.color is not None and isinstance(f.color.rgb, str) and len(f.color.rgb) == 8 and f.color.rgb[2:] != "000000":
        t = f'<font color="#{f.color.rgb[2:]}">{t}</font>'
    return t


def _fill(cell):
    fl = cell.fill
    if fl and fl.fill_type == "solid" and fl.fgColor is not None and isinstance(fl.fgColor.rgb, str) and len(fl.fgColor.rgb) == 8:
        return colors.HexColor("#" + fl.fgColor.rgb[2:])
    return None


def _sheet_table(ws, ws_vals, avail_w, warnings):
    if ws.print_area:
        area = ws.print_area if isinstance(ws.print_area, str) else ws.print_area[0]
        rng = area.split(",")[0].split("!")[-1].replace("$", "")
        c1, r1, c2, r2 = range_boundaries(rng)
    else:
        c1, r1, c2, r2 = 1, 1, ws.max_column, ws.max_row
    if (r2 - r1 + 1) * (c2 - c1 + 1) > MAX_CELLS:
        raise ConversionError("XLSX_TOO_LARGE", "Sheet has too many cells to convert safely.")
    rows = [r for r in range(r1, r2 + 1) if not ws.row_dimensions[r].hidden]
    cols = [c for c in range(c1, c2 + 1) if not ws.column_dimensions[get_column_letter(c)].hidden]
    # trim fully empty trailing rows/cols when no explicit print area
    def has_val(r, c): return ws_vals.cell(row=r, column=c).value not in (None, "") or ws.cell(row=r, column=c).value not in (None, "")
    if not ws.print_area:
        while rows and not any(has_val(rows[-1], c) for c in cols):
            rows.pop()
        while cols and not any(has_val(r, cols[-1]) for r in rows):
            cols.pop()
    if not rows or not cols:
        return None
    widths = []
    for c in cols:
        w = ws.column_dimensions[get_column_letter(c)].width
        widths.append((w if w else 8.43) * 5.6 + 6)  # excel chars -> points (approx)
    total = sum(widths)
    scale = 1.0
    if total > avail_w:
        scale = avail_w / total
        widths = [w * scale for w in widths]
        if scale < 0.6:
            warnings.append(f"Sheet '{ws.title}' is very wide; scaled to {scale:.0%} to fit one page width (text may be small).")
    fsize = max(MIN_FONT, 9 * scale if scale < 1 else 9)
    style = ParagraphStyle("x", fontName=body_font(), fontSize=fsize, leading=fsize * 1.2)
    rstyle = ParagraphStyle("xr", parent=style, alignment=2)
    cstyle = ParagraphStyle("xc", parent=style, alignment=1)
    ridx = {r: i for i, r in enumerate(rows)}
    cidx = {c: i for i, c in enumerate(cols)}
    data, cmds = [], [("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                      ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]
    show_grid = bool(ws.print_options.gridLines)
    if show_grid:
        cmds.append(("GRID", (0, 0), (-1, -1), 0.4, colors.grey))
    merged_skip = set()
    for mr in ws.merged_cells.ranges:
        mc1, mr1, mc2, mr2 = mr.min_col, mr.min_row, mr.max_col, mr.max_row
        if mr1 in ridx and mc1 in cidx and mr2 in ridx and mc2 in cidx:
            cmds.append(("SPAN", (cidx[mc1], ridx[mr1]), (cidx[mc2], ridx[mr2])))
            for rr in range(mr1, mr2 + 1):
                for cc in range(mc1, mc2 + 1):
                    if (rr, cc) != (mr1, mc1):
                        merged_skip.add((rr, cc))
    for r in rows:
        line = []
        for c in cols:
            if (r, c) in merged_skip:
                line.append("")
                continue
            cell = ws_vals.cell(row=r, column=c)
            fcell = ws.cell(row=r, column=c)
            text = _fmt(cell)
            if text == "" and isinstance(fcell.value, str) and fcell.value.startswith("="):
                text = ""
                warnings.append("Some formula cells have no cached value (file was never calculated in Excel); they print blank.")
            if isinstance(cell.value, str) and cell.value.startswith("#"):
                pass
            st = style
            h = cell.alignment.horizontal if cell.alignment else None
            if h == "center" or h == "centerContinuous":
                st = cstyle
            elif h == "right" or (h is None and isinstance(cell.value, (int, float)) and not isinstance(cell.value, bool)):
                st = rstyle
            line.append(Paragraph(_cell_markup(cell, text), st) if text else "")
            fl = _fill(cell)
            if fl is not None:
                cmds.append(("BACKGROUND", (cidx[c], ridx[r]), (cidx[c], ridx[r]), fl))
        data.append(line)
    repeat = 0
    if ws.print_title_rows:
        try:
            a, b = [int(x) for x in ws.print_title_rows.replace("$", "").split(":")]
            if a == rows[0]:
                repeat = sum(1 for r in rows if a <= r <= b)
        except Exception:
            pass
    row_heights = [None] * len(rows)
    tbl = Table(data, colWidths=widths, repeatRows=repeat, rowHeights=row_heights)
    tbl.setStyle(TableStyle(cmds))
    if ws._charts or ws._images:
        warnings.append(f"Sheet '{ws.title}' contains charts/images, which are not rendered.")
    return tbl, ws.page_setup.orientation == "landscape"


def convert_xlsx(src: Path, dst: Path) -> list[str]:
    _check_zip(src)
    try:
        wb_vals = load_workbook(str(src), data_only=True)
        wb = load_workbook(str(src), data_only=False)
    except (InvalidFileException, KeyError, ValueError, zipfile.BadZipFile) as e:
        raise ConversionError("XLSX_MALFORMED", "File is not a valid .xlsx workbook.") from e
    except Exception as e:
        raise ConversionError("XLSX_MALFORMED", "The workbook could not be read.") from e
    register_fonts()
    warnings: list[str] = []
    sheets = []
    for ws in wb.worksheets:
        if ws.sheet_state != "visible":
            continue
        sheets.append(ws)
    if not sheets:
        raise ConversionError("XLSX_EMPTY", "Workbook has no visible sheets.")

    out = BaseDocTemplate(str(dst), pagesize=A4, title=src.stem, leftMargin=MARGIN, rightMargin=MARGIN, topMargin=MARGIN, bottomMargin=MARGIN)
    templates = {}
    for name, size in (("p", A4), ("l", landscape(A4))):
        w, h = size
        def mk(sheet_label_getter, w=w, h=h):
            def on_page(canvas, d):
                canvas.saveState()
                canvas.setFont(body_font(), 8)
                canvas.setFillColor(colors.grey)
                canvas.drawString(MARGIN, 18, sheet_label_getter())
                canvas.drawRightString(w - MARGIN, 18, f"Page {d.page}")
                canvas.restoreState()
            return on_page
        templates[name] = PageTemplate(id=name, pagesize=size, frames=[Frame(MARGIN, MARGIN, w - 2 * MARGIN, h - 2 * MARGIN, id=name)], onPage=mk(lambda: current["name"]))
    current = {"name": ""}
    out.addPageTemplates(list(templates.values()))

    from reportlab.platypus import NextPageTemplate
    flow, first = [], True
    for ws in sheets:
        landscape_mode = ws.page_setup.orientation == "landscape"
        avail = (landscape(A4)[0] if landscape_mode else A4[0]) - 2 * MARGIN
        res = _sheet_table(ws, wb_vals[ws.title], avail, warnings)
        if res is None:
            continue
        tbl, is_land = res
        key = "l" if is_land else "p"
        if not first:
            flow.append(NextPageTemplate(key))
            flow.append(PageBreak())
        else:
            flow.append(NextPageTemplate(key))
            out.pageTemplates = [templates[key]] + [t for t in out.pageTemplates if t.id != key]
        first = False
        flow.append(Paragraph(f"<b>{escape(ws.title)}</b>", ParagraphStyle("t", fontName=body_font(bold=True), fontSize=10, spaceAfter=4)))
        flow.append(tbl)
    if first:
        raise ConversionError("XLSX_EMPTY", "Workbook has no printable content.")
    # sheet name in footer: tracked via a Paragraph-independent marker
    names = [ws.title for ws in sheets]
    current["name"] = ", ".join(names)[:80]
    try:
        out.build(flow)
    except Exception as e:
        raise ConversionError("XLSX_RENDER_FAILED", "The workbook could not be rendered to PDF.") from e
    return sorted(set(warnings))
