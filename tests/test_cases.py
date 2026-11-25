"""รายชื่อเคสสำหรับทบทวน (/cases/<kind>): มี HN + ชื่อผู้ป่วย → เฉพาะ LAN + ต้องใส่รหัส + บันทึกการเปิดดู · ไม่ต่อฐานข้อมูลจริง"""

import logging

import pytest
from werkzeug.security import generate_password_hash

import app as app_mod
import cases
import db
import nurse_report

D1, D2 = "2026-11-01", "2026-11-25"


def test_names_show_title_first_name_and_part_of_surname_only():
    assert cases._name({"pname": "นางสาว", "fname": "สมศรี", "lname": "ใจดี"}) == "นางสาวสมศรี ใจxxx"
    assert cases._name({"pname": "นาย", "fname": "สมชาย", "lname": "รักดี"}) == "นายสมชาย รักxxx"
    assert "ใจดี" not in cases._name({"pname": "", "fname": "สมศรี", "lname": "ใจดี"})
    assert cases._name({"pname": None, "fname": None, "lname": None}) == "(ไม่มีชื่อ)"
    assert cases._name({"pname": "ด.ช.", "fname": "ก้อง", "lname": None}) == "ด.ช.ก้อง"


@pytest.fixture
def canned(monkeypatch):
    calls = []

    def fetch(sql, args=None):
        calls.append(db._bind(sql, args))
        return fetch.rows

    fetch.rows = []
    monkeypatch.setattr(cases, "fetch", fetch)
    monkeypatch.setattr(nurse_report, "fetch", lambda sql, args=None: [{"code": "J069", "name": "ติดเชื้อทางเดินหายใจ"},
                                                                       {"code": "J060", "name": "กล่องเสียงอักเสบ"}])
    fetch.calls = calls
    return fetch


def test_revisit_rows_use_dashboard_conditions_and_mask_names(canned):
    canned.rows = [{"vn": "1", "hn": "000123", "pname": "นาย", "fname": "สมชาย", "lname": "รักดี", "t": "2026-11-10 10:00:00",
                    "lvl": "2", "dch": "1", "pdx": "J069", "ptime": "2026-11-09 09:30:00", "pdch": "2", "gap": "1470",
                    "ppdx": "J060"}]
    d = cases.revisit_cases(D1, D2, "all")
    sql = " ".join(canned.calls[0].split())
    assert "left(b.icd10, 3) = left(a.icd10, 3)" in sql and "interval 48 hour" in sql       # เงื่อนไขเดียวกับการ์ดบนแดชบอร์ด
    assert "order by p.enter_er_time desc limit 1" in sql and "limit 501" in sql
    r = d["rows"][0]
    assert r["hn"] == "000123" and r["name"] == "นายสมชาย รักxxx" and "รักดี" not in str(d)
    assert r["gap"] == "24 ชม. 30 นาที" and r["level"] == 2 and r["level_name"] == "Emergency"
    assert r["prev_dx"] == "J060 กล่องเสียงอักเสบ" and r["dx"] == "J069 ติดเชื้อทางเดินหายใจ"
    assert r["prev_dch"] == "ส่งต่อ (Refer)" and r["dch"] == "กลับบ้าน" and not d["truncated"]


def test_red_rows_use_block_17_rule(canned):
    canned.rows = [{"vn": "2", "hn": "000456", "pname": "นาง", "fname": "สมหญิง", "lname": "ดีมาก", "t": "2026-11-12 03:00:00",
                    "dch": "3", "m": "95", "pdx": "J069"}]
    d = cases.red_cases(D1, D2, "all")
    sql = " ".join(canned.calls[0].split())
    assert "t.m > 60 and t.m <= 1440" in sql and "= 1 and e.er_dch_type in (2,3)" in sql
    r = d["rows"][0]
    assert (r["minutes"], r["span"], r["dch"]) == (95, "1 ชม. 35 นาที", "Admit")


def test_lists_are_capped(canned):
    canned.rows = [{"vn": str(i), "hn": str(i), "pname": "", "fname": "ก", "lname": "", "t": "2026-11-10 10:00:00", "dch": "3",
                    "m": "70", "pdx": None} for i in range(cases.MAX_ROWS + 1)]
    d = cases.red_cases(D1, D2, "all")
    assert len(d["rows"]) == cases.MAX_ROWS and d["truncated"] and d["rows"][0]["dx"] == "ไม่ได้ลงโรคหลัก"


# ------------------------------------------------------------- หน้าเว็บ: LAN + รหัส + บันทึก

