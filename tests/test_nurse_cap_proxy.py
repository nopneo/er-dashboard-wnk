"""รหัสรายงานพยาบาล: เพดานรวมทุก IP · cookie Secure ทาง https · header จาก proxy เฉพาะเครื่องเดียวกัน ·
ช่วง IP ภายในระบุเอง"""
import pytest
from werkzeug.security import generate_password_hash

import app as app_mod

CF_EDGE = "172.70.1.1"


def _cf(ip):
    return {"headers": {"X-Forwarded-For": f"{ip}, {CF_EDGE}", "CF-Connecting-IP": ip, "X-Forwarded-Proto": "https"},
            "environ_base": {"REMOTE_ADDR": "127.0.0.1"}}


@pytest.fixture
def client(monkeypatch):
    h = generate_password_hash("right-code", method="pbkdf2:sha256:1000")
    monkeypatch.setattr(app_mod, "NP_PASS_HASH", h)
    monkeypatch.setattr(app_mod, "_NP_TAG", "tag")
    monkeypatch.setattr(app_mod, "NP_OPEN", False)
    monkeypatch.setattr(app_mod, "NP_MAX_FAIL_ALL", 5)
    monkeypatch.setattr(app_mod.time, "sleep", lambda s: None)
    app_mod._np_fail._data.clear()
    app_mod._np_fail_all.clear()
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def _post(client, code, ip):
    return client.post("/nurse/login", data={"passcode": code}, **_cf(ip))


def test_global_cap_blocks_attack_from_many_ips(client):
    for i in range(5):
        assert _post(client, "wrong", f"1.2.3.{i}").status_code == 401
    r = _post(client, "right-code", "9.9.9.9")                     # รหัสถูกก็พักไว้ก่อน
    assert r.status_code == 401 and "รอ 15 นาที" in r.get_data(as_text=True)


def test_global_cap_expires(client, monkeypatch):
    for i in range(5):
        _post(client, "wrong", f"1.2.3.{i}")
    now = app_mod.time.time()
    monkeypatch.setattr(app_mod.time, "time", lambda: now + app_mod.NP_FAIL_WINDOW + 1)
    assert _post(client, "right-code", "9.9.9.9").status_code == 302


def test_per_ip_cap_still_applies(client, monkeypatch):
    monkeypatch.setattr(app_mod, "NP_MAX_FAIL_ALL", 0)                # ปิดเพดานรวม
    for _ in range(app_mod.NP_MAX_FAIL):
        _post(client, "wrong", "1.2.3.4")
    assert _post(client, "right-code", "1.2.3.4").status_code == 401
    assert _post(client, "right-code", "5.6.7.8").status_code == 302


def test_cookie_secure_over_https(client):
    r = _post(client, "right-code", "9.9.9.9")
    cookie = r.headers.get("Set-Cookie", "")
    assert "erdash_session=" in cookie and "Secure" in cookie and "HttpOnly" in cookie


def test_cookie_not_secure_over_plain_http(client):
    r = client.post("/nurse/login", data={"passcode": "right-code"}, environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert "Secure" not in r.headers.get("Set-Cookie", "")


@pytest.mark.parametrize("ip, lan", [
    ("192.168.11.5", True), ("10.1.2.3", True), ("172.20.0.1", True), ("127.0.0.1", True),
    ("198.18.0.1", False), ("203.0.113.7", False), ("100.64.1.1", False), ("8.8.8.8", False),
    ("::ffff:192.168.1.1", True), ("::ffff:8.8.8.8", False)])
def test_from_lan_explicit_ranges(ip, lan):
    with app_mod.app.test_request_context("/", environ_base={"REMOTE_ADDR": ip}):
        assert app_mod._from_lan() is lan


def test_proxy_headers_only_from_trusted_proxy(client):
    html = client.get("/nurse/login", headers={"X-Forwarded-Prefix": "/evil"},
                      environ_base={"REMOTE_ADDR": "192.168.1.5"}).get_data(as_text=True)
    assert "/evil/" not in html
    html = client.get("/nurse/login", headers={"X-Forwarded-Prefix": "/erdash"},
                      environ_base={"REMOTE_ADDR": "127.0.0.1"}).get_data(as_text=True)
    assert "/erdash/" in html
    with app_mod.app.test_request_context("/", headers={"X-Forwarded-Proto": "https"},
                                          environ_base={"REMOTE_ADDR": "192.168.1.5"}):
        pass
    r = client.post("/nurse/login", data={"passcode": "right-code"},
                    headers={"X-Forwarded-Proto": "https"}, environ_base={"REMOTE_ADDR": "192.168.1.5"})
    assert "Secure" not in r.headers.get("Set-Cookie", "")              # proto ปลอมจากเครื่องอื่นไม่มีผล
