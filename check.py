"""ตรวจความพร้อมของข้อมูล HOSxP ก่อนใช้ ER Dashboard (หน้า /check และ setup_check.py)

รหัสใน HOSxP ของแต่ละโรงพยาบาลไม่เหมือนกัน ถ้าไม่ตรง ตัวเลขบนแดชบอร์ดจะเพี้ยนโดยไม่มีข้อความเตือน
หน้านี้เทียบรหัสที่แอปใช้ (queries.P — ตั้งได้ด้วย env ER_*) กับข้อมูลจริงของโรงพยาบาล
  - ประเภทการมา (ovstist) · การจำหน่าย (er_dch_type) · ประเภทผู้ป่วย (er_pt_type) · วินิจฉัยหลัก (diagtype)
    ชื่อของรหัสที่ใช้ % ของ visit ER และรหัสอื่นที่ชื่อตรงกลุ่ม (เสนอค่า env ให้)
  - แผนก ER (ER_DEPCODE) มีจริงและชื่อเป็นห้องฉุกเฉิน · ระดับความเร่งด่วน 1–5
  - ความครบของข้อมูลใน er_regist และแหล่งเวลาพบแพทย์ (เสนอคอลัมน์ service_time ถ้าคอลัมน์ที่ใช้ไม่มีข้อมูล)
  - Fast track: ICD-10 ของแต่ละกลุ่ม · หัตถการ ER ที่ผูก ICD-9 ของ ECG / CPR / Defib · ยาละลายลิ่มเลือด · GCS
แสดงเฉพาะจำนวนนับและชื่อรหัส ไม่มีข้อมูลรายคน
"""

import os
import re

import nurse_report
import queries
from db import fetch, fetch_one, num

#: รหัสที่ไม่เข้ากลุ่มแต่ชื่อตรงกลุ่ม / แผนกที่ชื่อเป็น ER ≥ เท่านี้ (% ของ visit ER) → เตือน
WARN_PCT = 1.0
#: ข้อมูลว่างเกินเท่านี้ (%) → เตือน
MISSING_WARN_PCT = 5.0
#: visit ที่มีวินิจฉัย แต่มีวินิจฉัยหลักน้อยกว่านี้ (%) → เตือน
PDX_WARN_PCT = 80.0
#: admit / refer ที่มีเวลา admit / เวลาส่งต่อ น้อยกว่านี้ (%) → เตือน
TIME_WARN_PCT = 80.0
#: คอลัมน์ "เริ่มตรวจ" มีข้อมูลน้อยกว่านี้ (% ของ visit ER) → เสนอคอลัมน์อื่น
ST_LOW_PCT = 10.0
ST_COLS = [f"service{i}" for i in range(1, 21)]

ER_NAME = re.compile(r"\ber\b|ฉุกเฉิน|อุบัติเหตุ|emergency", re.I)

