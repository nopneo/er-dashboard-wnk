"""กลับมา ER ซ้ำ ≤ 48 ชม. ต้องเป็นโรคหรืออาการเดิม (โรคหลักครั้งนี้กับครั้งก่อนมี ICD-10 ตรงกัน N ตัวแรก) — ไม่ต่อฐานข้อมูลจริง"""

import pytest

import app as app_mod
import queries

D1, D2 = "2026-11-01", "2026-11-25"


def test_default_requires_same_principal_diagnosis_group(fake_db):
    queries.build(D1, D2, "all")
    sql = " ".join(next(b for _, _, b in fake_db if "elig" in b and "rv" in b).split())
    assert "left(b.icd10, 3) = left(a.icd10, 3)" in sql
    assert "a.vn = e.vn and a.diagtype = '1' and b.vn = p.vn and b.diagtype = '1'" in sql     # โรคหลักทั้งสองครั้ง
    assert "interval 48 hour" in sql                                                           # เกณฑ์เวลาเดิมยังอยู่


def test_match_length_zero_keeps_the_old_rule():
    assert "ovstdiag a" in queries._revisit_sql(3)
    assert "ovstdiag a" not in queries._revisit_sql(0)
    assert "interval 48 hour" in queries._revisit_sql(0)


@pytest.mark.parametrize("raw,want", [("", (3, False)), ("3", (3, False)), ("0", (0, False)), ("5", (5, False)),
                                      ("6", (6, False)), ("7", (3, True)), ("x", (3, True)), ("-1", (3, True))])
def test_match_length_env_parsing(raw, want):
    assert queries._match_len(raw) == want


def test_length_reaches_sql_as_a_parameter(fake_db, monkeypatch):
    monkeypatch.setitem(queries.P, "rv_len", 4)
    queries.build(D1, D2, "all")
    assert any("left(b.icd10, 4) = left(a.icd10, 4)" in " ".join(b.split()) for _, _, b in fake_db)


def test_kpi_and_alert_tell_that_it_is_same_disease(monkeypatch):
    monkeypatch.setattr(queries, "_visits", lambda *a: [
        {"lvl": 3, "ct": "non", "dch": 1, "d2d": 5, "elig": True, "rv": i < 2, "hpi": False} for i in range(10)])
    k = queries.kpi_summary(D1, D2, "all")
    assert k["revisit"] == 2 and k["revisit_base"] == 10 and k["revisit_match_len"] == queries.REVISIT_MATCH_LEN
    a = queries.alerts(k, {"items": [], "missing_pct": 0}, {"admit": {"over": 0}, "refer": {"over": 0}, "cutoff": 120},
                       {"ama": 0}, [])
    assert any("ด้วยโรคเดิม" in x["issue"] for x in a)


@pytest.fixture
def client(fake_db):
    app_mod.app.config["TESTING"] = True
    return app_mod.app.test_client()


def test_dashboard_labels_revisit_as_same_disease(client):
    html = client.get("/").get_data(as_text=True)
    assert "Revisit ≤ 48 ชม. · โรคเดิม" in html and "โรคหลักเดิม (ICD-10 3 ตัวแรก)" in html
