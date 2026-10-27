"""Data Audit (หน้า /check), แถบเตือนบนแดชบอร์ด, เปิด /check เฉพาะ LAN และ setup_check.py — ไม่ต่อฐานข้อมูลจริง"""

import pytest

import app as app_mod
import check
import queries
import setup_check

D1, D2 = "2026-10-01", "2026-10-27"
OVSTIST = {"01": "มาเอง", "02": "นัดมา", "03": "รับ Refer", "04": "EMS (ALS)", "05": "Kiosk",
           "06": "EMS (FR)", "07": "EMS BLS"}


def test_ovstist_suggests_code_with_matching_name():
    rows = [{"code": "01", "n": 60}, {"code": "04", "n": 10}, {"code": "06", "n": 5}, {"code": "07", "n": 8},
            {"code": "02", "n": 10}, {"code": "03", "n": 7}]
    r = check._group_audit(rows, OVSTIST, check.OVSTIST_GROUPS, 100)
    s = {x["env"]: x for x in r["summary"]}
    assert s["ER_EMS_FR_CODES"]["status"] == "warn"
    assert s["ER_EMS_FR_CODES"]["fix"] == "ER_EMS_FR_CODES=06,07"
    assert s["ER_EMS_ALS_CODES"]["status"] == "ok" and s["ER_APPT_CODES"]["status"] == "ok"
    row07 = next(x for x in r["rows"] if x["code"] == "07")
    assert row07["group"] == "" and "EMS FR" in row07["note"]


def test_dch_code_with_wrong_name_is_flagged():
    names = {"1": "Admit", "2": "Refer", "3": "กลับบ้าน", "4": "เสียชีวิต", "5": "สังเกตอาการ",
             "7": "ปฏิเสธการรักษา", "8": "หนีกลับ"}
    rows = [{"code": "1", "n": 20}, {"code": "3", "n": 70}, {"code": "4", "n": 1}, {"code": "2", "n": 9}]
    r = check._group_audit(rows, names, check.DCH_GROUPS, 100)
    s = {x["env"]: x for x in r["summary"]}
    assert s["ER_DCH_HOME"]["status"] == "bad" and s["ER_DCH_HOME"]["fix"] == "ER_DCH_HOME=3"
    assert s["ER_DCH_ADMIT"]["status"] == "bad" and s["ER_DCH_ADMIT"]["fix"] == "ER_DCH_ADMIT=1"
    assert s["ER_DCH_REFER"]["status"] == "ok"
    assert not next(x for x in r["rows"] if x["code"] == "4")["note"]      # เสียชีวิต = "อื่น ๆ" ตั้งใจ ไม่เตือน
    assert s["ER_DCH_ESCAPE"]["status"] == "ok"            # ไม่มี visit แต่ชื่อตรง = เหตุการณ์ไม่บ่อย ไม่เตือน


def test_dentist_is_not_counted_as_doctor():
    r = check._group_audit([{"code": "01", "n": 30}, {"code": "02", "n": 1}, {"code": "03", "n": 20}],
                           {"01": "แพทย์", "02": "ทันตแพทย์", "03": "พยาบาลวิชาชีพ"}, check.PROVIDER_GROUPS, 100)
    assert all(x["status"] == "ok" for x in r["summary"])


def test_group_without_lookup_table_uses_counts_only():
    r = check._group_audit([{"code": "4", "n": 3}], {}, check.PT_GROUPS, 100)
    s = {x["env"]: x for x in r["summary"]}
    assert s["ER_SVC_PT_TYPES"]["status"] == "ok"
    assert s["ER_AUTOPSY_PT_TYPES"]["status"] == "warn"       # ไม่มี visit ด้วยรหัสนี้
    assert r["names"] is False


def _fake(monkeypatch, by_key, one=None):
    def fetch(sql, args=None):
        for key, rows in by_key.items():
            if key in sql:
                return rows
        return []
    monkeypatch.setattr(check, "fetch", fetch)
    monkeypatch.setattr(check, "fetch_one", lambda sql, args=None: one or {})


def test_er_depcode_missing_suggests_er_named_department(monkeypatch):
    _fake(monkeypatch, {"from kskdepartment": [
        {"depcode": "001", "department": "ห้องบัตร"}, {"depcode": "017", "department": "ห้องฉุกเฉิน ER"}],
        "o.main_dep": [{"code": "017", "n": 80}, {"code": "001", "n": 20}]})
    r = check._deps({"d1": D1, "d2": D2}, 100)
    assert r["summary"]["status"] == "bad" and r["summary"]["fix"] == "ER_DEPCODE=017"
    assert r["rows"][0]["note"]                                    # ER จริงถูกนับเป็น "ส่งมาจากแผนกอื่น"