# ---- กลุ่มของรหัส: (key, ชื่อกลุ่ม, env, รหัสที่แอปใช้, คำในชื่อที่บอกว่าอยู่กลุ่มนี้, มีผลกับ)
# เรียงตามลำดับที่ใช้เดากลุ่มจากชื่อ (กลุ่มแรกที่ตรงชนะ) · env None = รู้จักแต่ไม่ได้ใช้ (ไม่เตือน)
OVSTIST_GROUPS = [
    ("walkin", "ลงทะเบียนเอง / Kiosk", "ER_WALKIN_CODES", queries.WALKIN_CODES,
     r"มาเอง|walk|kiosk|ลงทะเบียนเอง", "บล็อก 9 ประเภทการมา"),
    ("appt", "มาตามนัด", "ER_APPT_CODES", queries.APPT_CODES, r"นัด",
     "บล็อก 9 · อัตรากลับมา ER ซ้ำ 48 ชม. (ไม่นับมาตามนัด)"),
    ("refer_in", "รับส่งต่อ (Refer in)", "ER_REFER_IN_CODES", queries.REFER_IN_CODES,
     r"refer|ส่งต่อ|ส่งตัว", "บล็อก 9, 11"),
    ("ems_fr", "EMS FR / BLS", "ER_EMS_FR_CODES", queries.EMS_FR_CODES,
     r"\bfr\b|\bbls\b|เบื้องต้น|first\s*resp|กู้ภัย|อาสา", "บล็อก 7, 9, 11"),
    ("ems_als", "EMS ALS", "ER_EMS_ALS_CODES", queries.EMS_ALS_CODES,
     r"\bals\b|ขั้นสูง|\bems\b|1669|กู้ชีพ|การแพทย์ฉุกเฉิน", "บล็อก 7, 9, 11"),
]
DCH_GROUPS = [
    ("death", "เสียชีวิต (รวมใน “อื่น ๆ”)", None, (), r"เสียชีวิต|ตาย|dead|death|\bdoa\b|\bdbr\b", ""),
    ("escape", "หนีกลับ", "ER_DCH_ESCAPE", queries.DCH["escape"], r"หนี|escape", "บล็อก 4, 11"),
    ("refuse", "ปฏิเสธการรักษา", "ER_DCH_REFUSE", queries.DCH["refuse"],
     r"ปฏิเสธ|ไม่สมัครใจ|ไม่ยินยอม|against|\bama\b|refus", "บล็อก 4, 11"),
    ("refer", "ส่งต่อ (Refer)", "ER_DCH_REFER", queries.DCH["refer"], r"refer|ส่งต่อ|ส่งตัว",
     "บล็อก 1, 4, 7, 8, 12, 13, 16, 17"),
    ("admit", "Admit", "ER_DCH_ADMIT", queries.DCH["admit"], r"admit|รับไว้|รับเข้า|นอน|ผู้ป่วยใน|\bipd\b",
     "บล็อก 1, 2, 4, 7, 8, 14, 16, 17"),
    ("obs", "สังเกตอาการ", "ER_DCH_OBS", queries.DCH["obs"], r"สังเกต|observ|\bobs\b", "บล็อก 4"),
    ("home", "กลับบ้าน", "ER_DCH_HOME", queries.DCH["home"], r"กลับบ้าน|จำหน่าย|discharge|\bhome\b", "บล็อก 4"),
]
PT_GROUPS = [
    ("autopsy", "ชันสูตรศพ", "ER_AUTOPSY_PT_TYPES", queries.AUTOPSY_PT_TYPES, r"ชันสูตร|ศพ",
     "รายงานพยาบาล (นับเป็นงานบริการ)"),
    ("svc", "รับบริการ (ทำแผล ฉีดยา)", "ER_SVC_PT_TYPES", queries.SVC_PT_TYPES,
     r"บริการ|ทำแผล|ฉีดยา|service", "บล็อก 5, 9, 15, 18 · อัตรากลับมาซ้ำ · รายงานพยาบาล"),
]
DIAG_GROUPS = [
    ("pdx", "วินิจฉัยหลัก", "ER_PDX_DIAGTYPE", (queries.PDX_DIAGTYPE,), r"principal|หลัก|\bpdx\b",
     "บล็อก 9, 11, 13, 14, 15 · อัตรากลับมาซ้ำ · รายงานพยาบาล"),
]
PROVIDER_GROUPS = [
    ("nurse", "พยาบาล", "ER_NURSE_PROVIDER_TYPES", nurse_report.NURSE_TYPES, r"พยาบาล|nurse",
     "รายงานพยาบาลตรวจแทนแพทย์"),
    ("doctor", "แพทย์", "ER_DOCTOR_PROVIDER_TYPES", nurse_report.DOCTOR_TYPES,
     r"^(?!.*(แผนไทย|ทันต)).*แพทย์|doctor|physician", "รายงานพยาบาลตรวจแทนแพทย์"),
]


def _safe(fn, *args):
    """ตารางบางตัวไม่มีในบางรุ่นของ HOSxP / ไม่ได้รับสิทธิ์ → ข้ามข้อนั้น ไม่ให้ทั้งหน้าล้ม"""
    try:
        return fn(*args)
    except Exception as e:
        return {"error": type(e).__name__}


def _status(cond_bad, cond_warn):
    return "bad" if cond_bad else "warn" if cond_warn else "ok"


def _lookup(table, key):
    """{รหัส: ชื่อ} จากตารางอ้างอิงของ HOSxP — คอลัมน์ชื่อ = name หรือคอลัมน์แรกที่มีคำว่า name
    (ชื่อคอลัมน์ต่างกันในแต่ละรุ่น) · ไม่มีตาราง = {}"""
    try:
        rows = fetch(f"select * from {table} limit 500")
    except Exception:
        return {}
    out = {}
    for r in rows:
        cols = list(r)
        k = key if key in r else cols[0]
        nc = "name" if "name" in r else next((c for c in cols if c != k and "name" in c.lower()), None)
        if r.get(k) is not None:
            out[str(r[k]).strip()] = str(r.get(nc) or "").strip() if nc else ""
    return out


def _guess(name, groups):
    """กลุ่มที่ชื่อรหัสบอก (กลุ่มแรกที่คำตรง) ไม่ตรงกลุ่มใด = None"""
    if not name:
        return None
    return next((g[0] for g in groups if re.search(g[4], name, re.I)), None)


def _fmt(codes, names, by_n, total):
    return ", ".join(f"{c} {names.get(c, '')}".strip() + f" ({queries._pct(by_n.get(c, 0), total)}%)"
                     for c in codes)