@pytest.fixture
def web(fake_db, monkeypatch, caplog):
    monkeypatch.setattr(app_mod, "NP_PASS_HASH", generate_password_hash("right-code", method="pbkdf2:sha256:1000"))
    monkeypatch.setattr(app_mod, "_NP_TAG", "tag")
    monkeypatch.setattr(app_mod, "NP_OPEN", False)
    monkeypatch.setattr(app_mod.time, "sleep", lambda s: None)
    monkeypatch.setattr(app_mod.audit_log, "propagate", True)
    caplog.set_level(logging.INFO, logger="erdash.audit")
    app_mod._np_fail._data.clear()
    app_mod._np_fail_all.clear()
    shown = {"rows": [{"hn": "000123", "name": "นายสมชาย รักxxx", "prev_when": "9 พ.ย. 69 09:30", "prev_dx": "J060 x",
                       "prev_dch": "กลับบ้าน", "when": "10 พ.ย. 69 10:00", "level": 2, "level_name": "Emergency",
                       "dx": "J069 y", "dch": "กลับบ้าน", "gap": "24 ชม."}], "truncated": False}
    monkeypatch.setattr(cases, "get", lambda kind, d1, d2, shift: {**shown, "kind": kind})
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def _login(c):
    with c.session_transaction() as s:
        s["np"] = "tag"


OUTSIDE = {"headers": {"CF-Connecting-IP": "8.8.8.8", "X-Forwarded-For": "8.8.8.8, 172.70.1.1"}}


def test_outside_viewer_is_refused_without_touching_the_database(web, monkeypatch, caplog):
    monkeypatch.setattr(cases, "get", lambda *a: pytest.fail("ไม่ควรดึงข้อมูล"))
    _login(web)                                                          # แม้ใส่รหัสแล้วก็เปิดจากนอก รพ. ไม่ได้
    r = web.get("/cases/revisit", **OUTSIDE)
    assert r.status_code == 403 and "เฉพาะเครื่องในเครือข่าย" in r.get_data(as_text=True)
    assert r.headers["Cache-Control"] == "no-store"
    assert "CASES denied kind=revisit reason=lan" in caplog.text


def test_public_view_flag_is_refused_even_from_the_hospital(web):
    _login(web)
    assert web.get("/cases/red?public=1").status_code == 403


def test_closed_until_a_password_is_set(web, monkeypatch):
    monkeypatch.setattr(app_mod, "NP_PASS_HASH", "")
    r = web.get("/cases/revisit")
    assert r.status_code == 403 and "ER_NP_PASS_HASH" in r.get_data(as_text=True)


def test_password_required_then_back_to_the_list(web):
    r = web.get(f"/cases/revisit?d1={D1}&d2={D2}&shift=C")
    assert r.status_code == 302 and "/nurse/login" in r.location and "next=revisit" in r.location
    page = web.get(r.location).get_data(as_text=True)
    assert "รายชื่อเคสสำหรับทบทวน" in page and 'name="next" value="revisit"' in page
    ok = web.post("/nurse/login", data={"passcode": "right-code", "next": "revisit", "d1": D1, "d2": D2, "shift": "C"})
    assert ok.status_code == 302 and ok.location.startswith("/cases/revisit") and "shift=C" in ok.location


def test_export_token_does_not_open_the_list(web):
    token = app_mod._np_token.dumps("tag")
    assert web.get(f"/cases/revisit?t={token}").status_code == 302


def test_list_shows_masked_names_and_logs_without_personal_data(web, caplog):
    _login(web)
    r = web.get(f"/cases/revisit?d1={D1}&d2={D2}")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "000123" in html and "นายสมชาย รักxxx" in html
    assert r.headers["Cache-Control"] == "no-store" and r.headers["X-Robots-Tag"] == "noindex"
    line = next(x for x in caplog.messages if x.startswith("CASES view"))
    assert "kind=revisit" in line and f"range={D1}..{D2}" in line and "rows=1" in line and "ip=127.0.0.1" in line
    assert "000123" not in caplog.text and "สมชาย" not in caplog.text


def test_unknown_list_is_404(web):
    _login(web)
    assert web.get("/cases/everyone").status_code == 404


def test_dashboard_links_only_for_hospital_viewers_with_a_password(web, monkeypatch):
    assert "ดูรายชื่อเคส" in web.get("/").get_data(as_text=True)
    assert "ดูรายชื่อเคส" not in web.get("/?print=1").get_data(as_text=True)          # ไม่ติดไปในภาพส่งออก
    assert "ดูรายชื่อเคส" not in web.get("/", **OUTSIDE).get_data(as_text=True)
    monkeypatch.setattr(app_mod, "NP_PASS_HASH", "")
    assert "ดูรายชื่อเคส" not in web.get("/").get_data(as_text=True)
