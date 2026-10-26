"""รวม SQL ทุกบล็อกของ ER Dashboard (อ้างอิงสคีมา HOSxP)

ตัวเลขทุกตัวมาจากฐานข้อมูลจริง เป็นยอดรวมเท่านั้น (ไม่มี HN/ชื่อผู้ป่วยออกจากฐาน)

กติกาเดียวกับแอป ER Shift (สรุปเวร ER):
  - ผู้ป่วย ER = er_regist ที่มี enter_er_time · เวรนับจากเวลาเข้า ER
      00:00–08:00 ดึก (D) · 08:01–16:00 เช้า (C) · 16:01–23:59 บ่าย (B)
  - ระดับ = er_emergency_level_id ถ้าว่างใช้ er_emergency_type.export_code
  - Trauma = มีรหัส ICD-10 กลุ่ม S/T · จราจร = กลุ่ม V · รับบริการ = er_pt_type 4
  - EMS = ovst.ovstist 04 (ALS) / 06 (FR)

เวลาที่ใช้ได้จริงของ รพ.วังน้ำเขียว (ตรวจข้อมูล พ.ค.–ต.ค. 2569):
  - er_regist.doctor_tx_time / finish_time เป็นค่าตั้งต้น (+5 / +10 นาทีทุกราย) ใช้วัดไม่ได้
  - door_to_doctor_second, triage_datetime, leave_datetime ฯลฯ ว่างเกือบทั้งหมด
  - เวลาพบแพทย์ = เร็วที่สุดของ กดเริ่มตรวจ (service5) / สั่ง Lab / สั่ง X-ray / สั่งยา (ดู D2D ด้านล่าง)
  - เวลา admit = ipt.regdate+regtime · เวลา refer = referout.refer_date+refer_time
"""

import os
import re
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from db import fetch, fetch_one, num

# ---------------------------------------------------------------- helpers

SHIFTS = {"all": "ทุกเวร", "D": "เวรดึก", "C": "เวรเช้า", "B": "เวรบ่าย"}
SHIFT_TIME = {"D": "00:00–08:00", "C": "08:01–16:00", "B": "16:01–23:59"}


def _shift_case(col):
    return (f"case when time({col}) < '08:01:00' then 'D' "
            f"when time({col}) < '16:01:00' then 'C' else 'B' end")


SHIFT_CASE = _shift_case("e.enter_er_time")
LEVEL = "coalesce(e.er_emergency_level_id, least(et.export_code, 5), 5)"
TRAUMA = "exists(select 1 from ovstdiag x where x.vn = e.vn and x.icd10 regexp '^[STV]')"
SVC = "ifnull(e.er_pt_type, 0) = 4"

FROM = ("from er_regist e join ovst o on o.vn = e.vn "
        "left join er_emergency_type et on et.er_emergency_type = e.er_emergency_type ")

# ---- เวลาพบแพทย์ = "การกระทำแรกของแพทย์" คือเวลาที่เร็วที่สุดของ
#   กดเริ่มตรวจ (service_time.service5) · สั่ง Lab (lab_head) · สั่ง X-ray (xray_report) · สั่งยา (opitemrece+drugitems)
# ER มักกดเริ่มตรวจตอนเปิดชาร์ตลงบันทึก (ช้ากว่าสั่ง Lab ครั้งแรกเฉลี่ย ~20 นาที) จึงใช้ปุ่มเดียวไม่ได้
# แต่ละแหล่งนับเฉพาะ 0–240 นาทีหลังเข้า ER (ติดลบ = รายการจาก OPD ก่อนย้ายมา ER, เกิน 4 ชม. = ลืมกด)
D2D_MAX = 240

#: คอลัมน์ service_time ที่ ER กด "เริ่มตรวจ" — แต่ละ รพ. ตั้งค่า HOSxP ต่างกัน (รพ.วังน้ำเขียว = service5)
_col = os.getenv("ER_ST_DOCTOR_COL", "service5")
ST_DOCTOR = _col if re.fullmatch(r"service\d{1,2}", _col) else "service5"

# service_time เก็บเป็น TIME ของวันมา — ข้ามเที่ยงคืนต้องบวก 1 วัน
_S5_RAW = f"timestampdiff(minute, e.enter_er_time, timestamp(date(e.enter_er_time), s.{ST_DOCTOR}))"
_S5 = (f"case when s.{ST_DOCTOR} is null then null when {_S5_RAW} < -60 then {_S5_RAW} + 1440 "
       f"else {_S5_RAW} end")


def _first_order(table, ts, extra=""):
    return (f"(select min(timestampdiff(minute, e.enter_er_time, {ts})) from {table} {extra} "
            f"where x.vn = e.vn and {ts} between e.enter_er_time "
            f"and e.enter_er_time + interval {D2D_MAX} minute)")


_SOURCES = [
    f"case when ({_S5}) between 0 and {D2D_MAX} then ({_S5}) end",
    _first_order("lab_head x", "timestamp(x.order_date, x.order_time)"),
    _first_order("xray_report x", "timestamp(x.request_date, x.request_time)"),
    _first_order("opitemrece x", "timestamp(x.rxdate, x.rxtime)", "join drugitems di on di.icode = x.icode"),
]
#: ใช้ใน select ชั้นในเท่านั้น (subquery หนัก — คำนวณครั้งเดียวต่อแถวแล้วค่อย aggregate ชั้นนอก)
D2D = "nullif(least(" + ", ".join(f"ifnull({s}, 99999)" for s in _SOURCES) + "), 99999)"
FROM_ST = FROM + "left join service_time s on s.vn = e.vn "