def _group_audit(rows, names, groups, total):
    """rows = [{code, n}] ของ visit ER · names = {รหัส: ชื่อ} จากตารางอ้างอิง

    คืน {"rows": รหัสทุกตัวที่พบพร้อมกลุ่ม, "summary": สรุปต่อกลุ่ม (ตาราง "แอปใช้ vs พบในข้อมูล")}
    เตือนเมื่อ: ไม่มี visit ด้วยรหัสที่ใช้ · ชื่อรหัสที่ใช้ตรงกลุ่มอื่น · มีรหัสอื่นที่ชื่อตรงกลุ่มนี้แต่แอปไม่นับ
    """
    by_n = {}
    for r in rows:
        c = str(r.get("code") if r.get("code") is not None else "").strip()
        by_n[c] = by_n.get(c, 0) + num(r.get("n"))
    label = {g[0]: g[1] for g in groups}
    owner = {}
    for g in groups:
        for c in g[3]:
            owner.setdefault(str(c), g[0])

    out_rows = []
    for c, n in sorted(by_n.items(), key=lambda kv: -kv[1]):
        nm = names.get(c, "")
        own, guess = owner.get(c), _guess(nm, groups)
        pct = queries._pct(n, total)
        note = None
        if own and guess and guess != own and not re.search(next(g[4] for g in groups if g[0] == own), nm, re.I):
            note = f"ชื่อดูเหมือน “{label[guess]}”"
        elif not own and guess and next(g[2] for g in groups if g[0] == guess) and pct >= WARN_PCT:
            note = f"ชื่อดูเหมือน “{label[guess]}” แต่แอปไม่นับเป็นกลุ่มนี้"
        elif names and c and c not in names and pct >= WARN_PCT:
            note = "ไม่มีรหัสนี้ในตารางอ้างอิง"
        out_rows.append({"code": c or "–", "name": nm or ("(ว่าง)" if not c else ""), "n": n, "pct": pct,
                         "group": label.get(own, ""), "note": note})

    summary = []
    for key, glabel, env, codes, kw, impact in groups:
        if not env:
            continue
        codes = [str(c) for c in codes]
        n = sum(by_n.get(c, 0) for c in codes)
        missing = [c for c in codes if names and c not in names]
        wrong = [c for c in codes if names.get(c) and not re.search(kw, names[c], re.I)
                 and _guess(names[c], groups) not in (None, key)]
        pool = set(by_n) | set(names)
        cand = sorted((c for c in pool if c and c not in codes and _guess(names.get(c, ""), groups) == key
                       and (by_n.get(c, 0) or not n)), key=lambda c: -by_n.get(c, 0))
        keep = [c for c in codes if c not in wrong and c not in missing]
        # ไม่มี visit แต่ชื่อในตารางตรงกลุ่ม = เหตุการณ์ที่ไม่บ่อย (เช่น หนีกลับ) ไม่ใช่รหัสผิด
        named = any(re.search(kw, names.get(c, ""), re.I) for c in codes)
        empty = not n and not named
        fix = None
        if cand:
            fix = f"{env}=" + ",".join(keep + cand)
        elif wrong or missing or empty:
            fix = f"ตรวจตารางอ้างอิงว่ารหัสไหนคือ “{glabel}” แล้วตั้ง {env}"
        found = (_fmt(codes, names, by_n, total) + " ของ visit ER" if n else
                 ", ".join(f"{c} {names.get(c, '')}".strip() for c in codes) + " · ไม่มี visit ER ในช่วงที่ตรวจ")
        if missing:
            found += " · ไม่มีในตารางอ้างอิง: " + ", ".join(missing)
        if wrong:
            found += " · ชื่อไม่ตรงกลุ่ม: " + ", ".join(f"{c} {names[c]}" for c in wrong)
        if cand:
            found += " · มีรหัสอื่นที่ชื่อตรงกลุ่มนี้: " + _fmt(cand, names, by_n, total)
        summary.append({
            "label": glabel, "env": env, "assume": ", ".join(codes), "found": found, "fix": fix,
            "impact": impact, "n": n, "pct": queries._pct(n, total),
            "status": _status(bool(names) and len(missing) + len(wrong) == len(codes), empty or wrong or missing or cand),
        })
    return {"rows": out_rows, "summary": summary, "names": bool(names)}


# ---------------------------------------------------------------- คำสั่ง SQL

def _args(d1, d2):
    return {**queries.P, "d1": d1, "d2": d2}


#: visit ER ที่แดชบอร์ดนับ (มีเวลาเข้า ER และคัดกรองแล้ว)
W_ER = "where e.vstdate between %(d1)s and %(d2)s and e.enter_er_time is not null"
W = f"{W_ER} and {queries.SCREENED}"


def _dist(expr, a, join=""):
    """จำนวน visit ER แยกตามค่าของ expr (นับ visit ไม่ใช่แถว — วินิจฉัยหลายรายการต่อ visit)"""
    return fetch(f"select {expr} code, count(distinct e.vn) n from er_regist e join ovst o on o.vn = e.vn {join} "
                 f"{W} group by {expr}", a)


def _base(a):
    r = fetch_one(f"""
        select count(*) n_all, sum(e.enter_er_time is not null) n_er,
          sum(e.enter_er_time is not null and {queries.SCREENED}) n,
          sum(e.enter_er_time is not null and e.er_emergency_level_id is null) no_level,
          sum(e.enter_er_time is not null and e.er_pt_type is null) no_pt,
          sum(e.enter_er_time is not null and {queries.SCREENED} and e.er_dch_type is null) no_dch,
          sum(e.enter_er_time is not null and {queries.SCREENED} and e.er_dch_type in %(dch_admit)s) admit,
          sum(e.enter_er_time is not null and {queries.SCREENED} and e.er_dch_type in %(dch_admit)s
              and exists(select 1 from ipt i where i.vn = e.vn and i.regtime is not null)) admit_t,
          sum(e.enter_er_time is not null and {queries.SCREENED} and e.er_dch_type in %(dch_refer)s) refer,
          sum(e.enter_er_time is not null and {queries.SCREENED} and e.er_dch_type in %(dch_refer)s
              and exists(select 1 from referout r where r.vn = e.vn and r.refer_time is not null)) refer_t
        from er_regist e where e.vstdate between %(d1)s and %(d2)s""", a) or {}
    return {k: num(r.get(k)) for k in ("n_all", "n_er", "n", "no_level", "no_pt", "no_dch",
                                         "admit", "admit_t", "refer", "refer_t")}


