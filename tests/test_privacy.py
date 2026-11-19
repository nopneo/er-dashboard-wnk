"""คนนอก รพ. ต้องย้อนหาผล CPR (รอด/ไม่รอด) ของคนที่มา ER วันใดวันหนึ่งไม่ได้"""
from datetime import date

import pytest

import app as app_mod
import privacy
import queries

PUBLIC = {"REMOTE_ADDR": "8.8.8.8"}
LAN = {"REMOTE_ADDR": "192.168.1.5"}


def _ctx(d1, d2, shift="all"):
    return {"d1": d1, "d2": d2, "shift": shift,
            "ft": {"cpr": {"n": 1, "ems": 1, "refer": 0, "admit": 0, "refer_pct": 0.0, "rosc": 0, "rosc_pct": 0.0,
                           "defib": 0},
                   "stroke": {"n": 3}}}


@pytest.fixture
def seen(monkeypatch):
    calls = []

    def build(d1, d2, shift="all"):
        calls.append((d1, d2, shift))
        return _ctx(d1, d2, shift)
    monkeypatch.setattr(queries, "build", build)
    app_mod.app.config["TESTING"] = True
    return calls


@pytest.fixture
def client():
    app_mod._export_cache._data.clear()
    return app_mod.app.test_client()


def test_lan_keeps_exact_single_day(client, seen):
    d = client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03", environ_base=LAN).get_json()
    assert seen[-1][:2] == ("2026-02-03", "2026-02-03")
    assert d["ft"]["cpr"]["n"] == 1 and d["ft"]["cpr"]["rosc"] == 0


def test_public_snapped_and_cpr_hidden(client, seen):
    d = client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03", environ_base=PUBLIC).get_json()
    assert seen[-1][:2] == ("2026-02-01", "2026-02-28")
    cpr = d["ft"]["cpr"]
    assert cpr["n"] == cpr["rosc"] == cpr["refer"] == cpr["admit"] == "<5"   # n − rosc ลบหาการเสียชีวิตไม่ได้
    assert d["ft"]["stroke"]["n"] == 3                                          # บล็อกอื่นไม่เปลี่ยน


def test_cloudflare_hospital_ip_is_inside(client, seen, monkeypatch):
    monkeypatch.setattr(app_mod, "LAN_NETS", app_mod._nets("183.88.214.202"))
    for ip, inside in (("1.2.3.4", False), ("183.88.214.202", True)):
        h = {"X-Forwarded-For": f"{ip}, 162.158.1.1", "CF-Connecting-IP": ip}
        client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03", headers=h, environ_base={"REMOTE_ADDR": "127.0.0.1"})
        assert (seen[-1][:2] == ("2026-02-03", "2026-02-03")) is inside


def test_public_ctx_does_not_mutate_cache():
    ctx = _ctx("2026-02-01", "2026-02-28")
    out = privacy.public_ctx(ctx)
    assert out["ft"]["cpr"]["n"] == "<5" and ctx["ft"]["cpr"]["n"] == 1


def test_large_cpr_block_unchanged():
    ctx = _ctx("2026-02-01", "2026-02-28")
    ctx["ft"]["cpr"]["n"] = 7
    assert privacy.public_ctx(ctx)["ft"]["cpr"]["n"] == 7


def test_export_public_flag_and_separate_cache(client, seen, monkeypatch):
    urls = []

    def fake(fmt, url, out):
        urls.append(url)
        open(out, "wb").write(b"x")
    monkeypatch.setattr(app_mod.render, "render_to", fake)
    monkeypatch.setattr(app_mod, "EXPORT_ENABLED", True)
    r1 = client.get("/export.png?d1=2026-02-01&d2=2026-02-28", environ_base=LAN)
    r2 = client.get("/export.png?d1=2026-02-01&d2=2026-02-28", environ_base=PUBLIC)
    assert r1.status_code == r2.status_code == 200
    assert len(urls) == 2 and "public=1" not in urls[0] and "public=1" in urls[1]
    assert "|" not in r2.headers["Content-Disposition"]


def test_month_window():
    assert privacy.month_window("2026-02-03", "2026-02-03", today=date(2026, 6, 1)) == ("2026-02-01", "2026-02-28")
    assert privacy.month_window("2026-06-02", "2026-06-05", today=date(2026, 6, 9)) == ("2026-06-01", "2026-06-09")


def test_disabled_with_zero(client, seen, monkeypatch):
    monkeypatch.setattr(privacy, "MIN_CELL", 0)
    client.get("/api/data.json?d1=2026-02-03&d2=2026-02-03", environ_base=PUBLIC)
    assert seen[-1][:2] == ("2026-02-03", "2026-02-03")


def test_template_renders_suppressed_cpr(fake_db):
    from flask import render_template
    ctx = queries.build("2026-02-01", "2026-02-28", "all")
    ctx["ft"]["cpr"].update(n=1, ems=1, rosc=0, defib=0)
    with app_mod.app.test_request_context("/", environ_base=PUBLIC):
        html = render_template("dashboard.html", d=privacy.public_ctx(ctx), presets=app_mod._fy_presets(),
                               print_mode=False, public_view=True)
    assert "ข้อมูลสาธารณะ" in html and "&lt;5" in html