#: กลับมา ER ซ้ำภายใน 48 ชม. (ไม่นับมารับบริการตามนัด เช่น ทำแผล ฉีดยา ทั้งครั้งก่อนและครั้งนี้)
REVISIT = ("exists(select 1 from ovst po join er_regist p on p.vn = po.vn "
           "where po.hn = o.hn and p.vn <> e.vn and ifnull(p.er_pt_type, 0) <> 4 "
           "and po.vstdate between e.vstdate - interval 2 day and e.vstdate "
           "and p.enter_er_time < e.enter_er_time "
           "and p.enter_er_time >= e.enter_er_time - interval 48 hour)")

#: เป้าหมายเวลาพบแพทย์ (นาที) ตามระดับความเร่งด่วน
D2D_TARGET = {1: 5, 2: 10, 3: 30, 4: 60, 5: 120}
D2D_TARGET_LABEL = {1: "ทันที", 2: "≤ 10 นาที", 3: "≤ 30 นาที", 4: "≤ 60 นาที", 5: "≤ 120 นาที"}
LEVEL_NAME = {1: "Resuscitation", 2: "Emergency", 3: "Urgent", 4: "Semi-urgent", 5: "Non-urgent"}
LEVEL_COLOR = {1: "#b91c1c", 2: "#ef4444", 3: "#f59e0b", 4: "#22a06b", 5: "#3b82f6"}

#: ER → Admit/Refer เกินกี่นาทีถือว่าค้างใน ER นาน
BOARDING_MIN = int(os.getenv("ER_BOARDING_MIN", "120"))


def _where(d1, d2, shift):
    sc = "" if shift == "all" else f" and ({SHIFT_CASE}) = '{shift}' "
    return f"where e.vstdate between '{d1}' and '{d2}' and e.enter_er_time is not null {sc}"


def _pct(part, whole, digits=1):
    return round(part * 100.0 / whole, digits) if whole else 0.0


def _delta(cur, prev):
    if not prev:
        return None
    return round((cur - prev) * 100.0 / prev, 1)


def _days(d1, d2):
    return (date.fromisoformat(d2) - date.fromisoformat(d1)).days + 1


def _prev_range(d1, d2):
    span = _days(d1, d2)
    p2 = date.fromisoformat(d1) - timedelta(days=1)
    return (p2 - timedelta(days=span - 1)).isoformat(), p2.isoformat()


# ---------------------------------------------------------------- 1. KPI

def _kpi_raw(d1, d2, shift):
    r = fetch_one(f"""
        select count(*) n, sum(lvl = 1) l1, sum(lvl = 2) l2,
          sum(dch = 3) admit, sum(dch = 2) refer,
          avg(d2d) d2d, sum(d2d is not null) d2d_n, sum(not svc) nsvc, sum(not svc and rv) rv
        from (select {LEVEL} lvl, e.er_dch_type dch, {D2D} d2d, {SVC} svc, {REVISIT} rv
              {FROM_ST} {_where(d1, d2, shift)}) t""")
    return {k: num(v) for k, v in r.items()}


def kpi_summary(d1, d2, shift):
    p1, p2 = _prev_range(d1, d2)
    cur, prev = _kpi_raw(d1, d2, shift), _kpi_raw(p1, p2, shift)
    days = _days(d1, d2)
    n = cur["n"]
    l12 = cur["l1"] + cur["l2"]
    rv_rate, rv_prev = _pct(cur["rv"], cur["nsvc"]), _pct(prev["rv"], prev["nsvc"])
    d2d, d2d_prev = round(cur["d2d"], 1), round(prev["d2d"], 1)
    return {
        "period": {"d1": d1, "d2": d2, "prev_d1": p1, "prev_d2": p2, "days": days},
        "visits": n, "visits_delta": _delta(n, prev["n"]),
        "per_day": round(n / days, 1) if days else 0,
        "l1": cur["l1"], "l2": cur["l2"], "l12": l12, "l12_pct": _pct(l12, n),
        "admit": cur["admit"], "admit_pct": _pct(cur["admit"], n),
        "refer": cur["refer"], "refer_pct": _pct(cur["refer"], n),
        "d2d": d2d, "d2d_delta": _delta(d2d, d2d_prev),
        "d2d_n": cur["d2d_n"], "d2d_cover": _pct(cur["d2d_n"], n),
        "revisit": cur["rv"], "revisit_rate": rv_rate, "revisit_delta": _delta(rv_rate, rv_prev),
    }


# ------------------------------------------- 1b. ระดับ 3-5 ช่วงเย็น 16:00–20:00

EVENING = ("16:00", "20:00")


def _evening_raw(d1, d2):
    return num(fetch_one(f"""
        select count(*) n {FROM}
        {_where(d1, d2, "all")} and {LEVEL} between 3 and 5
          and time(e.enter_er_time) >= '{EVENING[0]}:00' and time(e.enter_er_time) < '{EVENING[1]}:00'
        """).get("n"))


def evening_low(d1, d2):
    """ผู้ป่วยระดับ 3-5 ที่เข้า ER ช่วง 16:00–20:00 เฉลี่ยต่อวัน (ไม่แยกเวร/ไม่แยกพยาบาล-แพทย์)"""
    p1, p2 = _prev_range(d1, d2)
    days = _days(d1, d2)
    n, prev = _evening_raw(d1, d2), _evening_raw(p1, p2)
    per_day = round(n / days, 1) if days else 0
    return {"n": n, "per_day": per_day, "delta": _delta(n, prev),
            "time": f"{EVENING[0]}–{EVENING[1]}"}


