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
              "ER_PDX_DIAGTYPE", "ER_SVC_PT_TYPES", "ER_AUTOPSY_PT_TYPES", "ER_DCH_ADMIT", "ER_DATA_AUDITED",
              "ER_FT_STROKE_ICD", "ER_FT_STEMI_ICD", "ER_FT_HI_ICD", "ER_FT_ECG_ICD9", "ER_FT_CPR_ICD9"):
        assert k in text, k


def test_ems_row_shows_als_fr_split(monkeypatch):
    rows = [{"t": "01", "n": 50}, {"t": "04", "n": 200}, {"t": "06", "n": 315}, {"t": "02", "n": 10}]
    monkeypatch.setattr(queries, "fetch", lambda sql, args=None: rows if "o.ovstist t" in sql else [])
    monkeypatch.setattr(queries, "fetch_one", lambda sql, args=None: {})
    ems = next(i for i in queries.patient_profile(D1, D2, "all")["arrival"] if i["label"] == "EMS ALS/FR")
    assert ems["value"] == 515
    assert ems["parts"] == [{"label": "ALS", "value": 200}, {"label": "FR", "value": 315}]


# ---------------------------------------------------------- 19-22. Fast track

def test_fast_track_default_codes_reach_sql(fake_db):
    queries.build(D1, D2, "all")
    sql = _bound(fake_db)
    for frag in ("d.icd10 regexp '^(I60|I61|I62|I63|I64)'", "d.icd10 regexp '^(I210|I211|I212|I213|I219|I22)'",
                 "d.icd10 regexp '^(S06)'", "replace(c.icd9cm, '.', '') in ('8952')",
                 "replace(c.icd9cm, '.', '') in ('9960')", "regexp 'streptokinase|alteplase|tenecteplase'"):
        assert frag in sql, frag


def test_fast_track_env_codes_reach_sql(fake_db, monkeypatch):
    monkeypatch.setitem(queries.P, "ft_stroke", "^(I63|G45)")
    monkeypatch.setitem(queries.P, "ft_ecg", ("8952", "8951"))
    queries.fast_track(D1, D2, "all")
    sql = _bound(fake_db)
    assert "regexp '^(I63|G45)'" in sql and "in ('8952','8951')" in sql


def _ft_row(**kw):
    r = {"stroke": 0, "stemi": 0, "hi": 0, "tbi": 0, "cpr": 0, "defib": 0, "dch": 1, "ems": 0,
         "ecg": None, "lytic": None, "refer_m": None, "ge": None, "gv": None, "gm": None}
    r.update(kw)
    return r


def test_fast_track_aggregates(monkeypatch):
    rows = [
        _ft_row(stemi=1, dch=2, ems=1, ecg=4, lytic=25, refer_m=50),
        _ft_row(stemi=1, dch=2, ecg=12, lytic=60, refer_m=70),
        _ft_row(stemi=1, dch=3, ecg="", refer_m=None),               # ไม่มี ECG / admit
        _ft_row(stroke=1, dch=2, refer_m=40), _ft_row(stroke=1, dch=2, refer_m=-5),   # เวลาส่งต่อติดลบ = ไม่นับ
        _ft_row(stroke=1, dch=3, refer_m=30),                       # admit — ไม่นับใน DIDO
        _ft_row(hi=1, ge=4, gv=5, gm=6), _ft_row(hi=1, tbi=1, ge=2, gv=2, gm=4, dch=2),
        _ft_row(hi=1, ge=0, gv=0, gm=0), _ft_row(hi=1, ge=40, gv=5, gm=6), _ft_row(hi=1, ge=3, gv=4, gm=5),
        _ft_row(cpr=1, defib=1, dch=2), _ft_row(cpr=1, dch=6), _ft_row(cpr=1, dch=4, ems=1),
    ]
    monkeypatch.setattr(queries, "fetch", lambda sql, args=None: rows)
    ft = queries.fast_track(D1, D2, "all")
    mi = ft["stemi"]
    assert mi["n"] == 3 and mi["refer"] == 2 and mi["admit"] == 1 and mi["ems"] == 1
    assert mi["ecg"]["median"] == 8 and mi["ecg"]["n"] == 2 and mi["ecg"]["ok_pct"] == 50.0
    assert mi["lytic"] == 2 and mi["d2n"]["median"] == 42 and mi["d2n"]["status"] == "watch"
    assert mi["dido"]["median"] == 60 and mi["dido"]["status"] == "bad"
    st = ft["stroke"]
    assert st["n"] == 3 and st["dido"]["n"] == 1 and st["dido"]["median"] == 40 and st["dido"]["status"] == "good"
    hi = ft["hi"]
    assert (hi["n"], hi["tbi"], hi["mild"], hi["moderate"], hi["severe"], hi["no_gcs"]) == (5, 1, 1, 1, 1, 2)
    assert hi["gcs_pct"] == 60.0
    assert ft["cpr"]["rosc"] == 1 and ft["cpr"]["rosc_pct"] == 33.3 and ft["cpr"]["defib"] == 1


def test_fast_track_empty_and_alert(monkeypatch):
    monkeypatch.setattr(queries, "fetch", lambda sql, args=None: [])
    ft = queries.fast_track(D1, D2, "all")
    assert ft["stemi"]["d2n"]["median"] is None and ft["stemi"]["d2n"]["status"] == "flat"
    assert ft["cpr"]["rosc_pct"] == 0.0
    kpi = {"revisit_rate": 0, "d2d_cover": 100}
    tri = {"items": [], "missing_pct": 0}
    ttd = {"admit": {"over": 0}, "refer": {"over": 0}, "cutoff": 120}
    q = {"ama": 0}
    assert not any("STEMI" in a["issue"] for a in queries.alerts(kpi, tri, ttd, q, [], ft=ft))
    ft["stemi"]["d2n"] = queries._ft_time([50, 70], queries.FT_D2N_MIN)
    a = [x for x in queries.alerts(kpi, tri, ttd, q, [], ft=ft) if "STEMI" in x["issue"]]
    assert len(a) == 1 and a[0]["level"] == "high" and "60 นาที" in a[0]["issue"]
    ft["stemi"]["dido"] = queries._ft_time([40], queries.FT_STEMI_DIDO_MIN)          # ไม่เกิน 1.5 เท่า
    a = [x for x in queries.alerts(kpi, tri, ttd, q, [], ft=ft) if "STEMI" in x["issue"]]
    assert len(a) == 1 and "ได้ยาละลายลิ่มเลือด" in a[0]["issue"] and "DIDO" in a[0]["issue"]
