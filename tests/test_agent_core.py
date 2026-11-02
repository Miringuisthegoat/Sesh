"""Windows-free tests for the Sesh Agent core (no network, no pywin32, no Tk)."""
import logging
import sqlite3
from urllib.parse import urlsplit

import pytest

pytest.importorskip("requests")
import requests  # noqa: E402

from office_agent import backoff  # noqa: E402
from office_agent.agent_service import AgentService, State  # noqa: E402
from office_agent.cloud_client import CloudClient, Rejected, Unauthorized, Unavailable  # noqa: E402
from office_agent.config import normalize_server_url  # noqa: E402
from office_agent.local_database import LocalDatabase  # noqa: E402
from office_agent.logging_setup import RedactFilter  # noqa: E402
from office_agent.printer_manager import FakePrinterBackend, InstalledPrinter, is_virtual, map_status  # noqa: E402
from office_agent.secrets_store import CredentialStore, InsecureDevProtector  # noqa: E402

URL = "http://localhost"
HP = InstalledPrinter("HP LaserJet", "AVAILABLE", False)
CANON = InstalledPrinter("Canon", "OFFLINE", True)


class Resp:
    def __init__(self, status, data=None, headers=None):
        self.status_code, self._d, self.headers = status, data, headers or {}

    def json(self):
        if self._d is None:
            raise ValueError("no json")
        return self._d


class FakeTransport:
    """Scriptable stand-in for requests.Session. Handlers: callable(json, headers) -> (status, data) or raise."""
    def __init__(self):
        self.routes, self.calls = {}, []

    def request(self, method, url, json=None, headers=None, **kw):
        assert kw.get("allow_redirects") is False
        path = urlsplit(url).path.replace("/api/agent/v1", "")
        self.calls.append((method, path, json, headers))
        return Resp(*self.routes[(method, path)](json, headers or {}))


def ok_register(body, h):
    return 201, {"agent_id": "a1", "credential": "sa_TESTCREDENTIAL1234567890", "office": "HQ", "heartbeat_seconds": 30}


def ok_heartbeat(body, h):
    return 200, {"heartbeat_seconds": 30, "printers": []}


@pytest.fixture()
def env(tmp_path):
    db = LocalDatabase(tmp_path / "agent.db")
    tr = FakeTransport()
    tr.routes[("POST", "/register")] = ok_register
    tr.routes[("POST", "/heartbeat")] = ok_heartbeat
    backend = FakePrinterBackend([HP, CANON])
    store = CredentialStore(db, InsecureDevProtector())
    svc = AgentService(db, store, backend, lambda url, cred: CloudClient(url, cred, session=tr), hostname="PC1")
    return svc, tr, db, store, tmp_path


def enrol(svc):
    svc.register(URL, "se_sometoken")


# ---- config / backoff / logging -------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw,ok", [("https://sesh.example.com/", True), ("http://localhost:5000", True), ("http://127.0.0.1:5000", True),
                                    ("http://sesh.example.com", False), ("ftp://x.com", False), ("sesh.example.com", False),
                                    ("https://user:pw@sesh.example.com", False), ("https://x.com/?a=1", False), ("", False)])
def test_server_url_rules(raw, ok):
    if ok:
        assert normalize_server_url(raw) == raw.rstrip("/")
    else:
        with pytest.raises(ValueError):
            normalize_server_url(raw)


def test_backoff_grows_is_capped_and_jittered():
    assert backoff.delay(1, rng=lambda: 0) == 2.0 and backoff.delay(1, rng=lambda: 1) == 4.0
    assert backoff.delay(50, rng=lambda: 1) == 300.0 and backoff.delay(50, rng=lambda: 0) == 150.0
    assert len({backoff.delay(3) for _ in range(20)}) > 1


def test_log_redaction():
    rec = logging.LogRecord("x", logging.INFO, "", 0, "cred sa_ABCDEFGHIJKLMNOP and Bearer sa_ZZZZZZZZZZZZ and se_1234567890abc", (), None)
    RedactFilter().filter(rec)
    assert "sa_" not in rec.msg and "se_" not in rec.msg and "Bearer" not in rec.msg and "[redacted]" in rec.msg


# ---- local database / credential store ----------------------------------------------------------------------------------
def test_database_persists_caps_activity_and_keeps_data_on_reopen(tmp_path):
    p = tmp_path / "a.db"
    db = LocalDatabase(p)
    db.set("k", "v")
    db.set_selected_printers(["A", "B"])
    for i in range(260):
        db.log("info", f"m{i}")
    db.close()
    db = LocalDatabase(p)  # simulates restart / upgrade
    assert db.get("k") == "v" and db.selected_printers() == ["A", "B"]
    act = db.recent_activity(500)
    assert len(act) <= 201 and act[0]["message"] == "m259"
    assert sqlite3.connect(p).execute("PRAGMA user_version").fetchone()[0] == 1


def test_database_from_newer_version_refused(tmp_path):
    p = tmp_path / "a.db"
    c = sqlite3.connect(p)
    c.execute("PRAGMA user_version=99")
    c.commit()
    c.close()
    with pytest.raises(RuntimeError):
        LocalDatabase(p)


