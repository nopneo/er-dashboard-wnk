"""ตั้งค่าร่วมของ test — ไม่ต่อฐานข้อมูลจริง (แทน fetch ด้วยตัวปลอม)"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ER_HOSPITAL_NAME", "โรงพยาบาลทดสอบ")     # ไม่ต้องอ่าน opdconfig

import db  # noqa: E402

#: ชื่อ alias ของคอลัมน์ใน select เช่น "count(*) n," "sum(...) ems_als from"
_ALIAS = re.compile(r"[\w)']\s+(\w+)\s*(?=,|\s+from\b)", re.I)


@pytest.fixture
def fake_db(monkeypatch):
    """แทน fetch ทุกโมดูล: ประกอบ SQL ด้วย db._bind (จับ % ที่ไม่ได้ escape / พารามิเตอร์หาย)
    แล้วคืนผลว่าง · คืน list ของ (sql, args, sql ที่แทนค่าแล้ว) ให้ test ตรวจ"""
    import check
    import nurse_report
    import queries

    calls = []

    def fetch(sql, args=None):
        calls.append((sql, args, db._bind(sql, args)))
        return []

    def fetch_one(sql, args=None):
        """แถวเดียวแบบ aggregate จริง: ทุกคอลัมน์ใน select (ชื่อ alias) เป็น 0"""
        fetch(sql, args)
        return {k: 0 for k in _ALIAS.findall(" ".join(sql.split()))}

    for mod in (queries, check):
        monkeypatch.setattr(mod, "fetch", fetch)
        monkeypatch.setattr(mod, "fetch_one", fetch_one)
    monkeypatch.setattr(nurse_report, "fetch", fetch)
    monkeypatch.setattr(db, "fetch", fetch)
    monkeypatch.setattr(db, "fetch_one", fetch_one)
    monkeypatch.setattr(queries, "CACHE_TTL", 0)
    nurse_report._cache._data.clear()
    queries._vmemo.clear()
    return calls
