"""ปุ่มลัดดูรายปีงบประมาณบนแถบด้านบนของแดชบอร์ด — ไม่ต่อฐานข้อมูลจริง"""

from datetime import date

import pytest

import app as app_mod


def test_fy_presets_in_october_start_new_fiscal_year():
    p = app_mod._fy_presets(date(2026, 10, 15))
    assert p[0] == {"label": "ปีงบ 2570", "d1": "2026-10-01", "d2": "2026-10-15"}
    assert p[1] == {"label": "ปีงบ 2569", "d1": "2025-10-01", "d2": "2026-09-30"}
    assert p[2] == {"label": "ปีงบ 2568", "d1": "2024-10-01", "d2": "2025-09-30"}


def test_fy_presets_before_october_belong_to_previous_calendar_year():
    p = app_mod._fy_presets(date(2026, 3, 5))
    assert p[0] == {"label": "ปีงบ 2569", "d1": "2025-10-01", "d2": "2026-03-05"}
    assert p[1]["d1"] == "2024-10-01" and p[1]["d2"] == "2025-09-30"


def test_fy_presets_stay_within_max_days():
    # ปีงบที่ข้ามวันที่ 29 ก.พ. ยาว 366 วัน — ต้องไม่ถูก _range ตัดตามค่าเริ่มต้นของ ER_MAX_DAYS
    for p in app_mod._fy_presets(date(2028, 11, 1), years=6):
        span = (date.fromisoformat(p["d2"]) - date.fromisoformat(p["d1"])).days + 1
        assert span <= app_mod.MAX_DAYS


@pytest.fixture
def client(fake_db):
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def test_dashboard_shows_fy_buttons_and_keeps_shift(client):
    html = client.get("/?shift=C").get_data(as_text=True)
    first = app_mod._fy_presets()[0]
    assert 'class="presets"' in html and first["label"] in html
    assert f'href="?d1={first["d1"]}&d2={first["d2"]}&shift=C"' in html


def test_active_fy_button_highlighted(client):
    p = app_mod._fy_presets()[1]
    html = client.get(f"/?d1={p['d1']}&d2={p['d2']}").get_data(as_text=True)
    assert f'shift=all" class="on">{p["label"]}' in html
    assert f'>{app_mod._fy_presets()[0]["label"]}</a>' in html and 'class="on">' + app_mod._fy_presets()[0]["label"] not in html

