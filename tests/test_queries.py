"""รหัสของ HOSxP ที่ตั้งได้ด้วย env ER_* — ส่งเข้า SQL เป็นพารามิเตอร์ และค่าเริ่มต้นได้ SQL แบบเดิม"""

import os

import nurse_report
import queries

D1, D2 = "2026-10-01", "2026-10-27"


def _bound(calls):
    return "\n".join(" ".join(b.split()) for _, _, b in calls)


def test_every_dashboard_query_is_parameterised(fake_db):
    queries.build(D1, D2, "all")
    nurse_report.build(D1, D2)
    assert len(fake_db) > 20
    for sql, args, _ in fake_db:
        if "%(" in sql:               # มีพารามิเตอร์ → ต้องส่ง args มาด้วย (bind สำเร็จแล้วใน fake_db)
            assert args is not None, sql


def test_default_codes_give_the_original_sql(fake_db):
    queries.build(D1, D2, "all")
    nurse_report.build(D1, D2)
    sql = _bound(fake_db)
    for frag in ("o.ovstist in ('04','06')",            # EMS ALS / FR
                 "o.ovstist in ('03')",                 # รับส่งต่อ
                 "ifnull(o.ovstist, '') not in ('02')",  # มาตามนัด (อัตรากลับมาซ้ำ)
                 "ifnull(e.er_pt_type, 0) in (4)",      # รับบริการ
                 "e.er_dch_type in (3)", "e.er_dch_type in (2)", "e.er_dch_type in (2,3)",
                 "e.er_dch_type in (7,8)",
                 "d.diagtype = '1'", "o.main_dep not in ('011','999')",
                 "like 'Z%'", "like 'V%'", "'%Y-%m'", "'%H:%i'",
                 "ex.provider_type_code in ('03')", "ex.position_id in (1)"):
        assert frag in sql, frag
    assert "%%" not in sql


def test_env_codes_reach_sql(fake_db, monkeypatch):
    monkeypatch.setitem(queries.P, "ems", ("04", "07"))
    monkeypatch.setitem(queries.P, "dch_admit", (3, 9))
    monkeypatch.setitem(queries.P, "not_dep", ("020", "999"))
    queries.build(D1, D2, "all")
    sql = _bound(fake_db)
    assert "o.ovstist in ('04','07')" in sql
    assert "e.er_dch_type in (3,9)" in sql
    assert "o.main_dep not in ('020','999')" in sql


def test_codes_parse_and_reject_bad_values(monkeypatch):
    monkeypatch.setattr(queries, "CODE_ERRORS", [])
    monkeypatch.setenv("ER_X", " 04, 07 ,04")
    assert queries._codes("ER_X", "04") == ("04", "07")
    monkeypatch.setenv("ER_X", "04,'; drop")
    assert queries._codes("ER_X", "04") == ("04",) and queries.CODE_ERRORS == ["ER_X=04,'; drop"]
    monkeypatch.setenv("ER_Y", "3,a")
    assert queries._codes("ER_Y", "3", ints=True) == (3,)
    monkeypatch.delenv("ER_Z", raising=False)
    assert queries._codes("ER_Z", "1,5", ints=True) == (1, 5)


def test_disposition_groups_follow_codes(monkeypatch):
    rows = [{"t": 1, "n": 5}, {"t": 3, "n": 2}, {"t": 9, "n": 3}, {"t": 4, "n": 1}]
    monkeypatch.setattr(queries, "fetch", lambda sql, args=None: rows)
    d = {x["key"]: x["value"] for x in queries.disposition(D1, D2, "all")["items"]}
    assert d["admit"] == 2 and d["other"] == 4
    monkeypatch.setattr(queries, "DISPOSITION", [x if x[0] != "admit" else (*x[:2], (3, 9), x[3])
                                                 for x in queries.DISPOSITION])
    d = {x["key"]: x["value"] for x in queries.disposition(D1, D2, "all")["items"]}
    assert d["admit"] == 5 and d["other"] == 1


def test_env_example_lists_code_settings():
    text = open(os.path.join(os.path.dirname(queries.__file__), ".env.example"), encoding="utf-8").read()
    for k in ("ER_WALKIN_CODES", "ER_APPT_CODES", "ER_REFER_IN_CODES", "ER_EMS_ALS_CODES", "ER_EMS_FR_CODES",
              "ER_PDX_DIAGTYPE", "ER_SVC_PT_TYPES", "ER_AUTOPSY_PT_TYPES", "ER_DCH_ADMIT", "ER_DATA_AUDITED"):
        assert k in text, k
