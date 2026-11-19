"""ป้องกันการย้อนหาตัวบุคคลจากตัวเลขรวมในหน้าสาธารณะ (คนนอก รพ.) — แบบเดียวกับ anc / lr / ipd dashboard

ปัญหา: หน้าแดชบอร์ดและ /api/data.json เลือกช่วงวันที่ได้แคบถึง 1 วัน — บล็อก CPR แสดงจำนวน CPR และ ROSC
(CPR แล้วได้ส่งต่อ / Admit) จึงลบกันได้ว่าวันนั้นมีคนทำ CPR แล้วไม่รอด ทั้งที่แดชบอร์ดตั้งใจไม่แสดงยอดเสียชีวิต
คนที่รู้ว่าใครมา ER วันไหนจึงรู้ผลของคนนั้น

มาตรการ (เฉพาะคนนอก รพ. — เครื่องใน รพ. / ER_LAN_NETS เห็นตัวเลขเต็มเหมือนเดิม):
1. ช่วงวันที่ปัดเป็นเดือนเต็ม: วันเริ่ม = วันที่ 1 · วันสิ้นสุด = สิ้นเดือน (เดือนปัจจุบันถึงวันนี้)
2. บล็อก CPR ที่มี 1 ถึง MIN_CELL-1 ราย แสดงเป็น "<5" ทั้งบล็อก (จำนวน · EMS · ROSC · Defibrillation · ส่งต่อ · Admit)

ER_PUBLIC_MIN_CELL = ขนาดช่องขั้นต่ำ (ค่าเริ่มต้น 5) · 0 = ปิดมาตรการทั้งหมด (ไม่แนะนำ)
"""

import calendar
import copy
import os
from datetime import date

MIN_CELL = max(int(os.getenv("ER_PUBLIC_MIN_CELL", "5") or 0), 0)

#: ช่องนับจำนวนของบล็อก CPR (queries.fast_track) — ซ่อนพร้อมกันทั้งชุด ไม่งั้นลบกันได้
CPR_COUNTS = ("n", "ems", "refer", "admit", "rosc", "defib")
CPR_PCTS = ("refer_pct", "rosc_pct")


def enabled():
    return MIN_CELL > 0


def label():
    return f"<{MIN_CELL}"


def month_window(d1, d2, today=None):
    """(d1, d2) แบบ ISO string → ปัดเป็นเดือนเต็ม · วันสิ้นสุดไม่เกินวันนี้"""
    today = today or date.today()
    a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    end = b.replace(day=calendar.monthrange(b.year, b.month)[1])
    return a.replace(day=1).isoformat(), min(end, today).isoformat()


def public_ctx(ctx):
    """สำเนาของข้อมูลหน้า (ต้นฉบับอยู่ใน cache ที่ใช้ร่วมกับเครื่องใน รพ. — ห้ามแก้) ที่ซ่อนบล็อก CPR ช่องเล็กแล้ว"""
    if not enabled():
        return ctx
    c = copy.copy(ctx)
    c["public_privacy"] = True
    ft = ctx.get("ft")
    cpr = (ft or {}).get("cpr") if isinstance(ft, dict) else None
    if isinstance(cpr, dict) and isinstance(cpr.get("n"), int) and 0 < cpr["n"] < MIN_CELL:
        hidden = dict(cpr)
        for k in CPR_COUNTS:
            if k in hidden:
                hidden[k] = label()
        for k in CPR_PCTS:
            if k in hidden:
                hidden[k] = "–"
        c["ft"] = dict(ft, cpr=hidden)
    return c
