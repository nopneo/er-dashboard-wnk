"""ชั้นเชื่อมต่อฐานข้อมูล HOSxP

รองรับ 2 โหมด
  - direct : ต่อ MariaDB ตรง ๆ ด้วย pymysql อ่านค่าจาก .env  (ใช้บนเซิร์ฟเวอร์ production)
  - ssh    : ยิงคำสั่งผ่าน `ssh <host> mariadb hos` แล้วอ่าน TSV  (ใช้ตอน dev บนเครื่องที่ไม่มีรหัส DB)

เลือกโหมดด้วย env `ER_DB_MODE` (default: direct ถ้ามี DB_PASS, ไม่งั้น ssh)
ข้อมูลใน HOSxP เก็บเป็น tis-620 จึงต้องระบุ charset ให้ถูกทั้งสองทาง

รหัสที่ตั้งได้ด้วย env (ER_*_CODES ฯลฯ) ส่งเป็นพารามิเตอร์: fetch("... in %(ems)s", {"ems": ("04", "06")})
เมื่อส่งพารามิเตอร์ เครื่องหมาย % ที่เป็นตัวอักษรใน SQL ต้องเขียนเป็น %% (เช่น like 'Z%%')
"""

import os
import shlex
import subprocess
from decimal import Decimal

try:
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "hos")
DB_USER = os.getenv("DB_USER", "")
DB_PASS = os.getenv("DB_PASS", "")
SSH_HOST = os.getenv("ER_SSH_HOST", "")
DB_TIMEOUT = int(os.getenv("ER_DB_TIMEOUT", "60"))

MODE = os.getenv("ER_DB_MODE") or ("direct" if DB_PASS else "ssh")


def _fetch_direct(sql, args=None):
    import pymysql

    conn = pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        charset="tis620",
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=DB_TIMEOUT,
        init_command="SET SESSION TRANSACTION READ ONLY",
    )
    try:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.close()


def _bind(sql, args):
    """แทนพารามิเตอร์ลง SQL สำหรับโหมด ssh — escape แบบเดียวกับ pymysql"""
    if args is None:
        return sql
    from pymysql.converters import escape_item

    if isinstance(args, dict):
        return sql % {k: escape_item(v, "utf8") for k, v in args.items()}
    return sql % tuple(escape_item(v, "utf8") for v in args)


def _fetch_ssh(sql, args=None):
    """รันผ่าน ssh แล้วแปลง TSV กลับเป็น list[dict]"""
    sql = _bind(sql, args)
    remote = (
        "mariadb --default-character-set=tis620 --batch --raw "
        f"{shlex.quote(DB_NAME)} -e {shlex.quote(' '.join(sql.split()))}"
    )
    if not SSH_HOST:
        raise RuntimeError("โหมด ssh ต้องตั้ง ER_SSH_HOST (หรือตั้ง DB_PASS เพื่อใช้โหมด direct)")
    # BatchMode + stdin ปิด: ไม่ให้ ssh ค้างรอถามรหัส/อ่าน stdin เมื่อรันใต้ dev server
    proc = subprocess.run(["ssh", "-o", "BatchMode=yes", SSH_HOST, remote], capture_output=True,
                          stdin=subprocess.DEVNULL, timeout=DB_TIMEOUT)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace"))

    text = proc.stdout.decode("tis-620", "replace")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    rows = []
    for ln in lines[1:]:
        cells = ln.split("\t")
        rows.append({k: (None if v == "NULL" else v) for k, v in zip(header, cells)})
    return rows


def fetch(sql, args=None):
    rows = _fetch_direct(sql, args) if MODE == "direct" else _fetch_ssh(sql, args)
    return [{k: (float(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in rows]


def fetch_one(sql, args=None):
    rows = fetch(sql, args)
    return rows[0] if rows else {}


def num(v, default=0):
    """แปลงค่าที่ได้จาก DB (อาจเป็น str จากโหมด ssh) ให้เป็นตัวเลขเสมอ"""
    if v is None or v == "":
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return int(f) if f == int(f) else f