def _times(b):
    """ความครบของข้อมูลใน er_regist ที่แดชบอร์ดใช้"""
    pct = queries._pct
    rows = [
        ("เวลาเข้า ER (enter_er_time)", b["n_er"], b["n_all"], MISSING_WARN_PCT,
         "ไม่มี = ไม่นับในแดชบอร์ดเลย (ทุกบล็อกใช้เวลานี้)"),
        ("ระดับความเร่งด่วน (er_emergency_level_id)", b["n_er"] - b["no_level"], b["n_er"], MISSING_WARN_PCT,
         "ไม่มี = ถือว่า ER ไม่ได้คัดกรอง ไม่นับ (บล็อก 3 แสดงจำนวนที่ตัดออก)"),
        ("ประเภทผู้ป่วย (er_pt_type)", b["n_er"] - b["no_pt"], b["n_er"], MISSING_WARN_PCT,
         "ไม่มี = ถือว่า ER ไม่ได้คัดกรอง ไม่นับ"),
        ("การจำหน่ายจาก ER (er_dch_type)", b["n"] - b["no_dch"], b["n"], MISSING_WARN_PCT,
         "ไม่มี = “อื่น ๆ / ยังไม่ระบุ” ในบล็อก 4"),
        ("เวลา Admit (ipt.regdate + regtime)", b["admit_t"], b["admit"], 100 - TIME_WARN_PCT,
         "บล็อก 8 และ 17 (ไม่มีเวลา = ไม่นับในค่าเฉลี่ย)"),
        ("เวลาส่งต่อ (referout.refer_date + refer_time)", b["refer_t"], b["refer"], 100 - TIME_WARN_PCT,
         "บล็อก 8 และ 17"),
    ]
    out = []
    for label, have, base, warn, impact in rows:
        p = pct(have, base)
        out.append({"label": label, "have": have, "base": base, "pct": p, "impact": impact,
                    "status": "ok" if not base else _status(have == 0, 100 - p > warn)})
    return out


def _d2d(a):
    """แต่ละแหล่งของเวลาพบแพทย์มีข้อมูลกี่ % ของ visit ER (ภายใน D2D_MAX นาทีหลังเข้า ER)"""
    src = queries._SOURCES
    r = fetch_one(f"""
        select count(*) n, sum(({src[0]}) is not null) st, sum({src[1]} is not null) lab,
          sum({src[2]} is not null) xray, sum({src[3]} is not null) rx,
          sum({queries._HPI_LIKE}) hpi, sum(({queries.D2D}) is not null) any_src
        {queries.FROM_ST} left join opdscreen sc on sc.vn = e.vn {W}""", a) or {}
    n = num(r.get("n"))
    labels = [("st", f"กดเริ่มตรวจ (service_time.{queries.ST_DOCTOR})"), ("lab", "สั่ง Lab (lab_head)"),
              ("xray", "สั่ง X-ray (xray_report)"), ("rx", "สั่งยา (opitemrece)"),
              ("hpi", "บันทึกพยาบาลใน HPI ที่มีคำว่า แพทย์ / consult (ประมาณ)"),
              ("any_src", "รวมทุกแหล่ง (ไม่รวม HPI)")]
    return {"n": n, "rows": [{"key": k, "label": l, "n": num(r.get(k)), "pct": queries._pct(num(r.get(k)), n)}
                             for k, l in labels]}


def _st_cols(a):
    """คอลัมน์ service_time ทุกตัว: มีเวลาภายใน D2D_MAX นาทีหลังเข้า ER กี่ % · เฉลี่ยกี่นาที · กดจากแผนกไหน
    ทีละ 5 คอลัมน์ — คำสั่งยาวเกิน ~4 KB ผ่าน ssh บน Windows แล้วถูกตัด (โหมด ssh)"""
    out, n = [], 0
    for i in range(0, len(ST_COLS), 5):
        part = ST_COLS[i:i + 5]
        sel = []
        for c in part:
            raw = f"timestampdiff(minute, e.enter_er_time, timestamp(date(e.enter_er_time), s.{c}))"
            m = f"(case when s.{c} is null then null when {raw} < -60 then {raw} + 1440 else {raw} end)"
            sel.append(f"sum({m} between 0 and {queries.D2D_MAX}) n_{c}, "
                       f"avg(case when {m} between 0 and {queries.D2D_MAX} then {m} end) m_{c}")
        r = fetch_one(f"select count(*) n, {', '.join(sel)} {queries.FROM_ST} {W}", a) or {}
        n = num(r.get("n"))
        deps = {}
        union = " union all ".join(
            f"select '{c}' c, s.{c}_dep dep, count(*) k {queries.FROM_ST} {W} and s.{c} is not null "
            f"group by s.{c}_dep" for c in part)
        for x in fetch(union, a):
            deps.setdefault(x["c"], []).append((num(x["k"]), str(x["dep"] or "")))
        for c in part:
            k, m = num(r.get(f"n_{c}")), r.get(f"m_{c}")
            top = sorted(deps.get(c, []), reverse=True)[:2]
            out.append({"col": c, "n": k, "pct": queries._pct(k, n),
                        "after": round(num(m)) if m is not None and m != "" else None,
                        "deps": [{"code": d, "pct": queries._pct(x, sum(y for y, _ in deps[c]), 0)} for x, d in top],
                        "used": c == queries.ST_DOCTOR})
    return out


