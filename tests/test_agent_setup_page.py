from tests.conftest import login


def test_agent_page_admin_only(client, acme):
    assert client.get("/agent").status_code == 302
    login(client, "worker@acme-corp.com")
    assert client.get("/agent").status_code == 403
    assert client.get("/agent/download").status_code == 403


def test_agent_page_and_download(app, client, acme, tmp_path):
    login(client, "admin@acme-corp.com")
    r = client.get("/agent")
    assert r.status_code == 200 and b"not available on this server yet" in r.data
    assert client.get("/agent/download").status_code == 404
    exe = tmp_path / "SeshAgentSetup.exe"
    exe.write_bytes(b"MZ" + b"0" * 100)
    app.config["AGENT_INSTALLER_PATH"] = str(exe)
    assert b"Download SeshAgentSetup.exe" in client.get("/agent").data
    d = client.get("/agent/download")
    assert d.status_code == 200 and "attachment" in d.headers["Content-Disposition"]