# ------------------------------------------------ 2. แนวโน้มรายเดือน

def visit_trend(d2, shift, months=9):
    end = date.fromisoformat(d2).replace(day=1)
    start = end
    for _ in range(months - 1):
        start = (start - timedelta(days=1)).replace(day=1)
    last = (end + timedelta(days=31)).replace(day=1) - timedelta(days=1)
    rows = fetch(f"""
        select date_format(e.vstdate, '%Y-%m') ym, count(*) n, sum({TRAUMA}) tr,
               sum(e.er_dch_type = 3) admit
        {FROM} {_where(start.isoformat(), last.isoformat(), shift)} group by ym""")
    by = {r["ym"]: r for r in rows}
    out, cur = [], start
    for _ in range(months):
        r = by.get(cur.strftime("%Y-%m"), {})
        n, tr = num(r.get("n")), num(r.get("tr"))
        out.append({"label": TH_MONTH[cur.month - 1], "visits": n,
                    "trauma": tr, "nontrauma": n - tr, "admit": num(r.get("admit"))})
        cur = (cur + timedelta(days=31)).replace(day=1)
    return out


# ------------------------------------------- 3. ระดับความเร่งด่วน (Triage)

def triage(d1, d2, shift):
    rows = fetch(f"""
        select lvl, count(*) n, avg(d2d) d2d, sum(d2d is not null) d2d_n, sum(dch in (2, 3)) ar
        from (select {LEVEL} lvl, e.er_dch_type dch, {D2D} d2d
              {FROM_ST} {_where(d1, d2, shift)}) t group by lvl""")
    by = {int(num(r["lvl"])): r for r in rows}
    total = sum(num(r["n"]) for r in rows)
    items = []
    for lv in range(1, 6):
        r = by.get(lv, {})
        n, d2d_n = num(r.get("n")), num(r.get("d2d_n"))
        d2d = round(num(r.get("d2d")), 1) if d2d_n else None
        tgt = D2D_TARGET[lv]
        status = ("flat" if d2d is None else "good" if d2d <= tgt
                  else "watch" if d2d <= tgt * 1.5 else "bad")
        items.append({"level": lv, "name": LEVEL_NAME[lv], "color": LEVEL_COLOR[lv],
                      "value": n, "pct": _pct(n, total), "d2d": d2d, "d2d_cover": _pct(d2d_n, n),
                      "target": D2D_TARGET_LABEL[lv], "status": status,
                      "ar_pct": _pct(num(r.get("ar")), n)})
    missing = num(fetch_one(f"select sum(e.er_emergency_level_id is null) c "
                            f"from er_regist e {_where(d1, d2, shift)}").get("c"))
    return {"total": total, "items": items, "missing": missing, "missing_pct": _pct(missing, total)}


# ------------------------------------------------------ 4. Disposition

#: er_dch_type ของ HOSxP — เสียชีวิต (4, 6) รวมใน "อื่น ๆ" ตามที่ตกลงไว้ว่าไม่แสดงบนแดชบอร์ด
DISPOSITION = [
    ("home", "กลับบ้าน", (1,), "#22a06b"),
    ("admit", "Admit", (3,), "#ef4444"),
    ("refer", "ส่งต่อ (Refer)", (2,), "#3b82f6"),
    ("obs", "สังเกตอาการ", (5,), "#f59e0b"),
    ("ama", "ปฏิเสธการรักษา / หนีกลับ", (7, 8), "#8b5cf6"),
]


def disposition(d1, d2, shift):
    rows = fetch(f"select e.er_dch_type t, count(*) n from er_regist e {_where(d1, d2, shift)} group by t")
    by = {int(num(r["t"], -1)): num(r["n"]) for r in rows}
    total = sum(by.values())
    items = []
    for key, label, codes, color in DISPOSITION:
        v = sum(by.get(c, 0) for c in codes)
        items.append({"key": key, "label": label, "value": v, "pct": _pct(v, total), "color": color})
    other = total - sum(i["value"] for i in items)
    if other > 0:
        items.append({"key": "other", "label": "อื่น ๆ / ยังไม่ระบุ", "value": other,
                      "pct": _pct(other, total), "color": "#94a3b8"})
    return {"total": total, "items": items}


# ------------------------------------------------------ 5. ประเภทผู้ป่วย

def case_type(d1, d2, shift):
    r = fetch_one(f"""
        select count(*) n,
          sum({SVC}) svc,
          sum(not {SVC} and exists(select 1 from ovstdiag x where x.vn = e.vn and x.icd10 like 'V%')) traf,
          sum(not {SVC} and not exists(select 1 from ovstdiag x where x.vn = e.vn and x.icd10 like 'V%')
              and exists(select 1 from ovstdiag x where x.vn = e.vn and x.icd10 regexp '^[ST]')) tr
        from er_regist e {_where(d1, d2, shift)}""")
    n = num(r.get("n"))
    svc, traf, tr = num(r.get("svc")), num(r.get("traf")), num(r.get("tr"))
    items = [("Non-trauma", n - svc - traf - tr, "#ef4444"),
             ("Trauma ทั่วไป", tr, "#f59e0b"),
             ("อุบัติเหตุจราจร", traf, "#7c2d12"),
             ("รับบริการ", svc, "#3b82f6")]
    return {"total": n, "items": [{"label": l, "value": v, "pct": _pct(v, n), "color": c}
                                  for l, v, c in items]}


# ---------------------------------------------------- 6. มาถึงรายชั่วโมง