def _st_audit(cols, dep_names):
    """คอลัมน์ "เริ่มตรวจ" (ER_ST_DOCTOR_COL) — ไม่มีข้อมูล → เสนอคอลัมน์ที่ ER กดมากที่สุด"""
    for c in cols:
        for d in c["deps"]:
            d["name"] = dep_names.get(d["code"], d["code"] or "(ไม่ระบุ)")
    cur = next((c for c in cols if c["used"]), {"pct": 0.0, "after": None})
    er_cols = [c for c in cols if not c["used"] and c["deps"] and c["deps"][0]["code"] == queries.ER_DEPCODE
               and c["pct"] > cur["pct"]]
    best = max(er_cols, key=lambda c: c["pct"], default=None) if cur["pct"] < ST_LOW_PCT else None
    found = f"มีเวลาภายใน {queries.D2D_MAX} นาทีหลังเข้า ER {cur['pct']}% ของ visit ER"
    if cur.get("after") is not None:
        found += f" · เฉลี่ย {cur['after']} นาทีหลังเข้า ER"
    if best:
        found += f" · {best['col']} กดจากแผนก ER {best['pct']}%"
    return {"label": "คอลัมน์ “เริ่มตรวจ” ของแพทย์ (service_time)", "env": "ER_ST_DOCTOR_COL",
            "assume": queries.ST_DOCTOR, "found": found,
            "fix": (f"ER_ST_DOCTOR_COL={best['col']} (ตรวจว่าเป็นปุ่มเริ่มตรวจของแพทย์ ไม่ใช่คัดกรอง)" if best
                    else None if cur["pct"] >= ST_LOW_PCT else "ดูตารางคอลัมน์ service_time ด้านล่าง"),
            "impact": "เวลาพบแพทย์ (บล็อก 1, 3) — ยังมี Lab / X-ray / ยา / HPI เป็นแหล่งสำรอง",
            "status": _status(False, cur["pct"] < ST_LOW_PCT), "suggest": best["col"] if best else None}


def _levels(a, total):
    rows = fetch(f"select e.er_emergency_level_id code, count(*) n from er_regist e {W_ER} "
                 "group by e.er_emergency_level_id", a)
    names = _lookup("er_emergency_level", "er_emergency_level_id")
    by = {str(r["code"]).strip() if r["code"] is not None else "": num(r["n"]) for r in rows}
    out = [{"code": c or "–", "name": names.get(c, "") if c else "(ว่าง = ไม่ได้คัดกรอง ไม่นับ)", "n": n,
            "pct": queries._pct(n, total)} for c, n in sorted(by.items(), key=lambda kv: (kv[0] == "", kv[0]))]
    odd = [x for x in out if x["code"] != "–" and x["code"] not in {"1", "2", "3", "4", "5"} and x["n"]]
    missing = [str(i) for i in range(1, 6) if not by.get(str(i))]
    found = " · ".join(f"{x['code']} {x['name']}".strip() + f" {x['pct']}%" for x in out if x["code"] != "–")
    if odd:
        found += " · ระดับนอก 1–5: " + ", ".join(x["code"] for x in odd)
    return {"rows": out, "summary": {
        "label": "ระดับความเร่งด่วน (er_emergency_level_id)", "env": None, "assume": "1–5 (1 = Resuscitation … 5 = Non-urgent)",
        "found": found or "ไม่มีข้อมูล", "impact": "บล็อก 1, 3, 7, 16, 17 และรายงานพยาบาล",
        "fix": ("ระดับนอก 1–5 ไม่ถูกนับในบล็อก 3 — แจ้งผู้พัฒนาพร้อมผลหน้านี้" if odd else
                "ตรวจชื่อระดับในตาราง er_emergency_level ว่า 1 = วิกฤตที่สุด" if missing and total else None),
        "status": _status(bool(odd) and sum(x["pct"] for x in odd) >= WARN_PCT, bool(missing) and total > 0)}}


