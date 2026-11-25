"""รายชื่อเคสสำหรับทบทวน — กลับมา ER ซ้ำด้วยโรคเดิม · สีแดง (ระดับ 1) อยู่ ER เกินกำหนดก่อน Admit/Refer

ต่างจากทุกหน้าอื่นของแอป: มี HN และชื่อผู้ป่วย จึงเปิดได้เฉพาะเครื่องใน LAN + ต้องใส่รหัส + บันทึกการเปิดดู (app.py)
ชื่อแสดงนามสกุลบางส่วน (nurse_report._mask_surname) · ไม่มีใน API / ไฟล์ส่งออก
นับเคสด้วยเงื่อนไขเดียวกับตัวเลขบนแดชบอร์ด (queries.ELIG / _revisit_where / red_over) — จำนวนแถวต้องเท่ากับตัวเลขในการ์ด
"""

import queries
from db import fetch, num
from nurse_report import _icd_names, _mask_surname
from queries import DISPOSITION, LEVEL, LEVEL_NAME, FROM, P, RED_MAX_MIN, TH_MONTH, _dt, _where

#: แถวสูงสุดต่อหน้า — เกินนี้แสดงเท่านี้พร้อมบอกว่ายังมีอีก
MAX_ROWS = 500

KINDS = {
    "revisit": "กลับมา ER ซ้ำด้วยโรคเดิม",
    "red": f"สีแดงอยู่ ER เกิน {RED_MAX_MIN} นาที",
}


def _name(r):
    n = f"{r.get('pname') or ''}{r.get('fname') or ''} {r.get('lname') or ''}".strip()
    return _mask_surname(n) if n else "(ไม่มีชื่อ)"


def _dch(code):
    code = int(num(code, 0))
    return next((label for _, label, codes, _ in DISPOSITION if code in codes), "อื่น ๆ / ยังไม่ระบุ")


def _when(v):
    d = _dt(v)
    return f"{d.day} {TH_MONTH[d.month - 1]} {str(d.year + 543)[2:]} {d:%H:%M}"


def _span(minutes):
    m = int(num(minutes, 0))
    return f"{m // 60} ชม. {m % 60} นาที" if m >= 60 else f"{m} นาที"


def _dx(code, names):
    code = (code or "").strip()
    return f"{code} {names.get(code, '')}".strip() if code else "ไม่ได้ลงโรคหลัก"


def revisit_cases(d1, d2, shift):
    """ผู้ป่วยที่กลับมา ER ซ้ำด้วยโรคเดิมใน 48 ชม. — แถวละ 1 ครั้งที่กลับมา พร้อม ER visit ก่อนหน้า (ล่าสุด) ที่ทำให้นับ"""
    prior = ("(select p.vn from ovst po join er_regist p on p.vn = po.vn where "
             + queries._revisit_where(queries.REVISIT_MATCH_LEN) + " order by p.enter_er_time desc limit 1)")
    rows = fetch(f"""
        select t.vn, t.hn, pt.pname, pt.fname, pt.lname, t.t, t.lvl, t.dch, t.pdx,
          p.enter_er_time ptime, p.er_dch_type pdch, timestampdiff(minute, p.enter_er_time, t.t) gap,
          (select x.icd10 from ovstdiag x where x.vn = p.vn and x.diagtype = %(pdx)s limit 1) ppdx
        from (select e.vn, o.hn, e.enter_er_time t, {LEVEL} lvl, e.er_dch_type dch, {prior} pvn,
                (select x.icd10 from ovstdiag x where x.vn = e.vn and x.diagtype = %(pdx)s limit 1) pdx
              {FROM} {_where(d1, d2, shift)} and {queries.ELIG} and {queries.REVISIT}) t
          join er_regist p on p.vn = t.pvn
          left join patient pt on pt.hn = t.hn
        order by t.t limit {MAX_ROWS + 1}""", P)
    names = _icd_names({r["pdx"] for r in rows} | {r["ppdx"] for r in rows})
    items = [{"hn": r["hn"], "name": _name(r), "prev_when": _when(r["ptime"]), "prev_dx": _dx(r["ppdx"], names),
              "prev_dch": _dch(r["pdch"]), "when": _when(r["t"]), "level": int(num(r["lvl"], 5)),
              "level_name": LEVEL_NAME.get(int(num(r["lvl"], 5)), ""), "dx": _dx(r["pdx"], names),
              "dch": _dch(r["dch"]), "gap": _span(r["gap"])} for r in rows[:MAX_ROWS]]
    return {"kind": "revisit", "rows": items, "truncated": len(rows) > MAX_ROWS}


def red_cases(d1, d2, shift):
    """ระดับ 1 (สีแดง) ที่ Admit/Refer แล้วอยู่ ER เกิน RED_MAX_MIN นาที (นับแบบบล็อก 17: เข้า ER → ลงทะเบียน Admit / เวลาส่งต่อ)"""
    rows = fetch(f"""
        select t.vn, t.hn, pt.pname, pt.fname, pt.lname, t.t, t.dch, t.m, t.pdx
        from (select e.vn, o.hn, e.enter_er_time t, e.er_dch_type dch,
                case when e.er_dch_type in %(dch_admit)s then timestampdiff(minute, e.enter_er_time,
                       (select min(timestamp(i.regdate, i.regtime)) from ipt i where i.vn = e.vn))
                     else timestampdiff(minute, e.enter_er_time,
                       (select min(timestamp(x.refer_date, x.refer_time)) from referout x where x.vn = e.vn)) end m,
                (select x.icd10 from ovstdiag x where x.vn = e.vn and x.diagtype = %(pdx)s limit 1) pdx
              {FROM} {_where(d1, d2, shift)} and {LEVEL} = 1 and e.er_dch_type in %(dch_ar)s) t
          left join patient pt on pt.hn = t.hn
        where t.m > {int(RED_MAX_MIN)} and t.m <= 1440
        order by t.m desc limit {MAX_ROWS + 1}""", P)
    names = _icd_names({r["pdx"] for r in rows})
    items = [{"hn": r["hn"], "name": _name(r), "when": _when(r["t"]), "minutes": int(num(r["m"], 0)),
              "span": _span(r["m"]), "dch": _dch(r["dch"]), "dx": _dx(r["pdx"], names)} for r in rows[:MAX_ROWS]]
    return {"kind": "red", "rows": items, "truncated": len(rows) > MAX_ROWS}


def get(kind, d1, d2, shift):
    return {"revisit": revisit_cases, "red": red_cases}[kind](d1, d2, shift)