def arrivals_by_hour(d1, d2, shift):
    rows = fetch(f"select hour(e.enter_er_time) h, count(*) n from er_regist e "
                 f"{_where(d1, d2, shift)} group by h")
    by = {int(num(r["h"])): num(r["n"]) for r in rows}
    days = _days(d1, d2)
    hours = []
    for h in range(24):
        sh = "D" if h < 8 else "C" if h < 16 else "B"
        v = by.get(h, 0)
        hours.append({"h": h, "value": v, "avg": round(v / days, 1), "shift": sh})
    peak = max(hours, key=lambda x: x["value"])
    return {"hours": hours, "peak": peak, "max": peak["value"] or 1}


# ------------------------------------------------------- 7. ภาระงานรายเวร

def shift_load(d1, d2):
    """ทุกเวรเสมอ (ไม่ขึ้นกับตัวกรองเวร) · admit/refer นับตามเวลาเข้า ER"""
    rows = fetch(f"""
        select {SHIFT_CASE} sh, count(*) n, sum({LEVEL} <= 2) l12, sum({TRAUMA}) tr,
               sum(e.er_dch_type = 3) admit, sum(e.er_dch_type = 2) refer,
               sum(o.ovstist in ('04', '06')) ems
        {FROM} {_where(d1, d2, 'all')} group by sh""")
    by = {r["sh"]: r for r in rows}
    days = _days(d1, d2)
    out = []
    for sh in ("D", "C", "B"):
        r = by.get(sh, {})
        n = num(r.get("n"))
        out.append({"shift": sh, "name": SHIFTS[sh], "time": SHIFT_TIME[sh], "value": n,
                    "per_shift": round(n / days, 1), "l12": num(r.get("l12")),
                    "trauma": num(r.get("tr")), "admit": num(r.get("admit")),
                    "refer": num(r.get("refer")), "ems": num(r.get("ems"))})
    total = sum(x["value"] for x in out)
    for x in out:
        x["pct"] = _pct(x["value"], total)
    return out


# ---------------------------------------------- 8. เวลา ER → Admit / Refer

def time_to_disposition(d1, d2, shift):
    w = _where(d1, d2, shift)
    a = fetch_one(f"""
        select count(*) n, avg(m) m, sum(m > {BOARDING_MIN}) over_n from (
          select timestampdiff(minute, e.enter_er_time, timestamp(i.regdate, i.regtime)) m
          from er_regist e join ipt i on i.vn = e.vn {w} and e.er_dch_type = 3 and i.regtime is not null
        ) x where m between 0 and 1440""")
    r = fetch_one(f"""
        select count(*) n, avg(m) m, sum(m > {BOARDING_MIN}) over_n from (
          select timestampdiff(minute, e.enter_er_time,
                 (select min(timestamp(r.refer_date, r.refer_time)) from referout r where r.vn = e.vn)) m
          from er_regist e {w} and e.er_dch_type = 2
        ) x where m between 0 and 1440""")

    def pack(x):
        n = num(x.get("n"))
        return {"n": n, "avg": round(num(x.get("m"))), "over": num(x.get("over_n")),
                "over_pct": _pct(num(x.get("over_n")), n)}
    return {"admit": pack(a), "refer": pack(r), "cutoff": BOARDING_MIN}


# ------------------------------------------------- 9. โปรไฟล์ผู้ป่วย ER

ARRIVAL = [("ลงทะเบียนเอง / Kiosk", ("01", "05"), "#ef4444"),
           ("EMS (ALS / FR)", ("04", "06"), "#f59e0b"),
           ("มาตามนัด", ("02",), "#3b82f6"),
           ("รับส่งต่อ (Refer in)", ("03",), "#8b5cf6")]


def patient_profile(d1, d2, shift):
    w = _where(d1, d2, shift)
    rows = fetch(f"select o.ovstist t, count(*) n {FROM} {w} group by t")
    by = {r["t"]: num(r["n"]) for r in rows}
    total = sum(by.values())
    arrival = []
    for label, codes, color in ARRIVAL:
        v = sum(by.get(c, 0) for c in codes)
        arrival.append({"label": label, "value": v, "pct": _pct(v, total), "color": color})
    other = total - sum(i["value"] for i in arrival)
    if other > 0:
        arrival.append({"label": "อื่น ๆ", "value": other, "pct": _pct(other, total), "color": "#94a3b8"})

    ages = fetch_one(f"""
        select count(*) total,
          sum(timestampdiff(year, pt.birthday, e.vstdate) <= 14) a1,
          sum(timestampdiff(year, pt.birthday, e.vstdate) between 15 and 40) a2,
          sum(timestampdiff(year, pt.birthday, e.vstdate) between 41 and 60) a3,
          sum(timestampdiff(year, pt.birthday, e.vstdate) between 61 and 80) a4,
          sum(timestampdiff(year, pt.birthday, e.vstdate) > 80) a5
        {FROM} join patient pt on pt.hn = o.hn {w} and pt.birthday is not null""")
    a_total = num(ages.get("total"))
    age_items = [{"label": l, "value": num(ages.get(k)), "color": c} for l, k, c in (
        ("0-14 ปี", "a1", "#22a06b"), ("15-40 ปี", "a2", "#3b82f6"), ("41-60 ปี", "a3", "#8b5cf6"),
        ("61-80 ปี", "a4", "#f59e0b"), ("> 80 ปี", "a5", "#ef4444"))]
    for it in age_items:
        it["pct"] = _pct(it["value"], a_total)

    dx = fetch(f"""
        select d.icd10, ifnull(nullif(c.tname, ''), ifnull(c.name, d.icd10)) name, count(*) cnt
        from er_regist e join ovst o on o.vn = e.vn
             join ovstdiag d on d.vn = e.vn and d.diagtype = '1'
             left join icd101 c on c.code = d.icd10
             left join er_emergency_type et on et.er_emergency_type = e.er_emergency_type
        {w} and not {SVC}
        group by d.icd10, name order by cnt desc limit 5""")
    dx_items = [{"label": r["name"], "code": r["icd10"], "value": num(r["cnt"])} for r in dx]
    top = max((i["value"] for i in dx_items), default=1) or 1
    for it in dx_items:
        it["bar"] = _pct(it["value"], top)
    return {"total": total, "arrival": arrival, "ages": age_items, "dx": dx_items}