def _deps(a, total):
    """แผนก ER (ER_DEPCODE) + แผนกแรกที่ลงทะเบียน (ovst.main_dep) ของ visit ER"""
    names = {str(r["depcode"]).strip(): (r["department"] or "").strip()
             for r in fetch("select depcode, department from kskdepartment")}
    rows = _dist("o.main_dep", a)
    er, not_dep = queries.ER_DEPCODE, set(queries.NOT_DEPT)
    out = []
    for r in sorted(rows, key=lambda r: -num(r["n"])):
        c = str(r["code"] or "").strip()
        nm = names.get(c, "")
        pct = queries._pct(num(r["n"]), total)
        role = ("แผนก ER (ER_DEPCODE)" if c == er else "ไม่ใช่แผนก (ER_NOT_DEPCODES)" if c in not_dep
                else "ส่งมาจากแผนกอื่น → บล็อก 16" if c else "ไม่ระบุแผนก")
        warn = c != er and bool(ER_NAME.search(nm)) and pct >= WARN_PCT
        out.append({"code": c or "–", "name": nm, "n": num(r["n"]), "pct": pct, "role": role,
                    "note": "ชื่อเป็นห้องฉุกเฉิน แต่ถูกนับเป็น “ส่งมาจากแผนกอื่น”" if warn else None})
    er_n = sum(x["n"] for x in out if x["code"] == er)
    cand = [x for x in out if x["note"]] or [
        {"code": c, "name": n, "pct": 0.0} for c, n in names.items() if c != er and ER_NAME.search(n)][:3]
    name = names.get(er)
    st = _status(name is None or (not ER_NAME.search(name or "") and bool(cand)),
                 not ER_NAME.search(name or "") or any(x["note"] for x in out))
    found = (f"{er} = {name or '(ไม่มีชื่อ)'}" if name is not None else f"ไม่มีรหัส {er} ใน kskdepartment")
    found += f" · {queries._pct(er_n, total)}% ของ visit ER ลงทะเบียนครั้งแรกที่แผนกนี้"
    if cand:
        found += " · แผนกอื่นที่ชื่อเป็น ER: " + ", ".join(f"{x['code']} {x['name']}" for x in cand)
    summary = {"label": "แผนกห้องฉุกเฉิน", "env": "ER_DEPCODE", "assume": er, "found": found,
               "impact": "บล็อก 16 ส่งมาจาก OPD / แผนกอื่น",
               "fix": f"ER_DEPCODE={cand[0]['code']}" if st != "ok" and cand else None, "status": st}
    return {"rows": out, "summary": summary, "names": names}


def _providers(a, total):
    rows = _dist("ex.provider_type_code", a,
                 "join ovstdiag x on x.vn = e.vn and x.diagtype = %(pdx)s join doctor ex on ex.code = x.doctor")
    return _group_audit(rows, _lookup("provider_type", "provider_type_code"), PROVIDER_GROUPS, total)


#: fast track (บล็อก 19-22) — ICD-10: (key, ชื่อ, มีผลกับ) · หัตถการ ER: (key, ชื่อ, คำในชื่อหัตถการ, มีผลกับ)
FT_ICD_GROUPS = [("stroke", "Stroke (ICD-10)", "บล็อก 19"), ("stemi", "STEMI (ICD-10)", "บล็อก 20"),
                 ("hi", "บาดเจ็บศีรษะ (ICD-10)", "บล็อก 21"), ("tbi", "TBI (ICD-10)", "บล็อก 21")]
FT_OPER_GROUPS = [("ecg", "หัตถการ ECG", r"\becg\b|\bekg\b|e\.c\.g|คลื่นไฟฟ้าหัวใจ", "บล็อก 20 Door-to-ECG"),
                  ("cpr", "หัตถการ CPR", r"\bcpr\b|ฟื้นคืนชีพ", "บล็อก 22"),
                  ("defib", "หัตถการ Defibrillation", r"defib|ช็อกไฟฟ้า|ช๊อกไฟฟ้า", "บล็อก 22")]
#: ECG ที่เวลาเริ่มตรงกับเวลาเข้า ER พอดีเกินเท่านี้ (%) → สงสัยว่าเป็นค่าตั้งต้น
ECG_ZERO_WARN_PCT = 30.0
_ER_OPER = ("from er_regist e join ovst o on o.vn = e.vn join er_regist_oper x on x.vn = e.vn "
            "join er_oper_code c on c.er_oper_code = x.er_oper_code ")
_LYTIC = "lower(concat(di.name, ' ', ifnull(di.generic_name, ''))) regexp %(ft_lytic)s"


def _ft_icd(a):
    """ICD-10 ของกลุ่ม fast track: visit ER ที่ตรง + รหัสในตระกูลเดียวกัน (3 หลักแรก) ที่แอปไม่นับ"""
    fams = tuple(sorted({c[:3] for v in queries.FT_ICD.values() for c in v}))
    by = {str(r["code"] or "").strip(): num(r["n"]) for r in fetch(
        f"select d.icd10 code, count(distinct e.vn) n from er_regist e join ovst o on o.vn = e.vn "
        f"join ovstdiag d on d.vn = e.vn and left(d.icd10, 3) in %(fams)s {W} group by d.icd10", {**a, "fams": fams})}
    cnt = fetch_one("select " + ", ".join(
        f"sum(exists(select 1 from ovstdiag d where d.vn = e.vn and d.icd10 regexp %(ft_{k})s)) {k}"
        for k, *_ in FT_ICD_GROUPS) + f" from er_regist e join ovst o on o.vn = e.vn {W}", a) or {}
    out = []
    for k, label, impact in FT_ICD_GROUPS:
        codes = queries.FT_ICD[k]
        n = num(cnt.get(k))
        hit = sorted(((c, x) for c, x in by.items() if c.startswith(codes)), key=lambda kv: -kv[1])
        near = sorted(((c, x) for c, x in by.items() if c[:3] in {p[:3] for p in codes} and not c.startswith(codes)),
                      key=lambda kv: -kv[1])
        found = (f"{n:,} visit ER · " + ", ".join(f"{c} ({x:,})" for c, x in hit[:6]) if n
                 else "ไม่มี visit ER ที่ตรงรหัสในช่วงที่ตรวจ")
        if near:
            found += " · รหัสตระกูลเดียวกันที่ไม่นับ: " + ", ".join(f"{c} ({x:,})" for c, x in near[:4])
        out.append({"label": label, "env": f"ER_FT_{k.upper()}_ICD", "assume": ", ".join(codes), "found": found,
                    "fix": None if n else f"ตรวจว่า ER ลงรหัสโรคกลุ่มนี้อย่างไร แล้วตั้ง ER_FT_{k.upper()}_ICD",
                    "impact": impact, "n": n, "status": _status(False, not n)})
    return out