def test_credential_store_never_holds_cleartext_and_survives_corruption(tmp_path):
    db = LocalDatabase(tmp_path / "a.db")
    st = CredentialStore(db, InsecureDevProtector())
    st.set("sa_secretvalue123456")
    assert "sa_secretvalue123456" not in db.get("credential") and st.get() == "sa_secretvalue123456"
    db.set("credential", "!!!not-base64!!!")
    assert st.get() is None
    st.clear()
    assert db.get("credential") is None


# ---- printers -----------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("flags,attrs,expect", [(0, 0, "AVAILABLE"), (0x80, 0, "OFFLINE"), (0x1000, 0, "OFFLINE"), (0, 0x400, "OFFLINE"),
                                                (0x10, 0, "ERROR"), (0x8, 0, "ERROR"), (0x1, 0, "ERROR"), (0x400, 0, "AVAILABLE"),  # printing = fine
                                                (0x80 | 0x10, 0, "OFFLINE")])
def test_status_mapping(flags, attrs, expect):
    assert map_status(flags, attrs) == expect


def test_virtual_printers_flagged():
    assert is_virtual("Microsoft Print to PDF") and is_virtual("OneNote (Desktop)") and not is_virtual("HP LaserJet Pro")


# ---- cloud client -------------------------------------------------------------------------------------------------------
def test_client_refuses_plain_http_to_remote_hosts():
    with pytest.raises(ValueError):
        CloudClient("http://sesh.example.com")


@pytest.mark.parametrize("status,exc", [(401, Unauthorized), (400, Rejected), (409, Rejected), (429, Unavailable), (503, Unavailable), (302, Unavailable)])
def test_client_error_mapping(status, exc):
    tr = FakeTransport()
    tr.routes[("POST", "/heartbeat")] = lambda b, h: (status, {"error": {"code": "X", "message": "m"}})
    with pytest.raises(exc):
        CloudClient(URL, "sa_x", session=tr).heartbeat("1", [])


def test_client_maps_network_errors_and_bad_bodies():
    tr = FakeTransport()

    def boom(b, h):
        raise requests.ConnectionError("secret-host-detail")
    tr.routes[("POST", "/heartbeat")] = boom
    with pytest.raises(Unavailable) as e:
        CloudClient(URL, "sa_x", session=tr).heartbeat("1", [])
    assert "secret" not in e.value.message
    tr.routes[("POST", "/heartbeat")] = lambda b, h: (200, None)
    with pytest.raises(Unavailable):
        CloudClient(URL, "sa_x", session=tr).heartbeat("1", [])


def test_register_401_is_rejected_not_unauthorized():
    tr = FakeTransport()
    tr.routes[("POST", "/register")] = lambda b, h: (401, {"error": {"code": "INVALID_ENROLLMENT", "message": "bad"}})
    with pytest.raises(Rejected):
        CloudClient(URL, session=tr).register("se_x", "pc", "1")


def test_credential_sent_as_bearer_only_when_authenticated():
    tr = FakeTransport()
    tr.routes[("POST", "/heartbeat")] = ok_heartbeat
    tr.routes[("POST", "/register")] = ok_register
    CloudClient(URL, "sa_abc", session=tr).heartbeat("1", [])
    CloudClient(URL, None, session=tr).register("se_x", "pc", "1")
    assert tr.calls[0][3]["Authorization"] == "Bearer sa_abc" and "Authorization" not in tr.calls[1][3]


# ---- service ------------------------------------------------------------------------------------------------------------
def test_register_stores_protected_credential_and_survives_restart(env):
    svc, tr, db, store, tmp = env
    assert svc.state == State.NOT_REGISTERED
    enrol(svc)
    assert store.get() == "sa_TESTCREDENTIAL1234567890" and "TESTCREDENTIAL" not in db.get("credential")
    assert db.get("office") == "HQ" and svc.state == State.OFFLINE  # not CONNECTED until a heartbeat succeeds
    again = AgentService(db, store, FakePrinterBackend([HP]), lambda u, c: CloudClient(u, c, session=tr))
    assert again.state == State.OFFLINE  # restored from local state


def test_register_validations_and_rejection(env):
    svc, tr, *_ = env
    with pytest.raises(ValueError):
        svc.register("http://evil.example.com", "se_x")
    with pytest.raises(Rejected):
        svc.register(URL, "   ")
    tr.routes[("POST", "/register")] = lambda b, h: (401, {"error": {"code": "INVALID_ENROLLMENT", "message": "bad"}})
    with pytest.raises(Rejected):
        svc.register(URL, "se_x")
    assert svc.state == State.NOT_REGISTERED


def test_heartbeat_success_reports_all_installed_printers(env):
    svc, tr, *_ = env
    enrol(svc)
    assert svc.tick() == 30.0 and svc.state == State.CONNECTED and svc.failures == 0
    sent = [c for c in tr.calls if c[1] == "/heartbeat"][-1][2]["printers"]
    assert {p["windows_name"] for p in sent} == {"HP LaserJet", "Canon"} and sent[0]["status"] in ("AVAILABLE", "OFFLINE")


