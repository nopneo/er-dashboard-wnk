"""ตรวจความพร้อมก่อนใช้งาน ER Dashboard — รันหลังกรอกค่าฐานข้อมูลใน .env

    python setup_check.py              ตรวจสภาพแวดล้อม + ฐานข้อมูล + Data Audit 30 วันล่าสุด
    python setup_check.py --days 7     ตรวจข้อมูลย้อนหลัง 7 วัน
    python setup_check.py --no-audit   ข้าม Data Audit (เร็วกว่า)
    Docker: docker exec <ชื่อ container> python setup_check.py

อ่านฐานข้อมูลอย่างเดียว ไม่แก้ข้อมูลใน HOSxP · แสดงเฉพาะจำนวนนับ ไม่มีข้อมูลรายคน
ผล ✘ = ต้องแก้ก่อนใช้งาน · ⚠ = ใช้ได้แต่ควรตรวจ · ส่งผลทั้งหมดนี้มาด้วยเมื่อแจ้งปัญหา
"""

import argparse
import importlib
import os
import platform
import re
import sys
import time
from datetime import date, datetime, timedelta

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))

#: ตารางที่แดชบอร์ดอ่าน — ไม่มี = บล็อกที่ใช้ตารางนั้นล้ม
TABLES = ["er_regist", "er_emergency_type", "ovst", "ovstdiag", "icd101", "patient", "service_time",
          "lab_head", "xray_report", "opitemrece", "drugitems", "referout", "hospcode", "ipt",
          "kskdepartment", "opdscreen", "doctor", "holiday"]
#: ไม่มีก็ใช้งานได้ (ชื่อ รพ. ตั้งด้วย ER_HOSPITAL_NAME ได้ / หน้า /check ใช้แสดงชื่อรหัสประกอบ)
TABLES_OPTIONAL = ["opdconfig", "ovstist", "er_dch_type", "er_pt_type", "er_emergency_level", "diagtype",
                   "provider_type"]
#: สิทธิ์ที่บัญชีอ่านอย่างเดียวมีได้
SAFE_PRIVS = {"SELECT", "USAGE", "SHOW VIEW"}

results = []      # (ระดับ, ข้อความ) — ok / warn / fail / info


def say(level, msg):
    results.append((level, msg))
    mark = {"ok": "✔", "warn": "⚠", "fail": "✘", "info": "·"}[level]
    print(f"  {mark} {msg}")


def head(title):
    print(f"\n=== {title} " + "=" * max(0, 60 - len(title)))


def risky_privs(grant_lines, dbname=None):
    """บรรทัดผลของ SHOW GRANTS → สิทธิ์ที่เกินกว่าอ่านอย่างเดียว (เช่น INSERT, ALL PRIVILEGES)
    dbname = นับเฉพาะสิทธิ์บนฐานข้อมูลนี้และ *.* (สิทธิ์เขียนบนฐานอื่น เช่น `test` ไม่กระทบ HOSxP)"""
    out = set()
    for line in grant_lines:
        m = re.match(r"\s*GRANT\s+(.+?)\s+ON\s+(\S+)", line or "", re.I)
        if not m:
            continue
        target = m.group(2).split(".")[0].strip("`'\"")
        if dbname and target not in ("*", dbname):
            continue
        privs = re.sub(r"\(.*?\)", "", m.group(1))          # SELECT (col1, col2) → SELECT
        for p in privs.split(","):
            p = p.strip().upper()
            if p and p not in SAFE_PRIVS:
                out.add(p)
        if re.search(r"WITH\s+GRANT\s+OPTION", line, re.I):
            out.add("GRANT OPTION")
    return sorted(out)