def _ft_oper(a):
    """หัตถการ ER ที่ผูก ICD-9-CM ของ ECG / CPR / Defib · หัตถการที่ชื่อตรงแต่ ICD-9 ไม่ตรง = แอปไม่นับ"""
    opers = fetch("select er_oper_code code, name, icd9cm from er_oper_code")
    used = {str(r["code"]): num(r["n"]) for r in fetch(
        f"select x.er_oper_code code, count(distinct e.vn) n {_ER_OPER} {W} group by x.er_oper_code", a)}
    out = []
    for k, label, kw, impact in FT_OPER_GROUPS:
        codes = queries.FT_ICD9[k]
        icd9 = lambda o: str(o.get("icd9cm") or "").replace(".", "").strip()
        mapped = [o for o in opers if icd9(o) in codes]
        cand = [o for o in opers if icd9(o) not in codes and re.search(kw, str(o.get("name") or ""), re.I)
                and used.get(str(o["code"]))]
        n = sum(used.get(str(o["code"]), 0) for o in mapped)
        nm = lambda o: f"{o['code']} {str(o.get('name') or '').strip()} ({used.get(str(o['code']), 0):,} visit)"
        found = ("ผูกกับ: " + ", ".join(nm(o) for o in mapped) if mapped
                 else f"ไม่มีหัตถการ ER ที่ใส่ ICD-9 {', '.join(codes)}")
        status = _status(not mapped and bool(cand), not mapped or bool(cand))
        if k == "ecg" and n:
            z = fetch_one(f"select count(*) n, sum({queries._ECG_M} = 0) z {_ER_OPER} {W} "
                          "and replace(c.icd9cm, '.', '') in %(ft_ecg)s and x.begin_time is not null", a) or {}
            zp = queries._pct(num(z.get("z")), num(z.get("n")))
            found += f" · เวลาเริ่มตรงกับเวลาเข้า ER พอดี {zp}%"
            if zp >= ECG_ZERO_WARN_PCT:
                found += " (สงสัยเป็นค่าตั้งต้น — Door-to-ECG จะเร็วกว่าจริง)"
                status = _status(status == "bad", True)
        if cand:
            found += " · ชื่อตรงแต่ ICD-9 ไม่ตรง (ไม่นับ): " + ", ".join(
                nm(o) + f" ICD-9 {icd9(o) or 'ว่าง'}" for o in cand)
        fix = None
        if cand:
            fix = (f"ใส่ icd9cm = {codes[0]} ให้หัตถการ " + ", ".join(str(o["code"]) for o in cand)
                   + " ใน HOSxP (er_oper_code)")
        elif not mapped:
            fix = f"ใส่ icd9cm = {codes[0]} ให้{label}ของ ER ใน HOSxP หรือตั้ง ER_FT_{k.upper()}_ICD9"
        out.append({"label": label, "env": f"ER_FT_{k.upper()}_ICD9", "assume": ", ".join(codes), "found": found,
                    "fix": fix, "impact": impact, "n": n, "status": status})
    return out


def _ft_lytic(a):
    """ยาละลายลิ่มเลือด — จับจากชื่อยา (ไม่มี env) · แสดงยาที่ตรงและจำนวน visit ER ที่สั่ง"""
    drugs = fetch(f"select di.icode code, di.name from drugitems di where {_LYTIC}", a)
    used = {str(r["code"]): num(r["n"]) for r in fetch(
        f"select x.icode code, count(distinct e.vn) n from er_regist e join ovst o on o.vn = e.vn "
        f"join opitemrece x on x.vn = e.vn join drugitems di on di.icode = x.icode {W} and {_LYTIC} "
        "group by x.icode", a)}
    found = (", ".join(f"{d['code']} {str(d.get('name') or '').strip()} ({used.get(str(d['code']), 0):,} visit)"
                       for d in drugs) if drugs else "ไม่พบยาที่ชื่อตรงใน drugitems")
    return {"label": "ยาละลายลิ่มเลือด (drugitems)", "env": None,
            "assume": "ชื่อยา/ชื่อสามัญมี " + " / ".join(queries.FT_LYTIC_RE.split("|")), "found": found,
            "fix": None if drugs else "ถ้า รพ. ไม่มียานี้ไม่ต้องแก้ · ถ้ามี ให้แจ้งผู้พัฒนาพร้อมชื่อยาใน drugitems",
            "impact": "บล็อก 20 Door-to-needle", "status": _status(False, not drugs)}


