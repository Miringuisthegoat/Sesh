import logging
import os
import threading

from .config import MUTEX_NAME, data_dir
from .logging_setup import setup_logging

log = logging.getLogger("sesh.agent")
_mutex = None  # keep a reference so the mutex lives as long as the process


def _single_instance() -> bool:
    global _mutex
    if os.name != "nt":
        return True
    import win32api
    import win32event
    import winerror
    _mutex = win32event.CreateMutex(None, False, MUTEX_NAME)
    return win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS


def main() -> None:
    setup_logging()
    if not _single_instance():
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "Sesh Agent is already running. Look for its icon near the clock.", "Sesh Agent", 0x40)
        return
    from .agent_service import AgentService
    from .cloud_client import CloudClient
    from .local_database import LocalDatabase
    from .printer_manager import default_printer_backend
    from .secrets_store import CredentialStore, default_protector
    from .ui import Ui

    db = LocalDatabase(data_dir() / "agent.db")
    service = AgentService(db, CredentialStore(db, default_protector()), default_printer_backend(),
                           lambda url, cred: CloudClient(url, cred))
    log.info("Sesh Agent started (state: %s)", service.state.value)
    threading.Thread(target=service.run_forever, name="heartbeat", daemon=True).start()
    from .job_journal import JobJournal
    from .job_processor import JobProcessor
    from .print_submitter import FakeSubmitter, SumatraSubmitter
    submitter = FakeSubmitter() if os.environ.get("SESH_AGENT_FAKE_PRINTERS") == "1" else SumatraSubmitter()
    processor = JobProcessor(service, JobJournal(db, default_protector()), submitter)
    threading.Thread(target=processor.run_forever, name="jobs", daemon=True).start()
    Ui(service).run()
    log.info("Sesh Agent stopped")


if __name__ == "__main__":
    main()