# ------------------------------------------------------ 11. คุณภาพบริการ ER

def quality(d1, d2, shift):
    r = fetch_one(f"""
        select count(*) n,
          sum(o.ovstist = '04') ems_als, sum(o.ovstist = '06') ems_fr, sum(o.ovstist = '03') refer_in,
          sum(e.er_dch_type in (7, 8)) ama,
          sum(not exists(select 1 from ovstdiag d where d.vn = e.vn and d.diagtype = '1')) no_pdx,
          sum(e.er_dch_type is null) no_dch,
          sum({LEVEL} = 1 and e.er_dch_type in (2, 3)) l1_ar, sum({LEVEL} = 1) l1
        {FROM} {_where(d1, d2, shift)}""")
    n = num(r.get("n"))
    v = {k: num(x) for k, x in r.items()}
    return {
        "total": n,
        "ems": v["ems_als"] + v["ems_fr"], "ems_als": v["ems_als"], "ems_fr": v["ems_fr"],
        "ems_pct": _pct(v["ems_als"] + v["ems_fr"], n),
        "refer_in": v["refer_in"], "refer_in_pct": _pct(v["refer_in"], n),
        "ama": v["ama"], "ama_pct": _pct(v["ama"], n),
        "no_pdx": v["no_pdx"], "no_pdx_pct": _pct(v["no_pdx"], n),
        "no_dch": v["no_dch"], "no_dch_pct": _pct(v["no_dch"], n),
    }


# --------------------------------------------------- 12. ส่งต่อไปที่ไหน

def refer_destinations(d1, d2, shift, limit=5):
    rows = fetch(f"""
        select r.refer_hospcode hc, h.name, count(distinct e.vn) n
        from er_regist e join referout r on r.vn = e.vn
             left join hospcode h on h.hospcode = r.refer_hospcode
        {_where(d1, d2, shift)} and e.er_dch_type = 2
        group by r.refer_hospcode, h.name order by n desc""")
    items = [{"label": x["name"] or x["hc"] or "ไม่ระบุ", "code": x["hc"] or "-", "value": num(x["n"])}
             for x in rows]
    total = sum(i["value"] for i in items)
    shown = items[:limit]
    if len(items) > limit:
        shown.append({"label": "อื่น ๆ", "code": "-", "value": sum(i["value"] for i in items[limit:])})
    top = max((i["value"] for i in shown), default=1) or 1
    for it in shown:
        it["bar"] = _pct(it["value"], top)
        it["pct"] = _pct(it["value"], total)
    return {"items": shown, "total": total}


# ------------------------------- 13-14. 10 อันดับโรค Refer / Admit · 15. สาเหตุการบาดเจ็บ

def _rank(rows, total, limit):
    items = [{"label": x["name"] or x["code"], "code": x["code"], "value": num(x["n"])} for x in rows]
    top = max((i["value"] for i in items), default=1) or 1
    for i, it in enumerate(items[:limit], 1):
        it["rank"] = i
        it["bar"] = _pct(it["value"], top)
        it["pct"] = _pct(it["value"], total)
    return {"items": items[:limit], "total": total}


def top_dx_by_dch(d1, d2, shift, dch_type, limit=10):
    """โรคหลัก (pdx ที่ ER ลง) ของผู้ป่วยที่จำหน่ายจาก ER ด้วย er_dch_type นั้น"""
    w = _where(d1, d2, shift)
    rows = fetch(f"""
        select d.icd10 code, ifnull(nullif(c.tname, ''), c.name) name, count(distinct e.vn) n
        from er_regist e join ovstdiag d on d.vn = e.vn and d.diagtype = '1'
             left join icd101 c on c.code = d.icd10
        {w} and e.er_dch_type = {int(dch_type)}
        group by d.icd10, name order by n desc limit {int(limit)}""")
    total = num(fetch_one(f"select count(*) c from er_regist e {w} and e.er_dch_type = {int(dch_type)}").get("c"))
    return _rank(rows, total, limit)


def injury_causes(d1, d2, shift, limit=10):
    """สาเหตุภายนอก (ICD-10 V01-Y98 ระดับ 3 หลัก) ของผู้บาดเจ็บ
    ไม่นับมารับบริการ/ติดตาม (er_pt_type 4 หรือโรคหลักเป็นรหัส Z เช่น มาฉีดวัคซีนพิษสุนัขบ้าเข็มถัดไป)"""
    base = (f"{_where(d1, d2, shift)} and not {SVC} and not exists(select 1 from ovstdiag p "
            "where p.vn = e.vn and p.diagtype = '1' and p.icd10 like 'Z%')")
    rows = fetch(f"""
        select left(d.icd10, 3) code, ifnull(nullif(c.tname, ''), c.name) name, count(distinct e.vn) n
        from er_regist e join ovstdiag d on d.vn = e.vn and d.icd10 regexp '^[V-Y]'
             left join icd101 c on c.code = left(d.icd10, 3)
        {base}
        group by code, name order by n desc limit {int(limit)}""")
    total = num(fetch_one(f"""
        select count(*) c from er_regist e {base}
          and exists(select 1 from ovstdiag d where d.vn = e.vn and d.icd10 regexp '^[V-Y]')""").get("c"))
    return _rank(rows, total, limit)


