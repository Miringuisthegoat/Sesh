# PyInstaller spec: one-folder, windowed (no console). Run from the project root via build\build.ps1
a = Analysis(
    ["build/entry.py"],
    pathex=["."],
    hiddenimports=["pystray._win32", "win32print", "win32crypt", "win32event", "win32api", "winerror", "PIL.ImageDraw"],
    excludes=["flask", "sqlalchemy", "reportlab", "openpyxl", "docx", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SeshAgent", console=False, disable_windowed_traceback=True)
coll = COLLECT(exe, a.binaries, a.datas, name="SeshAgent")
