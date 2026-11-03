"""Write-ahead record of what this computer did with each job, so a crash or restart never causes a blind reprint.

Stages, in order:  CLAIMED -> DOWNLOADING -> DOWNLOADED -> SUBMITTING -> SUBMITTED -> DONE
SUBMITTING is written BEFORE the print command runs. If the process dies at that stage nobody knows whether Windows got
the job, so recovery reports UNKNOWN and never prints again. The claim token is stored DPAPI-protected, like the credential.
"""
from __future__ import annotations

import base64
import json
import logging

from .local_database import LocalDatabase
from .secrets_store import Protector

log = logging.getLogger(__name__)
STAGES = ("CLAIMED", "DOWNLOADING", "DOWNLOADED", "SUBMITTING", "SUBMITTED", "DONE")


class JobJournal:
    def __init__(self, db: LocalDatabase, protector: Protector):
        self._db, self._p = db, protector

    def open_job(self, job_id: str, token: str, printer: str | None) -> None:
        blob = base64.b64encode(self._p.protect(token.encode())).decode()
        self._db.job_save(job_id, "CLAIMED", printer, json.dumps({"token": blob}))

    def advance(self, job_id: str, stage: str) -> None:
        if stage not in STAGES:
            raise ValueError(stage)
        self._db.job_save(job_id, stage)

    def finish(self, job_id: str) -> None:
        self._db.job_save(job_id, "DONE")
        self._db.jobs_prune()

    def stage(self, job_id: str) -> str | None:
        rec = self._db.job_get(job_id)
        return rec["stage"] if rec else None

    def token(self, job_id: str) -> str | None:
        rec = self._db.job_get(job_id)
        try:
            return self._p.unprotect(base64.b64decode(json.loads(rec["detail"])["token"])).decode() if rec else None
        except Exception:  # different Windows user/PC or corrupt: the job can no longer be reported on
            log.warning("job token could not be decrypted")
            return None

    def unfinished(self) -> list[dict]:
        return self._db.jobs_not_in("DONE")