# ------------------------------------------------- 16. ส่งมาจาก OPD / แผนกอื่น

#: รหัสแผนก ER ของ HOSxP · 999 = สถานะ "กลับบ้าน" ไม่ใช่แผนก
_DEP_RE = re.compile(r"[0-9A-Za-z]{1,5}")
ER_DEPCODE = os.getenv("ER_DEPCODE", "011")
ER_DEPCODE = ER_DEPCODE if _DEP_RE.fullmatch(ER_DEPCODE) else "011"
NOT_DEPT = tuple(c for c in (x.strip() for x in os.getenv("ER_NOT_DEPCODES", "999").split(","))
                 if _DEP_RE.fullmatch(c))


def from_opd(d1, d2, shift, limit=5):
    """visit ที่ลงทะเบียนครั้งแรกที่แผนกอื่น (ovst.main_dep) แล้วส่งมา ER"""
    excl = ", ".join(f"'{c}'" for c in (ER_DEPCODE,) + NOT_DEPT)
    rows = fetch(f"""
        select o.main_dep dep, k.department name, count(*) n, sum({LEVEL} <= 2) l12,
               sum(e.er_dch_type in (2, 3)) ar
        {FROM} left join kskdepartment k on k.depcode = o.main_dep
        {_where(d1, d2, shift)} and o.main_dep is not null and o.main_dep not in ({excl})
        group by o.main_dep, k.department order by n desc""")
    total = num(fetch_one(f"select count(*) c from er_regist e {_where(d1, d2, shift)}").get("c"))
    n = sum(num(r["n"]) for r in rows)
    items = [{"label": r["name"] or r["dep"], "code": r["dep"], "value": num(r["n"])} for r in rows[:limit]]
    if len(rows) > limit:
        items.append({"label": "แผนกอื่น ๆ", "code": "-", "value": sum(num(r["n"]) for r in rows[limit:])})
    top = max((i["value"] for i in items), default=1) or 1
    for it in items:
        it["bar"] = _pct(it["value"], top)
    l12 = sum(num(r["l12"]) for r in rows)
    ar = sum(num(r["ar"]) for r in rows)
    return {"n": n, "pct": _pct(n, total), "l12": l12, "l12_pct": _pct(l12, n),
            "ar": ar, "ar_pct": _pct(ar, n), "items": items}


# ------------------------------------------- 17. เคสสีแดงอยู่ ER เกิน 60 นาที

RED_MAX_MIN = int(os.getenv("ER_RED_MAX_MIN", "60"))


def red_over(d1, d2, shift):
    """ระดับ 1 (สีแดง) ที่ Admit/Refer — นาทีจากเข้า ER ถึงลงทะเบียน admit / เวลาส่งต่อ
    ใช้เวลา admit/refer ซึ่งบันทึกจริง (ไม่ใช่เวลากดเริ่มตรวจ)"""
    r = fetch_one(f"""
        select count(*) n, sum(m > {RED_MAX_MIN}) over_n, avg(m) avg_m, max(m) max_m,
               sum(dch = 3 and m > {RED_MAX_MIN}) over_admit, sum(dch = 2 and m > {RED_MAX_MIN}) over_refer
        from (select e.er_dch_type dch,
                case when e.er_dch_type = 3 then timestampdiff(minute, e.enter_er_time,
                       (select min(timestamp(i.regdate, i.regtime)) from ipt i where i.vn = e.vn))
                     else timestampdiff(minute, e.enter_er_time,
                       (select min(timestamp(x.refer_date, x.refer_time)) from referout x where x.vn = e.vn)) end m
              {FROM} {_where(d1, d2, shift)} and {LEVEL} = 1 and e.er_dch_type in (2, 3)) t
        where m between 0 and 1440""")
    n, over = num(r.get("n")), num(r.get("over_n"))
    return {"n": n, "over": over, "pct": _pct(over, n), "avg": round(num(r.get("avg_m"))),
            "max": num(r.get("max_m")), "over_admit": num(r.get("over_admit")),
            "over_refer": num(r.get("over_refer")), "cutoff": RED_MAX_MIN}


# ------------------------------------------------------ 18. อุบัติเหตุหมู่ (MCI)

MCI_MIN = int(os.getenv("ER_MCI_MIN", "5"))
MCI_WINDOW = int(os.getenv("ER_MCI_WINDOW", "60"))
#: กลุ่มสาเหตุภายนอก — ผู้บาดเจ็บกลุ่มเดียวกันมาพร้อมกันจึงนับเป็นเหตุเดียว ("อื่น ๆ" ปนหลายสาเหตุ ไม่นับ)
MCI_GROUPS = {"traf": "อุบัติเหตุจราจร", "animal": "สัตว์/แมลงกัดต่อย", "assault": "ถูกทำร้ายร่างกาย",
              "poison": "สารพิษ", "burn": "ไฟไหม้/ความร้อน", "fall": "พลัดตกหกล้ม"}
_CAUSE_GROUP = """case when c like 'V%' then 'traf' when c between 'W00' and 'W19' then 'fall'
       when c between 'W50' and 'W64' or c between 'X20' and 'X29' then 'animal'
       when c between 'X85' and 'Y09' then 'assault'
       when c between 'X40' and 'X49' or c between 'X60' and 'X69' then 'poison'
       when c between 'X00' and 'X19' then 'burn' else 'other' end"""