def check_env():
    head("1. สภาพแวดล้อมของเครื่อง")
    v = sys.version_info
    say("ok" if v >= (3, 9) else "fail", f"Python {platform.python_version()} (ต้อง 3.9 ขึ้นไป)")

    need = [("flask", "Flask"), ("pymysql", "PyMySQL"), ("dotenv", "python-dotenv"), ("PIL", "Pillow"),
            ("openpyxl", "openpyxl"),
            ("waitress" if os.name == "nt" else "gunicorn", "waitress" if os.name == "nt" else "gunicorn")]
    missing = []
    for mod, pkg in need:
        try:
            importlib.import_module(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        say("fail", "ขาดไลบรารี: " + ", ".join(missing) + " — รัน pip install -r requirements.txt")
    else:
        say("ok", "ไลบรารี Python ครบตาม requirements.txt")

    offset = -time.timezone if not time.localtime().tm_isdst else -time.altzone
    if offset == 7 * 3600:
        say("ok", f"เวลาเครื่อง {datetime.now():%Y-%m-%d %H:%M} (UTC+7)")
    else:
        say("warn", f"เขตเวลาเครื่องเป็น UTC{offset / 3600:+g} ไม่ใช่ UTC+7 — ช่วง “วันนี้” และค่าเริ่มต้นของช่วงวันที่จะเพี้ยน "
                    "(Linux: timedatectl set-timezone Asia/Bangkok · Docker: ใช้ Dockerfile ของโปรเจกต์)")


def check_config():
    head("2. การตั้งค่า (.env)")
    import db
    import queries

    env_path = os.path.join(HERE, ".env")
    say("ok" if os.path.exists(env_path) else "info",
        ".env อยู่ในโฟลเดอร์แอป" if os.path.exists(env_path) else "ไม่มีไฟล์ .env — อ่านค่าจากตัวแปรของระบบ/Docker แทน")
    say("info", f"โหมดฐานข้อมูล: {db.MODE}")
    if db.MODE == "direct":
        bad = [k for k in ("DB_HOST", "DB_USER", "DB_PASS")
               if not os.getenv(k) or os.getenv(k, "").startswith("<")]
        if bad:
            say("fail", "ยังไม่ได้กรอก " + ", ".join(bad) + " ใน .env")
        if os.getenv("DB_HOST") in ("127.0.0.1", "localhost") and os.path.exists("/.dockerenv"):
            say("warn", "รันใน container แต่ DB_HOST เป็น 127.0.0.1 = ตัว container เอง — ใช้ IP เครื่องฐานข้อมูล")
    for e in queries.CODE_ERRORS:
        say("warn", f"ค่า env รูปแบบผิด ใช้ค่าเริ่มต้นแทน: {e}")

    import render
    if os.getenv("ER_EXPORT", "1") == "0":
        say("info", "ปิดปุ่มส่งออก (ER_EXPORT=0) — ไม่ต้องใช้ Chrome")
    else:
        try:
            say("ok", f"พบเบราว์เซอร์สำหรับปุ่มส่งออก: {render.find_browser()}")
        except SystemExit:
            say("warn", "ไม่พบ Chrome / Edge / Chromium — ปุ่มส่งออก JPG/PNG/PDF จะใช้ไม่ได้ (แดชบอร์ดยังใช้ได้) "
                        "หรือตั้ง ER_EXPORT=0 เพื่อซ่อนปุ่ม")


def check_db():
    head("3. ฐานข้อมูล HOSxP")
    from db import fetch, fetch_one

    try:
        r = fetch_one("select version() v, now() n, database() d")
    except Exception as e:
        say("fail", f"ต่อฐานข้อมูลไม่ได้: {str(e)[:200]}")
        print("      ตรวจ DB_HOST / พอร์ต / firewall / GRANT ให้ IP เครื่องนี้ (INSTALL.md ข้อ 1)")
        return False
    say("ok", f"ต่อฐานข้อมูลได้: {r.get('v')} · ฐาน {r.get('d')}")

    try:
        db_now = datetime.fromisoformat(str(r.get("n")))
        diff = abs((db_now - datetime.now()).total_seconds()) / 60
        if diff > 5:
            say("warn", f"เวลาฐานข้อมูล ({db_now:%H:%M}) ต่างจากเครื่องนี้ {diff:.0f} นาที — ตรวจเขตเวลาของทั้งสองเครื่อง")
        else:
            say("ok", "เวลาฐานข้อมูลตรงกับเครื่องนี้")
    except ValueError:
        pass

    try:
        rows = fetch("show grants for current_user()")
        risky = risky_privs([next(iter(x.values()), "") for x in rows], r.get("d"))
        if risky:
            say("warn", "บัญชีนี้มีสิทธิ์เกินกว่าอ่านอย่างเดียว: " + ", ".join(risky) +
                " — ควรใช้บัญชีที่มีแค่ SELECT (INSTALL.md ข้อ 1)")
        else:
            say("ok", f"บัญชีฐานข้อมูลอ่าน {r.get('d')} ได้อย่างเดียว (SELECT)")
    except Exception:
        say("info", "อ่านสิทธิ์ของบัญชีไม่ได้ (SHOW GRANTS) — ข้าม")

    have = {str(x["t"]).lower() for x in fetch(
        "select table_name t from information_schema.tables where table_schema = database()")}
    miss = [t for t in TABLES if t not in have]
    say("fail" if miss else "ok",
        ("ไม่พบตาราง: " + ", ".join(miss) + " — ฐานข้อมูลนี้อาจไม่ใช่ HOSxP หรือบัญชีไม่มีสิทธิ์อ่าน") if miss
        else f"มีตารางที่แดชบอร์ดใช้ครบ {len(TABLES)} ตาราง")
    opt = [t for t in TABLES_OPTIONAL if t not in have]
    if opt:
        say("info", "ไม่มีตารางเสริม: " + ", ".join(opt) + " (ใช้งานได้ตามปกติ หน้า /check จะไม่มีชื่อรหัสบางกลุ่ม)")
    if miss:
        return False

    names = [x["department"] or "" for x in fetch("select department from kskdepartment limit 50")]
    thai = [n for n in names if re.search(r"[฀-๿]", n)]
    garbled = any(re.search(r"[ÃÂàáâ¡-ÿ]{2,}|\?{3,}", n) for n in names)
    if garbled or not thai:
        say("warn", "ชื่อแผนกอ่านเป็นภาษาไทยไม่ได้ — ตรวจ charset ของฐานข้อมูล (HOSxP ใช้ tis620)")
    else:
        say("ok", f"อ่านชื่อแผนกภาษาไทยได้ (เช่น “{thai[0]}”)")

    last = fetch_one("select max(vstdate) d from er_regist "
                     "where vstdate <= curdate() and enter_er_time is not null").get("d")
    if not last:
        say("fail", "ตาราง er_regist ไม่มี visit ที่มีเวลาเข้า ER (enter_er_time) เลย — ห้องฉุกเฉินต้องใช้ระบบ ER ของ HOSxP")
        return False
    age = (date.today() - date.fromisoformat(str(last)[:10])).days
    say("ok" if age <= 3 else "warn",
        f"visit ER ล่าสุดวันที่ {last}" + ("" if age <= 3 else f" ({age} วันก่อน) — ใช่ฐานข้อมูลที่ใช้งานจริงหรือไม่ (replica ไม่อัปเดต?)"))
    return True


def check_audit(days):
    head(f"4. Data Audit — รหัสของโรงพยาบาลนี้ ({days} วันล่าสุด)")
    import check

    d2 = date.today().isoformat()
    d1 = (date.today() - timedelta(days=days - 1)).isoformat()
    try:
        a = check.audit(d1, d2)
    except Exception as e:
        say("fail", f"ตรวจข้อมูลไม่สำเร็จ: {str(e)[:200]}")
        return
    say("info", f"{a['base'].get('n_er', 0):,} visit ER · คัดกรองแล้ว {a['total']:,}")
    for i in a["issues"]:
        say("warn", i)
    for c in a["codes"]:
        if c.get("fix") and c.get("status") in ("warn", "bad") and "=" in c["fix"]:
            print(f"      เสนอ: {c['fix']}")
    if not a["issues"]:
        say("ok", "ไม่พบรหัสหรือข้อมูลที่ผิดปกติ")
    print("      รายละเอียดและวิธีแก้: เปิดหน้า /check ของแดชบอร์ดจากเครื่องในโรงพยาบาล")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30, help="ตรวจข้อมูลย้อนหลังกี่วัน (ค่าเริ่มต้น 30, สูงสุด 92)")
    ap.add_argument("--no-audit", action="store_true", help="ข้าม Data Audit")
    args = ap.parse_args()

    os.chdir(HERE)
    sys.path.insert(0, HERE)
    print(f"ER Dashboard — ตรวจความพร้อม · {datetime.now():%Y-%m-%d %H:%M} · {platform.platform()}")
    check_env()
    if any(lv == "fail" for lv, _ in results):
        print("\nแก้ข้อ ✘ ด้านบนก่อน แล้วรันใหม่")
        return 1
    check_config()
    db_ok = check_db()
    if db_ok and not args.no_audit:
        check_audit(min(92, max(1, args.days)))

    n = {lv: sum(1 for x, _ in results if x == lv) for lv in ("ok", "warn", "fail")}
    head("สรุป")
    print(f"  ✔ {n['ok']} ผ่าน · ⚠ {n['warn']} ควรตรวจ · ✘ {n['fail']} ต้องแก้")
    if n["fail"]:
        print("  ยังไม่พร้อมใช้งาน — แก้ข้อ ✘ แล้วรันใหม่")
    elif n["warn"]:
        print("  ใช้งานได้ แต่ตัวเลขบางส่วนอาจคลาดเคลื่อน — ตรวจข้อ ⚠ และหน้า /check ก่อนใช้จริง")
    else:
        print("  พร้อมใช้งาน — เทียบตัวเลขกับรายงาน ER เดิม 1–2 สัปดาห์ แล้วตั้ง ER_DATA_AUDITED=1")
    return 1 if n["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