def _ft_gcs(a):
    """GCS ของผู้บาดเจ็บศีรษะ (er_nursing_detail) — 0/0/0 = ไม่ได้บันทึก"""
    r = fetch_one(f"""
        select count(*) n, sum(nd.gcs_e between 1 and 4 and nd.gcs_v between 1 and 5 and nd.gcs_m between 1 and 6) ok
        from er_regist e join ovst o on o.vn = e.vn left join er_nursing_detail nd on nd.vn = e.vn
        {W} and exists(select 1 from ovstdiag d where d.vn = e.vn and d.icd10 regexp %(ft_hi)s)""", a) or {}
    n, ok = num(r.get("n")), num(r.get("ok"))
    p = queries._pct(ok, n)
    return {"label": "GCS ของผู้บาดเจ็บศีรษะ (er_nursing_detail)", "env": None, "assume": "gcs_e / gcs_v / gcs_m",
            "found": f"บันทึก {p}% ({ok:,} จาก {n:,} ราย) — ไม่บันทึก (0/0/0) หรือเกินช่วง = “ไม่บันทึก”",
            "fix": "บันทึก GCS ในแบบบันทึกอุบัติเหตุ (ER) ทุกรายที่บาดเจ็บศีรษะ" if n and p < TIME_WARN_PCT else None,
            "impact": "บล็อก 21 แบ่งความรุนแรง", "status": _status(False, bool(n) and p < TIME_WARN_PCT)}


def audit(d1, d2):
    """Data Audit ทั้งหมด + สรุปเรื่องที่ควรแก้ (ใช้ทั้งหน้า /check และ setup_check.py)"""
    a = _args(d1, d2)
    b = _base(a)
    total = b["n"]
    res = {"base": b, "total": total, "times": _times(b)}
    res["ovstist"] = _safe(lambda: _group_audit(_dist("o.ovstist", a), _lookup("ovstist", "ovstist"),
                                                OVSTIST_GROUPS, total))
    res["dch"] = _safe(lambda: _group_audit(_dist("e.er_dch_type", a), _lookup("er_dch_type", "er_dch_type"),
                                            DCH_GROUPS, total))
    res["pt"] = _safe(lambda: _group_audit(_dist("e.er_pt_type", a), _lookup("er_pt_type", "er_pt_type"),
                                           PT_GROUPS, total))
    res["diag"] = _safe(lambda: _group_audit(
        _dist("d.diagtype", a, "join ovstdiag d on d.vn = e.vn"), _lookup("diagtype", "diagtype"),
        DIAG_GROUPS, total))
    for x in res["diag"].get("summary", []):           # ER ควรลงวินิจฉัยหลักเกือบทุกราย
        if total and x["pct"] < PDX_WARN_PCT:
            x["found"] += f" · ควร ≥ {PDX_WARN_PCT:g}% — ER ไม่ลงวินิจฉัยหลัก หรือรหัสวินิจฉัยหลักไม่ใช่ค่านี้"
            x["status"] = "bad" if x["status"] == "bad" else "warn"
    res["levels"] = _safe(_levels, a, b["n_er"])
    res["deps"] = _safe(_deps, a, total)
    res["providers"] = _safe(_providers, a, total)
    res["d2d"] = _safe(_d2d, a)
    cols = _safe(_st_cols, a)
    res["st_cols"] = cols if isinstance(cols, list) else []
    res["st"] = (_st_audit(cols, res["deps"].get("names", {})) if isinstance(cols, list)
                 else {"label": "คอลัมน์ “เริ่มตรวจ” ของแพทย์ (service_time)", **cols})

    codes = []
    for k in ("ovstist", "dch", "pt", "diag"):
        codes += res[k].get("summary", []) if "error" not in res[k] else [{"label": k, **res[k]}]
    for k in ("levels", "deps"):
        codes.append(res[k]["summary"] if "error" not in res[k] else {"label": k, **res[k]})
    codes.append(res["st"])
    codes += res["providers"].get("summary", []) if "error" not in res["providers"] else []
    for label, fn in (("Fast track (ICD-10)", _ft_icd), ("Fast track (หัตถการ ER)", _ft_oper),
                      ("ยาละลายลิ่มเลือด", lambda x: [_ft_lytic(x)]), ("GCS", lambda x: [_ft_gcs(x)])):
        r = _safe(fn, a)
        codes += r if isinstance(r, list) else [{"label": label, **r}]
    res["codes"] = codes

    issues = [f"ค่า env รูปแบบผิด ใช้ค่าเริ่มต้นแทน: {e}" for e in queries.CODE_ERRORS]
    if not total:
        issues.append("ไม่มี visit ER ที่คัดกรองแล้วในช่วงที่ตรวจ — เลือกช่วงวันที่อื่น หรือ ER ไม่ได้ใช้ er_regist")
    issues += [f"{c['label']}: {c['found']}" for c in codes if c.get("status") in ("warn", "bad")]
    issues += [f"{t['label']} มีข้อมูล {t['pct']}% ({t['impact']})" for t in res["times"] if t["status"] != "ok"]
    res["issues"] = issues
    res["audited"] = os.getenv("ER_DATA_AUDITED", "0") == "1"
    res["env_errors"] = list(queries.CODE_ERRORS)
    return res