def _dt(v):
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def mass_casualty(d1, d2, shift):
    """ผู้บาดเจ็บสาเหตุกลุ่มเดียวกัน ≥ MCI_MIN ราย เข้า ER ภายใน MCI_WINDOW นาที (หน้าต่างเลื่อน)"""
    rows = fetch(f"""
        select t, lvl, dch, c, {_CAUSE_GROUP} g from (
          select e.enter_er_time t, {LEVEL} lvl, e.er_dch_type dch,
            (select min(left(d.icd10, 3)) from ovstdiag d where d.vn = e.vn and d.icd10 regexp '^[V-Y]') c
          {FROM} {_where(d1, d2, shift)} and not {SVC}) x
        where c is not null order by t""")
    by = {}
    for r in rows:
        if r["g"] in MCI_GROUPS:
            by.setdefault(r["g"], []).append((_dt(r["t"]), int(num(r["lvl"], 5)), int(num(r["dch"], 0)), r["c"]))
    events = []
    for g, v in by.items():
        i = 0
        while i < len(v):
            j = i
            while j + 1 < len(v) and (v[j + 1][0] - v[i][0]).total_seconds() <= MCI_WINDOW * 60:
                j += 1
            if j - i + 1 >= MCI_MIN:
                grp = v[i:j + 1]
                codes = {}
                for x in grp:
                    codes[x[3]] = codes.get(x[3], 0) + 1
                icd = " · ".join(f"{c}×{k}" if k > 1 else c
                                 for c, k in sorted(codes.items(), key=lambda kv: (-kv[1], kv[0])))
                events.append({"start": grp[0][0], "end": grp[-1][0], "n": len(grp), "group": MCI_GROUPS[g],
                               "icd": icd,
                               "l12": sum(1 for x in grp if x[1] <= 2),
                               "admit": sum(1 for x in grp if x[2] == 3),
                               "refer": sum(1 for x in grp if x[2] == 2)})
                i = j + 1
            else:
                i += 1
    events.sort(key=lambda x: x["start"], reverse=True)
    for ev in events:
        ev["when"] = f"{_thai_date(ev['start'].date())} {ev['start']:%H:%M}–{ev['end']:%H:%M}"
        del ev["start"], ev["end"]
    return {"count": len(events), "patients": sum(x["n"] for x in events), "events": events[:8],
            "more": max(len(events) - 8, 0), "min": MCI_MIN, "window": MCI_WINDOW}


# ------------------------------------------------------ 10. Alert & Action

def alerts(kpi, tri, ttd, q, load, red=None, mci=None):
    out = []
    if red and red["over"] > 0:
        out.append({"level": "high", "issue": f"สีแดงอยู่ ER เกิน {red['cutoff']} นาที {red['over']} ราย "
                                              f"({red['pct']}% ของระดับ 1 ที่ Admit/Refer)",
                    "where": "ER", "status": "เร่งดำเนินการ",
                    "action": "ทบทวนเคส / เร่งประสาน Admit-Refer ผู้ป่วยวิกฤต"})
    if mci and mci["count"] > 0:
        out.append({"level": "mid", "issue": f"อุบัติเหตุหมู่ {mci['count']} ครั้ง ({mci['patients']} ราย)",
                    "where": "ER", "status": "ทบทวน", "action": "ทบทวนการรับมือ (After Action Review)"})
    # เวลาพบแพทย์มาจากเวลาที่บันทึกในระบบ ไม่ใช่เวลาตรวจจริง — แสดงเป็นข้อสังเกตด้านการบันทึกเท่านั้น
    late = [it for it in tri["items"][:2] if it["status"] in ("watch", "bad") and it["d2d"] is not None]
    if late:
        txt = " · ".join(f"ระดับ {it['level']} {it['d2d']} นาที" for it in late)
        out.append({"level": "info", "issue": f"เวลาพบแพทย์ตามที่บันทึก {txt}",
                    "where": "บันทึก ER", "status": "ข้อสังเกต",
                    "action": "กดเริ่มตรวจใน HOSxP ทันทีที่แพทย์พบผู้ป่วย ให้เวลาตรงกับจริง"})
    if kpi["revisit_rate"] > 5:
        out.append({"level": "high" if kpi["revisit_rate"] > 10 else "mid",
                    "issue": f"กลับมา ER ซ้ำใน 48 ชม. {kpi['revisit']} ราย ({kpi['revisit_rate']}%)",
                    "where": "ER", "status": "ติดตาม", "action": "ทบทวนเคสกลับมาซ้ำ (Unplanned return)"})
    if ttd["admit"]["over"] > 0:
        out.append({"level": "mid", "issue": f"รอ Admit เกิน {ttd['cutoff']} นาที {ttd['admit']['over']} ราย",
                    "where": "ER → IPD", "status": "เฝ้าระวัง", "action": "ประสานเตียง / เร่งรับเข้าหอผู้ป่วย"})
    if ttd["refer"]["over"] > 0:
        out.append({"level": "mid", "issue": f"รอ Refer เกิน {ttd['cutoff']} นาที {ttd['refer']['over']} ราย",
                    "where": "ER → ศูนย์ส่งต่อ", "status": "เฝ้าระวัง", "action": "ทบทวนขั้นตอนประสานส่งต่อ"})
    if q["ama"] > 0:
        out.append({"level": "mid", "issue": f"ปฏิเสธการรักษา / หนีกลับ {q['ama']} ราย",
                    "where": "ER", "status": "ติดตาม", "action": "ติดตามผู้ป่วย / บันทึกเหตุผล"})
    if tri["missing_pct"] > 5:
        out.append({"level": "mid", "issue": f"ยังไม่ลงระดับ Triage {tri['missing']} ราย ({tri['missing_pct']}%)",
                    "where": "บันทึก ER", "status": "เฝ้าระวัง", "action": "ลงระดับความเร่งด่วนทุกราย"})
    if kpi["d2d_cover"] < 80:
        out.append({"level": "info", "issue": f"มีเวลาพบแพทย์ {kpi['d2d_cover']}% ของราย",
                    "where": "บันทึก ER", "status": "ข้อสังเกต", "action": "กดเริ่มตรวจใน HOSxP ทุกราย"})
    busiest = max(load, key=lambda x: x["per_shift"]) if load else None
    if busiest and busiest["per_shift"] >= 25:
        out.append({"level": "mid", "issue": f"{busiest['name']} เฉลี่ย {busiest['per_shift']} ราย/เวร",
                    "where": "อัตรากำลัง", "status": "ทบทวน", "action": "พิจารณาเสริมกำลังช่วงพีค"})
    if not out:
        out.append({"level": "ok", "issue": "ไม่พบประเด็นเกินเกณฑ์เฝ้าระวัง",
                    "where": "-", "status": "ปกติ", "action": "ติดตามตามรอบปกติ"})
    # ข้อสังเกตด้านการบันทึกไว้ท้ายตาราง ให้ประเด็นที่ต้องทำก่อนอยู่บนสุด
    out.sort(key=lambda a: {"high": 0, "mid": 1, "info": 2}.get(a["level"], 3))
    return out[:7]