def test_er_depcode_ok(monkeypatch):
    _fake(monkeypatch, {"from kskdepartment": [{"depcode": "011", "department": "อุบัติเหตุ-ฉุกเฉิน"}],
                        "o.main_dep": [{"code": "011", "n": 90}, {"code": "010", "n": 10}]})
    r = check._deps({"d1": D1, "d2": D2}, 100)
    assert r["summary"]["status"] == "ok" and r["summary"]["fix"] is None


def test_st_column_suggested_only_when_current_is_empty(monkeypatch):
    monkeypatch.setattr(queries, "ST_DOCTOR", "service5")
    cols = [{"col": "service5", "n": 2, "pct": 2.0, "after": 30, "deps": [], "used": True},
            {"col": "service4", "n": 90, "pct": 90.0, "after": 3, "deps": [{"code": "010", "pct": 90}], "used": False},
            {"col": "service12", "n": 70, "pct": 70.0, "after": 25, "deps": [{"code": "011", "pct": 95}], "used": False}]
    r = check._st_audit(cols, {"011": "ER"})
    assert r["suggest"] == "service12" and r["status"] == "warn"
    cols[0]["pct"] = 60.0
    assert check._st_audit(cols, {})["suggest"] is None


def test_times_flag_missing_admit_time():
    b = {"n_all": 100, "n_er": 100, "n": 90, "no_level": 0, "no_pt": 0, "no_dch": 0,
         "admit": 10, "admit_t": 5, "refer": 4, "refer_t": 4}
    t = {x["label"].split(" (")[0]: x["status"] for x in check._times(b)}
    assert t["เวลา Admit"] == "warn" and t["เวลาส่งต่อ"] == "ok" and t["เวลาเข้า ER"] == "ok"


def test_full_audit_binds_all_queries(fake_db):
    a = check.audit(D1, D2)
    assert isinstance(a["codes"], list) and a["issues"]          # ไม่มี visit = มีเรื่องให้ตรวจ
    assert any("ER_ST_DOCTOR_COL" == c.get("env") for c in a["codes"])
    assert all(args is not None for sql, args, _ in fake_db if "%(" in sql)


def test_audit_survives_missing_tables(monkeypatch):
    def boom(sql, args=None):
        raise RuntimeError("no table")
    monkeypatch.setattr(check, "fetch", boom)
    monkeypatch.setattr(check, "fetch_one", lambda sql, args=None: {})
    a = check.audit(D1, D2)
    assert a["deps"]["error"] == "RuntimeError" and isinstance(a["codes"], list)
    assert any(c.get("error") for c in a["codes"])


# ------------------------------------------------------------- หน้าเว็บ

@pytest.fixture
def client(fake_db):
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def test_dashboard_shows_audit_bar_until_audited(client, monkeypatch):
    assert "audit-bar" in client.get("/").get_data(as_text=True)
    assert "audit-bar" not in client.get("/?print=1").get_data(as_text=True)    # ไม่ติดไปในไฟล์ส่งออก
    monkeypatch.setenv("ER_DATA_AUDITED", "1")
    assert "audit-bar" not in client.get("/").get_data(as_text=True)


def test_check_page_renders_on_lan(client):
    r = client.get("/check")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "audit-sum" in html and "ER_DEPCODE" in html
    assert 'data-audit-key="er-audited:' in html