def test_network_failure_goes_offline_with_backoff_then_recovers(env):
    svc, tr, *_ = env
    enrol(svc)
    svc.tick()

    def boom(b, h):
        raise requests.ConnectionError("down")
    tr.routes[("POST", "/heartbeat")] = boom
    d1, d2 = svc.tick(), svc.tick()
    assert svc.state == State.OFFLINE and svc.failures == 2 and 2 <= d1 <= 4 and 4 <= d2 <= 8
    assert "Not connected" in svc.describe()
    tr.routes[("POST", "/heartbeat")] = ok_heartbeat
    assert svc.tick() == 30.0 and svc.state == State.CONNECTED and svc.failures == 0


def test_401_marks_revoked_clears_credential_and_stops_heartbeats(env):
    svc, tr, db, store, _ = env
    enrol(svc)
    tr.routes[("POST", "/heartbeat")] = lambda b, h: (401, {"error": {"code": "UNAUTHORIZED", "message": "x"}})
    svc.tick()
    assert svc.state == State.REVOKED and store.get() is None and db.get("revoked") == "1"
    n = len(tr.calls)
    svc.tick()
    assert len(tr.calls) == n  # no more calls once revoked
    restarted = AgentService(db, store, FakePrinterBackend([HP]), lambda u, c: CloudClient(u, c, session=tr))
    assert restarted.state == State.REVOKED
    enrol(svc)  # re-enrol with a new token works
    assert svc.state == State.OFFLINE and db.get("revoked") is None


def test_set_enabled_applies_server_result_and_keeps_local_state_on_rejection(env):
    svc, tr, db, *_ = env
    enrol(svc)
    tr.routes[("PUT", "/printers")] = lambda b, h: (200, {"printers": [{"windows_name": n} for n in b["enabled"]]})
    assert svc.set_enabled(["HP LaserJet"]) == ["HP LaserJet"] and db.selected_printers() == ["HP LaserJet"]
    tr.routes[("PUT", "/printers")] = lambda b, h: (409, {"error": {"code": "SELECTION_REJECTED", "message": "This office already has 1 active printer."}})
    with pytest.raises(Rejected) as e:
        svc.set_enabled(["HP LaserJet", "Canon"])
    assert "already has 1" in e.value.message and db.selected_printers() == ["HP LaserJet"]


def test_server_is_authority_for_selection_on_heartbeat(env):
    svc, tr, db, *_ = env
    enrol(svc)
    db.set_selected_printers(["Stale"])
    tr.routes[("POST", "/heartbeat")] = lambda b, h: (200, {"heartbeat_seconds": 5, "printers": [{"windows_name": "Canon"}]})
    assert svc.tick() == 10.0  # heartbeat interval clamped to the minimum
    assert db.selected_printers() == ["Canon"]


def test_removed_printer_still_listed_so_user_sees_it(env):
    svc, tr, db, *_ = env
    enrol(svc)
    db.set_selected_printers(["Gone Printer"])
    svc.refresh_printers()
    gone = [p for p in svc.printers_view() if p["name"] == "Gone Printer"][0]
    assert gone["installed"] is False and gone["enabled"] is True


def test_reenrol_restores_previous_selection_when_server_allows(env):
    svc, tr, db, *_ = env
    db.set_selected_printers(["HP LaserJet"])
    tr.routes[("PUT", "/printers")] = lambda b, h: (200, {"printers": [{"windows_name": n} for n in b["enabled"]]})
    enrol(svc)
    assert db.selected_printers() == ["HP LaserJet"]


def test_unregister_revokes_server_side_and_clears_local(env):
    svc, tr, db, store, _ = env
    enrol(svc)
    tr.routes[("POST", "/unregister")] = lambda b, h: (200, {"ok": True})
    svc.unregister()
    assert svc.state == State.NOT_REGISTERED and store.get() is None and db.selected_printers() == []


def test_unregister_unreachable_needs_force_local(env):
    svc, tr, db, store, _ = env
    enrol(svc)

    def boom(b, h):
        raise requests.ConnectionError("down")
    tr.routes[("POST", "/unregister")] = boom
    with pytest.raises(Unavailable):
        svc.unregister()
    assert store.get() is not None  # nothing forgotten yet
    svc.unregister(force_local=True)
    assert svc.state == State.NOT_REGISTERED and store.get() is None


def test_rotate_stores_new_credential(env):
    svc, tr, db, store, _ = env
    enrol(svc)
    tr.routes[("POST", "/credential/rotate")] = lambda b, h: (200, {"credential": "sa_NEWCREDENTIAL12345678"})
    svc.rotate_credential()
    assert store.get() == "sa_NEWCREDENTIAL12345678"


def test_pause_flag_persists(env):
    svc, tr, db, *_ = env
    svc.set_paused(True)
    assert svc.paused and db.get("paused") == "1"
    svc.set_paused(False)
    assert not svc.paused


def test_secrets_never_reach_activity_log(env):
    svc, tr, db, *_ = env
    enrol(svc)
    svc.tick()
    blob = " ".join(a["message"] for a in db.recent_activity(100))
    assert "TESTCREDENTIAL" not in blob and "se_sometoken" not in blob
