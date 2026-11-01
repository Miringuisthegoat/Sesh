# Phase 1: conversion + local-print proof of concept

Go/no-go milestone. Answers two questions: (1) is DOCX/XLSX/image -> PDF good enough WITHOUT LibreOffice, using python-docx / openpyxl / Pillow / ReportLab? (2) can we silently print a PDF to the office printer from Python on Windows?

## Run
See the PowerShell commands delivered with this zip. Outputs: `poc/results/report.md`, `poc/results/*.pdf`, `poc/results/png/*.png`.

**Your real documents are the actual test.** Copy 5-10 files the office really prints into `poc/corpus/real/` and re-run `python -m poc.run_poc`.

## Windows printing
1. Download the portable SumatraPDF (64-bit) from https://www.sumatrapdfreader.org and save as `poc\bin\SumatraPDF.exe` (or set `SUMATRA_PATH`).
2. `python -m poc.print_test --list` shows exact printer names.
3. `python -m poc.print_test --printer "<exact name>"` prints a test page (A4, 100 mm ruler line, 10 mm border).
Exact command used: `SumatraPDF.exe -print-to "<printer>" -silent -print-settings "1x,paper=A4" file.pdf`.
Success means ONLY "Windows accepted it into the print queue". Never treat it as "paper came out".
Licence: SumatraPDF is GPLv3. Fine for you to install; get a licence review before redistributing it inside a product.
"Microsoft Print to PDF" is not a real test: it opens a save dialog and ignores printer settings.

## Known limitations (be honest with users)
DOCX: re-flowed, not rendered. Page breaks WILL differ from Word on complex files. Only the first section's page size/margins/header/footer apply. Not supported: charts, text boxes, shapes, SmartArt, floating-image positioning, columns, tracked changes, header/footer fields (a plain page number is added), numbered lists (shown with dashes), merged table cells (approximated), per-run fonts (a single substitute font family is used).
XLSX: formulas print CACHED values only (blank if the file was never calculated in Excel); charts/images skipped; hidden sheets/rows/cols skipped; number/date formats approximated; print areas, landscape, repeat title rows and merged cells are honoured; fit-to-page is approximated by scaling to page width (30 columns scales to ~22%, tiny text); no column-wise page splitting; footer shows the sheet list, not the current sheet.
Images: JPEG/PNG only; EXIF rotation applied; alpha flattened to white; 50 MP limit.
Fonts: Calibri/Arial from C:\Windows\Fonts if present (not bundled), else Carlito/Liberation/DejaVu, else Helvetica (no non-Latin glyphs). On the Linux cloud server, expect metric-compatible substitutes and slightly different line breaks.
Performance: ~5000-row sheet (93 pages) took ~9 s in the sandbox, so conversion belongs in a background worker (Phase 3).
