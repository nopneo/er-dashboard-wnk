"""บล็อก 3: แยก Non-trauma / Trauma / จราจร / รับบริการ ต่อระดับ · รายงานพยาบาล: คอลัมน์ระดับ 1-2 และรหัสโรค Z"""

from datetime import date
from io import BytesIO

import pytest
from openpyxl import load_workbook

import nurse_report
import queries

D1, D2 = "2026-10-01", "2026-10-31"


def _v(lvl, ct, dch=1, d2d=5):
    return {"lvl": lvl, "ct": ct, "dch": dch, "d2d": d2d, "elig": True, "rv": False, "hpi": False}


def test_triage_splits_case_types_per_level_and_in_total(monkeypatch):
    rows = [_v(1, "traf"), _v(1, "tr"), _v(1, "non"), _v(2, "non"), _v(5, "svc"), _v(5, "svc"), _v(5, "non")]
    monkeypatch.setattr(queries, "_visits", lambda *a: rows)
    monkeypatch.setattr(queries, "fetch_one", lambda sql, args=None: {"c": 0})
    t = queries.triage(D1, D2, "all")
    lv = {i["level"]: i for i in t["items"]}
    assert lv[1]["types"] == {"non": 1, "tr": 1, "traf": 1, "svc": 0}
    assert lv[5]["types"] == {"non": 1, "tr": 0, "traf": 0, "svc": 2}
    assert all(sum(i["types"].values()) == i["value"] for i in t["items"])      # รวมทุกประเภท = ราย
    assert t["types"] == {"non": 3, "tr": 1, "traf": 1, "svc": 2} and sum(t["types"].values()) == t["total"]


def test_case_type_uses_same_rules_as_block_5(fake_db):
    queries.build(D1, D2, "all")
    sql = " ".join(next(b for _, _, b in fake_db if "ct," in b or " ct " in b).split())
    assert "then 'svc'" in sql and "like 'V%'" in sql and "regexp '^[ST]'" in sql
    assert sql.index("then 'svc'") < sql.index("then 'traf'") < sql.index("then 'tr'")     # รับบริการก่อน จราจรก่อน trauma ทั่วไป


def _row(d, lvl, pdx, ex="N", pt=3):
    return {"vstdate": d, "tm": "17:00", "pt": pt, "lvl": lvl, "dch": 1, "pdx": pdx, "ex": ex,
            "exc": "x1", "exn": "พว.ทดสอบ ตัวอย่าง"}


@pytest.fixture
def nurse(monkeypatch):
    rows = [_row("2026-10-05", 1, "I460"), _row("2026-10-05", 2, "S0600", ex="D"), _row("2026-10-06", 4, "Z480"),
            _row("2026-10-07", 5, "z230", pt=4), _row("2026-10-07", 3, "J00"), _row("2026-11-02", 2, "Z000")]
    monkeypatch.setattr(nurse_report, "_rows", lambda d1, d2: rows)
    monkeypatch.setattr(nurse_report, "_holidays", lambda d1, d2: set())
    monkeypatch.setattr(nurse_report, "_icd_names", lambda codes: {})
    return nurse_report._build("2026-10-01", "2026-11-05", ("2026-10-01", "2026-11-05"))


def test_nurse_monthly_counts_level_1_2_and_z_codes(nurse):
    oct_, nov = nurse["monthly"]
    assert (oct_["total"], oct_["l12"], oct_["z"]) == (5, 2, 2)           # Z480 และ z230 (ตัวพิมพ์เล็กก็นับ) ไม่นับซ้ำใน l12 / l35
    assert (nov["total"], nov["l12"], nov["z"]) == (1, 0, 1)           # Z000 ระดับ 2 → อยู่ในคอลัมน์ Z ไม่ใช่ระดับ 1-2
    assert (oct_["l35"], nov["l35"]) == (1, 0)
    for grp in (*nurse["monthly"], *nurse["yearly"], *nurse["daily"], nurse["kpi"]):
        assert grp["l12"] + grp["l35"] + grp["z"] == grp["total"], grp           # 3 กลุ่มไม่ซ้อนกัน รวมกัน = ผู้ป่วยช่วงนั้น
    assert (nurse["kpi"]["l12"], nurse["kpi"]["l35"], nurse["kpi"]["z"]) == (2, 1, 3)
    assert nurse["kpi"]["l345"] == 1                                       # l345 เดิมของ kpi = เคสที่มีการตรวจ (ไม่นับงานบริการ) ไม่ถูกทับด้วย l35
    assert nurse["yearly"][0]["l12"] == 2 and nurse["yearly"][0]["z"] == 3
    assert nurse["daily"][4]["l12"] == 2 and nurse["daily"][4]["date"] == date(2026, 10, 5)


def test_nurse_excel_has_new_columns_without_moving_chart_columns(nurse):
    wb = load_workbook(BytesIO(nurse_report.to_xlsx(nurse, "รพ.ทดสอบ")))
    ws = wb["รายเดือน"]
    head = [c.value for c in ws[1]]
    assert head[5:11] == ["ผู้ป่วยในช่วงเวลา", "พยาบาลตรวจแทนแพทย์", "% พยาบาลตรวจ", "แพทย์ตรวจ", "พยาบาลให้บริการ", "อื่น ๆ/ไม่ระบุ"]
    assert head[11:14] == ["ระดับ 1-2 (ไม่รวมรหัส Z)", "ระดับ 3-5 (ไม่รวมรหัส Z)", "รหัสโรคหลัก Z (ทุกระดับ)"]   # ต่อท้ายกลุ่มเดิม กราฟอ้างคอลัมน์ 7, 9, 10
    assert [c.value for c in ws[2]][11:14] == [2, 1, 2]
    assert [c.value for c in ws[ws.max_row]][11:14] == [2, 1, 3]        # แถวรวม
    assert ws._charts and ws._charts[0].anchor._from.col == 16           # ไม่ทับคอลัมน์ข้อมูล (Q)


def test_distribution_zip_lists_every_local_module_the_app_imports(monkeypatch):
    import ast
    import os

    import build_dist

    here = os.path.dirname(os.path.abspath(build_dist.__file__))

    def missing(files):
        mods = set()
        for f in files:
            if f.endswith(".py"):
                for n in ast.walk(ast.parse(open(os.path.join(here, f), encoding="utf-8").read())):
                    if isinstance(n, ast.Import):
                        mods |= {a.name.split(".")[0] for a in n.names}
                    elif isinstance(n, ast.ImportFrom) and n.module:
                        mods.add(n.module.split(".")[0])
        return {m for m in mods if os.path.exists(os.path.join(here, m + ".py")) and m + ".py" not in files}

    assert missing(build_dist.FILES) == set()
    assert missing([f for f in build_dist.FILES if f != "privacy.py"]) == {"privacy"}      # test จับได้จริงเมื่อลืมใส่
