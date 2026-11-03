"""Hand a verified PDF to Windows through SumatraPDF's command line. SumatraPDF is NOT bundled (decision 4): it is found via
SUMATRA_PATH, next to the program, in the data folder, or in Program Files.

Outcome rules (printing is a physical side effect):
* could not even start the program            -> PrintSubmitError   (definitely nothing was sent: job FAILED)
* program ran and exited with a non-zero code -> PrintSubmitError   (job FAILED; Sumatra reports before spooling)
* timeout or any other error after it started -> PrintOutcomeUncertain (job UNKNOWN; never retried automatically)
* exit code 0                                 -> accepted into the Windows print queue. NOT proof that paper came out.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import app_dir, data_dir

PAPER = {"A4": "A4", "A3": "A3", "Letter": "letter", "Legal": "legal"}  # names SumatraPDF understands
CREATE_NO_WINDOW = 0x08000000


class PrintSubmitError(Exception):
    """Nothing was sent to the printer. Message is safe to show to users."""


class PrintOutcomeUncertain(Exception):
    """The print command may or may not have reached Windows."""


class SimulatedCrash(BaseException):
    """Tests only: models the process dying (BaseException so no handler swallows it)."""


@dataclass(frozen=True)
class JobOptions:
    printer: str
    copies: int = 1
    paper_size: str = "A4"
    color: bool | None = None
    landscape: bool | None = None


def build_settings(o: JobOptions) -> str:
    parts = [f"{o.copies}x", f"paper={PAPER[o.paper_size]}"]
    if o.color is True:
        parts.append("color")
    elif o.color is False:
        parts.append("monochrome")
    if o.landscape is True:
        parts.append("landscape")
    elif o.landscape is False:
        parts.append("portrait")
    return ",".join(parts)


def build_command(exe: str, pdf: Path, o: JobOptions) -> list[str]:
    """List form (never a shell string): the printer name comes from the server and is never interpreted by a shell."""
    return [exe, "-print-to", o.printer, "-silent", "-print-settings", build_settings(o), str(pdf)]


def find_sumatra() -> str | None:
    candidates = [os.environ.get("SUMATRA_PATH", ""), str(app_dir() / "SumatraPDF.exe"), str(data_dir() / "bin" / "SumatraPDF.exe"),
                  r"C:\Program Files\SumatraPDF\SumatraPDF.exe", r"C:\Program Files (x86)\SumatraPDF\SumatraPDF.exe",
                  str(Path(os.environ.get("LOCALAPPDATA", "")) / "SumatraPDF" / "SumatraPDF.exe")]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return shutil.which("SumatraPDF")


class SumatraSubmitter:
    def __init__(self, exe: str | None = None, timeout: int = 90, runner=subprocess.run):
        self._exe, self.timeout, self._run = exe, timeout, runner  # timeout stays below the server's job lease

    def problem(self) -> str | None:
        """None if printing is possible, else a user-safe explanation (the agent then takes no jobs)."""
        self._exe = self._exe if self._exe and Path(self._exe).is_file() else find_sumatra()
        return None if self._exe else "SumatraPDF.exe was not found. Install it or set SUMATRA_PATH; no jobs will be taken."

    def submit(self, pdf: Path, opts: JobOptions) -> None:
        if not self._exe:
            raise PrintSubmitError("SumatraPDF.exe was not found.")
        kw = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}
        try:
            r = self._run(build_command(self._exe, pdf, opts), capture_output=True, text=True, timeout=self.timeout, **kw)
        except subprocess.TimeoutExpired as e:
            raise PrintOutcomeUncertain("The print program did not finish in time.") from e
        except OSError as e:  # could not be started at all
            raise PrintSubmitError("The print program could not be started.") from e
        except Exception as e:
            raise PrintOutcomeUncertain("The print program failed unexpectedly.") from e
        if r.returncode != 0:
            raise PrintSubmitError(f"The print program reported an error (code {r.returncode}).")


class FakeSubmitter:
    """Tests and SESH_AGENT_FAKE_PRINTERS=1 development: prints nothing."""
    def __init__(self, mode: str = "ok"):
        self.mode, self.calls = mode, []

    def problem(self) -> str | None:
        return None

    def submit(self, pdf: Path, opts: JobOptions) -> None:
        self.calls.append((opts, Path(pdf).read_bytes()[:5]))  # the file must exist and be a PDF at this moment
        if self.mode == "crash":
            raise SimulatedCrash()
        if self.mode == "fail":
            raise PrintSubmitError("fake failure")
        if self.mode == "uncertain":
            raise PrintOutcomeUncertain("fake uncertain")
