"""มาตรการความเป็นส่วนตัวของหน้าสาธารณะ (privacy.py) — คนนอก รพ. เห็นช่วงวันที่เป็นเดือนเต็ม + CPR ช่องเล็กเป็น "<5" """

from datetime import date

import pytest

import app as app_mod
import privacy


def test_month_window_rounds_to_whole_months_and_caps_at_today():
    today = date(2026, 11, 23)
    assert privacy.month_window("2026-11-10", "2026-11-10", today) == ("2026-11-01", "2026-11-23")
    assert privacy.month_window("2026-09-15", "2026-10-03", today) == ("2026-09-01", "2026-10-31")
    assert privacy.month_window("2026-02-28", "2026-02-28", today) == ("2026-02-01", "2026-02-28")


def _ctx(n):
    return {"ft": {"cpr": {"n": n, "ems": 1, "refer": 1, "admit": 0, "rosc": 1, "defib": 1,
                           "refer_pct": 50.0, "rosc_pct": 33.3}, "stemi": {"n": 3}}}


def test_small_cpr_block_hidden_without_touching_shared_cache_copy():
    ctx = _ctx(3)
    out = privacy.public_ctx(ctx)
    cpr = out["ft"]["cpr"]
    assert out["public_privacy"] is True
    assert all(cpr[k] == "<5" for k in privacy.CPR_COUNTS) and cpr["rosc_pct"] == "–"
    assert ctx["ft"]["cpr"]["n"] == 3 and "public_privacy" not in ctx      # ต้นฉบับ (cache) ไม่ถูกแก้
    assert out["ft"]["stemi"] == {"n": 3}


@pytest.mark.parametrize("n", [0, 5, 31])
def test_cpr_block_with_zero_or_enough_cases_is_shown(n):
    assert privacy.public_ctx(_ctx(n))["ft"]["cpr"]["n"] == n


def test_privacy_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(privacy, "MIN_CELL", 0)
    ctx = _ctx(2)
    assert privacy.public_ctx(ctx) is ctx


@pytest.fixture
def client(fake_db):
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def test_outside_viewer_gets_month_window_and_notice(client):
    html = client.get("/?d1=2026-11-10&d2=2026-11-10&shift=C", headers={"CF-Connecting-IP": "8.8.8.8"}).get_data(as_text=True)
    assert 'name="d1" value="2026-11-01"' in html and "ข้อมูลสาธารณะ" in html
    assert '<option value="C" selected' in html                      # เวรไม่ถูกปัด


def test_hospital_viewer_keeps_the_exact_range(client):
    html = client.get("/?d1=2026-11-10&d2=2026-11-10&shift=C").get_data(as_text=True)
    assert 'name="d1" value="2026-11-10"' in html and "ข้อมูลสาธารณะ" not in html
