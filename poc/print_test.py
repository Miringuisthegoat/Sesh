"""Windows print test (run ON the office PC).

  python -m poc.print_test --list
  python -m poc.print_test --printer "Exact Printer Name"            # prints a generated test page
  python -m poc.print_test --printer "Exact Printer Name" --file poc\\results\\docx_simple.pdf --copies 1

Uses SumatraPDF command line. Not bundled: download the portable build and either put SumatraPDF.exe in poc\\bin\\
or set the SUMATRA_PATH environment variable. SumatraPDF is GPLv3: check licence terms before bundling it in a product.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SUMATRA_CANDIDATES = [
    os.environ.get("SUMATRA_PATH", ""),
    str(ROOT / "bin" / "SumatraPDF.exe"),
    r"C:\Program Files\SumatraPDF\SumatraPDF.exe",
    r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
    str(Path(os.environ.get("LOCALAPPDATA", "")) / "SumatraPDF" / "SumatraPDF.exe"),
]


def ps(command: str, timeout: int = 30) -> str:
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300] or "PowerShell failed")
    return r.stdout.strip()


def list_printers() -> list[dict]:
    out = ps("Get-Printer | Select-Object Name,DriverName,PortName,PrinterStatus,WorkOffline | ConvertTo-Json -Compress")
    data = json.loads(out) if out else []
    return data if isinstance(data, list) else [data]


def find_sumatra() -> str | None:
    for c in SUMATRA_CANDIDATES:
        if c and Path(c).is_file():
            return c
    return shutil.which("SumatraPDF")


def make_test_pdf(path: Path, printer: str):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    c = canvas.Canvas(str(path), pagesize=A4)
    w, h = A4
    c.rect(10 * mm, 10 * mm, w - 20 * mm, h - 20 * mm)
    c.setFont("Helvetica-Bold", 20); c.drawString(20 * mm, h - 30 * mm, "Remote printing PoC - test page")
    c.setFont("Helvetica", 11)
    c.drawString(20 * mm, h - 42 * mm, f"Printer: {printer}")
    c.drawString(20 * mm, h - 50 * mm, f"Generated: {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    c.drawString(20 * mm, h - 62 * mm, "The line below must measure 100 mm with a ruler (checks scaling):")
    c.line(20 * mm, h - 70 * mm, 120 * mm, h - 70 * mm)
    c.drawString(20 * mm, h - 85 * mm, "Border should sit 10 mm from each paper edge (checks clipping/margins).")
    c.save()


def print_pdf(pdf: Path, printer: str, copies: int = 1, paper: str | None = "A4", color: bool | None = None, landscape: bool | None = None):
    exe = find_sumatra()
    if not exe:
        raise SystemExit("SumatraPDF.exe not found. Put it in poc\\bin\\ or set SUMATRA_PATH.")
    settings = [f"{copies}x"]
    if paper: settings.append(f"paper={paper}")
    if color is True: settings.append("color")
    if color is False: settings.append("monochrome")
    if landscape is True: settings.append("landscape")
    if landscape is False: settings.append("portrait")
    cmd = [exe, "-print-to", printer, "-silent", "-print-settings", ",".join(settings), str(pdf)]
    print("Command:", cmd)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stderr or r.stdout).strip()[:300]


def queue_snapshot(printer: str) -> str:
    try:
        return ps(f"Get-PrintJob -PrinterName '{printer.replace(chr(39), chr(39) * 2)}' | Select-Object Id,DocumentName,JobStatus,TotalPages | Format-Table -AutoSize | Out-String") or "(print queue empty)"
    except Exception as e:
        return f"(could not read queue: {e})"


def main():
    if sys.platform != "win32":
        raise SystemExit("This script must run on Windows.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--printer")
    ap.add_argument("--file")
    ap.add_argument("--copies", type=int, default=1)
    ap.add_argument("--paper", default="A4")
    a = ap.parse_args()
    printers = list_printers()
    if a.list or not a.printer:
        for p in printers:
            print(f'- "{p["Name"]}"  driver={p["DriverName"]}  port={p["PortName"]}  status={p["PrinterStatus"]}  offline={p["WorkOffline"]}')
        print(f"\nSumatraPDF: {find_sumatra() or 'NOT FOUND'}")
        return
    match = next((p for p in printers if p["Name"] == a.printer), None)
    if not match:
        raise SystemExit(f'Printer "{a.printer}" not installed. Use --list for exact names.')
    if match.get("WorkOffline"):
        raise SystemExit("Windows reports this printer as offline. Fix that first.")
    pdf = Path(a.file) if a.file else ROOT / "results" / "printer_test_page.pdf"
    if not a.file:
        pdf.parent.mkdir(exist_ok=True); make_test_pdf(pdf, a.printer)
    code, msg = print_pdf(pdf, a.printer, copies=a.copies, paper=a.paper)
    print(f"SumatraPDF exit code: {code} {msg}")
    print("\nWindows print queue right now:\n" + queue_snapshot(a.printer))
    if code == 0:
        print("RESULT: submitted to the Windows print queue. This does NOT prove paper came out - check the printer.")
    else:
        print("RESULT: submission FAILED. Do not assume anything was printed.")
        sys.exit(2)


if __name__ == "__main__":
    main()