@pytest.mark.parametrize("remote, headers, ok", [
    ("10.1.2.3", {}, True),                                              # เข้าตรงจากเครื่องใน รพ.
    ("8.8.8.8", {}, False),                                              # เข้าตรงจากภายนอก
    ("8.8.8.8", {"X-Forwarded-For": "10.0.0.5"}, False),                 # header ปลอมจากเครื่องที่ไม่ใช่ proxy
    ("127.0.0.1", {"X-Forwarded-For": "192.168.1.20"}, True),            # ผ่าน Apache จากเครื่องใน รพ.
    ("127.0.0.1", {"X-Forwarded-For": "1.2.3.4"}, False),                # ผ่าน Apache จากภายนอก
    ("127.0.0.1", {"X-Forwarded-For": "173.245.48.9", "CF-Connecting-IP": "1.2.3.4"}, False),  # ผ่าน Cloudflare
    ("127.0.0.1", {"X-Forwarded-For": "1.2.3.4", "CF-Connecting-IP": "10.0.0.1"}, False),    # CF header ปลอม
    # 127.0.0.1 ไม่มี header = โปรเซสบนเครื่องแอปเอง (chromium ของปุ่มส่งออก / ผู้ดูแล curl บนเซิร์ฟเวอร์)
    ("127.0.0.1", {}, True),
    ("::1", {}, True),
    # X-Forwarded-For ปลอมผ่าน Apache: Apache ต่อ IP จริงไว้ท้ายสุด → ใช้ตัวท้าย
    ("127.0.0.1", {"X-Forwarded-For": "10.0.0.5, 1.2.3.4"}, False),
    # Cloudflare → Apache: ผู้ใช้ใส่ XFF ปลอม · Cloudflare ต่อ IP จริง · Apache ต่อ IP ของ Cloudflare
    ("127.0.0.1", {"X-Forwarded-For": "10.0.0.5, 1.2.3.4, 173.245.48.9", "CF-Connecting-IP": "1.2.3.4"}, False),
    # cloudflared บนเครื่องเดียวกัน (ต่อเข้ามาจาก 127.0.0.1 ตรง ไม่ผ่าน Apache)
    ("127.0.0.1", {"X-Forwarded-For": "1.2.3.4", "CF-Connecting-IP": "1.2.3.4"}, False),
    ("127.0.0.1", {"CF-Connecting-IP": "1.2.3.4"}, False),                              # ไม่มี XFF → 127.0.0.1
    ("127.0.0.1", {"X-Forwarded-For": "10.0.0.5", "CF-Connecting-IP": "1.2.3.4"}, False),  # XFF ภายในปลอม
    ("::1", {"CF-Connecting-IP": "1.2.3.4"}, False),
    ("8.8.8.8", {"CF-Connecting-IP": "10.0.0.1"}, False),                               # ไม่ใช่ proxy → ไม่เชื่อ header
])
def test_check_page_lan_only(client, remote, headers, ok):
    r = client.get("/check", headers=headers, environ_base={"REMOTE_ADDR": remote})
    assert r.status_code == (200 if ok else 403)
    page = client.get("/", headers=headers, environ_base={"REMOTE_ADDR": remote}).get_data(as_text=True)
    assert ("ตรวจข้อมูลก่อนใช้</a>" in page) == ok                       # ลิงก์ในแถบเตือนเฉพาะเครื่องที่เปิดได้


def test_check_page_lan_nets_and_open(client, monkeypatch):
    cf = {"X-Forwarded-For": "173.245.48.9", "CF-Connecting-IP": "1.2.3.4"}
    monkeypatch.setattr(app_mod, "LAN_NETS", app_mod._nets("1.2.3.0/24"))
    assert client.get("/check", headers=cf, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    # cloudflared: IP สาธารณะของ รพ. ที่ตั้งใน ER_LAN_NETS เข้าได้ · IP อื่น / XFF ปลอมเป็น IP ของ รพ. เข้าไม่ได้
    tunnel = {"X-Forwarded-For": "1.2.3.4", "CF-Connecting-IP": "1.2.3.4"}
    assert client.get("/check", headers=tunnel, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 200
    spoof = {"X-Forwarded-For": "1.2.3.4, 5.6.7.8", "CF-Connecting-IP": "5.6.7.8"}
    assert client.get("/check", headers=spoof, environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 403
    # cloudflared ไม่ส่ง XFF → 127.0.0.1 ไม่อยู่ใน ER_LAN_NETS → เข้าไม่ได้
    assert client.get("/check", headers={"CF-Connecting-IP": "1.2.3.4"},
                      environ_base={"REMOTE_ADDR": "127.0.0.1"}).status_code == 403
    monkeypatch.setattr(app_mod, "LAN_NETS", [])
    monkeypatch.setenv("ER_CHECK_OPEN", "1")
    assert client.get("/check", environ_base={"REMOTE_ADDR": "8.8.8.8"}).status_code == 200


# ------------------------------------------------------------- setup_check.py

@pytest.mark.parametrize("lines, risky", [
    (["GRANT USAGE ON *.* TO `er_ro`@`%`", "GRANT SELECT ON `hos`.* TO `er_ro`@`%`"], []),
    (["GRANT SELECT (hn, vn), SHOW VIEW ON `hos`.* TO `x`@`%`"], []),
    (["GRANT SELECT, INSERT, UPDATE ON `hos`.* TO `x`@`%`"], ["INSERT", "UPDATE"]),
    (["GRANT ALL PRIVILEGES ON *.* TO `root`@`localhost` WITH GRANT OPTION"], ["ALL PRIVILEGES", "GRANT OPTION"]),
])
def test_setup_check_flags_write_privileges(lines, risky):
    assert setup_check.risky_privs(lines) == risky


def test_setup_check_counts_only_hosxp_database_and_global():
    lines = ["GRANT SELECT ON `hos`.* TO `er_ro`@`10.0.0.%`",
             "GRANT SELECT, INSERT, UPDATE, DELETE ON `test`.* TO `er_ro`@`10.0.0.%`"]
    assert setup_check.risky_privs(lines, "hos") == []
    assert setup_check.risky_privs(lines, "test") == ["DELETE", "INSERT", "UPDATE"]
    assert setup_check.risky_privs(["GRANT INSERT ON *.* TO `x`@`%`"], "hos") == ["INSERT"]
    assert setup_check.risky_privs(["GRANT UPDATE ON `hos`.`ovst` TO `x`@`%`"], "hos") == ["UPDATE"]