# ------------------------------------------------------------- ประกอบร่าง

CACHE_TTL = int(os.getenv("ER_CACHE_TTL", "120"))
_cache = {}

#: จำนวนช่วงวันที่ที่คำนวณใหม่พร้อมกันได้ต่อโปรเซส — กันคนยิงช่วงวันที่ไม่ซ้ำรัว ๆ จน HOSxP ช้าทั้งโรงพยาบาล
MAX_BUILDS = max(1, int(os.getenv("ER_MAX_BUILDS", "2")))
BUILD_WAIT = int(os.getenv("ER_BUILD_WAIT", "60"))
_build_slots = threading.BoundedSemaphore(MAX_BUILDS)


class Busy(Exception):
    """มีงานคำนวณค้างอยู่เต็มแล้ว — ให้ผู้ใช้ลองใหม่"""


def _cached(key):
    hit = _cache.get(key) if CACHE_TTL else None
    if hit and _time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    return None


def build(d1=None, d2=None, shift="all"):
    today = date.today()
    d2 = d2 or today.isoformat()
    d1 = d1 or today.replace(day=1).isoformat()
    key = (d1, d2, shift)
    hit = _cached(key)
    if hit is not None:
        return hit
    if not _build_slots.acquire(timeout=BUILD_WAIT):
        raise Busy()
    try:
        hit = _cached(key)          # ระหว่างรอ อาจมีคนคำนวณช่วงเดียวกันเสร็จแล้ว
        return hit if hit is not None else _build(d1, d2, shift, key)
    finally:
        _build_slots.release()


def _build(d1, d2, shift, key):

    # ยิงทุกบล็อกพร้อมกัน — ต่างคนต่างเปิด connection ของตัวเอง
    jobs = {
        "kpi": (kpi_summary, d1, d2, shift),
        "evening": (evening_low, d1, d2),
        "trend": (visit_trend, d2, shift),
        "triage": (triage, d1, d2, shift),
        "dispo": (disposition, d1, d2, shift),
        "case": (case_type, d1, d2, shift),
        "hourly": (arrivals_by_hour, d1, d2, shift),
        "load": (shift_load, d1, d2),
        "ttd": (time_to_disposition, d1, d2, shift),
        "profile": (patient_profile, d1, d2, shift),
        "quality": (quality, d1, d2, shift),
        "refer": (refer_destinations, d1, d2, shift),
        "refer_dx": (top_dx_by_dch, d1, d2, shift, 2),
        "admit_dx": (top_dx_by_dch, d1, d2, shift, 3),
        "injury": (injury_causes, d1, d2, shift),
        "opd": (from_opd, d1, d2, shift),
        "red": (red_over, d1, d2, shift),
        "mci": (mass_casualty, d1, d2, shift),
    }
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {k: ex.submit(f, *a) for k, (f, *a) in jobs.items()}
        data = {k: f.result() for k, f in futs.items()}

    data.update({
        "generated_at": _thai_datetime(),
        "period_label": _thai_range(d1, d2),
        "d1": d1, "d2": d2, "shift": shift, "shift_name": SHIFTS[shift], "shifts": SHIFTS,
        "alerts": alerts(data["kpi"], data["triage"], data["ttd"], data["quality"], data["load"],
                         data["red"], data["mci"]),
    })
    if CACHE_TTL:
        _cache[key] = (_time.time(), data)
        while len(_cache) > 32:
            _cache.pop(next(iter(_cache)))
    return data


TH_MONTH = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.",
            "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]


def _thai_date(d):
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return f"{d.day} {TH_MONTH[d.month - 1]} {d.year + 543}"


def _thai_range(d1, d2):
    a, b = date.fromisoformat(d1), date.fromisoformat(d2)
    if a.month == b.month and a.year == b.year:
        return f"วันที่ {a.day} - {b.day} {TH_MONTH[b.month - 1]} {b.year + 543}"
    return f"{_thai_date(a)} - {_thai_date(b)}"


def _thai_datetime():
    n = datetime.now()
    return f"{_thai_date(n.date())} เวลา {n:%H:%M} น."
