from tests.conftest import login


def test_signed_out_visitor_sees_landing_with_login_link(client, acme):
    r = client.get("/")
    assert r.status_code == 200
    assert b'href="/login"' in r.data and b"Sign in" in r.data
    assert b"Welcome back" not in r.data  # not the dashboard
    assert b'href="/signup"' not in r.data  # sign-up is only offered before a company exists


def test_landing_offers_signup_only_before_a_company_exists(client):
    r = client.get("/")
    assert r.status_code == 200 and b'href="/signup"' in r.data and b"Sign up" in r.data


def test_signed_in_user_still_gets_the_dashboard(client, acme):
    login(client, "worker@acme-corp.com")
    r = client.get("/")
    assert r.status_code == 200 and b"Welcome back" in r.data and b"Skip to content" not in r.data


def test_landing_assets_are_served_locally(client, acme):
    r = client.get("/")
    assert b"landing.css" in r.data and b"cdn." not in r.data and b"googleapis" not in r.data
    assert client.get("/static/landing.css").status_code == 200


def test_protected_pages_still_redirect_to_login(client, acme):
    r = client.get("/documents/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]
