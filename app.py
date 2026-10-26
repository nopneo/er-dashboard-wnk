"""ER Dashboard — แดชบอร์ดบริหารจัดการห้องฉุกเฉิน (HOSxP)

รันเอง:      python app.py            → http://127.0.0.1:6200/
production:  gunicorn -b 0.0.0.0:6200 app:app   (ออกผ่าน sub-path เช่น /erdash/)
"""

import functools
import hashlib
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.parse
from datetime import date, timedelta
from io import BytesIO

try:
    import fcntl                      # Linux/macOS — ล็อกไฟล์ข้ามโปรเซสของ gunicorn
except ImportError:                   # Windows
    fcntl = None

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.security import check_password_hash

import db
import nurse_report
import queries
import render
from cache import TTLCache


_PREFIX_RE = re.compile(r"(/[A-Za-z0-9._~-]+)+/?")


class PrefixMiddleware:
    """อ่าน X-Forwarded-Prefix → SCRIPT_NAME และ X-Forwarded-Proto → url_scheme (sub-path หลัง proxy)"""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        prefix = environ.get("HTTP_X_FORWARDED_PREFIX", "")
        # รับเฉพาะ path ภายใน เช่น /erdash — กันคนส่ง header ปลอม (//evil.example) ให้ลิงก์ชี้ออกนอกเว็บ
        if prefix and _PREFIX_RE.fullmatch(prefix):
            environ["SCRIPT_NAME"] = prefix.rstrip("/")
            path = environ.get("PATH_INFO", "")
            if path.startswith(prefix):
                environ["PATH_INFO"] = path[len(prefix):] or "/"
        proto = environ.get("HTTP_X_FORWARDED_PROTO")
        if proto in ("http", "https"):
            environ["wsgi.url_scheme"] = proto
        return self.wsgi_app(environ, start_response)


app = Flask(__name__)
app.wsgi_app = PrefixMiddleware(app.wsgi_app)

VERSION = "1.1.0"
ICON_VERSION = "1"
DEV_CONTACT = "นายเผด็จชัย สิงหล โรงพยาบาลวังน้ำเขียว"

_HERE = os.path.dirname(os.path.abspath(__file__))
_hosp_cache = TTLCache(maxsize=1)


def hospital_name():
    """ชื่อโรงพยาบาล: env ER_HOSPITAL_NAME > opdconfig.hospitalname ของ HOSxP > "โรงพยาบาล" """
    env = os.getenv("ER_HOSPITAL_NAME")
    if env:
        return env
    hit = _hosp_cache.get("n", 3600)
    if hit:
        return hit
    try:
        name = (db.fetch_one("select hospitalname from opdconfig limit 1").get("hospitalname") or "").strip()
    except Exception:
        name = ""
    name = name or "โรงพยาบาล"
    _hosp_cache.set("n", name)
    return name


@app.context_processor
def _branding():
    img = lambda n: os.path.exists(os.path.join(_HERE, "static", "img", n))
    return {
        "hospital": hospital_name(),
        "hospital_en": os.getenv("ER_HOSPITAL_NAME_EN", ""),
        "logo_url": url_for("static", filename="img/hospital_logo.png" if img("hospital_logo.png")
                            else "img/logo.png") + "?v=" + ICON_VERSION,
        "hero_url": url_for("static", filename="img/hero.jpg") if img("hero.jpg") else None,
        "version": VERSION,
        "contact": os.getenv("ER_CONTACT", "ผู้ดูแลระบบสารสนเทศของโรงพยาบาล"),
        "dev_contact": os.getenv("ER_DEV_CONTACT", DEV_CONTACT),
        "css_v": VERSION,
    }


#: ทุกอย่างโหลดจากเว็บตัวเองเท่านั้น (ไม่มี CDN) · inline ใช้กับ style/สคริปต์ปุ่มส่งออกในหน้า
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "script-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'self'; "
       "form-action 'self'; frame-ancestors 'self'")


@app.after_request
def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    # cookie ผ่าน https (หลัง proxy) ให้ Secure — เข้าตรง http://<ip>:6200 บน LAN ยังใช้ได้
    if request.is_secure:
        resp.headers.setlist("Set-Cookie", [c if "; Secure" in c else c + "; Secure"
                                            for c in resp.headers.getlist("Set-Cookie")])
    return resp


#: ช่วงวันที่ยาวสุดที่ดึงได้ต่อครั้ง — กันการดึงหลายปีจนฐานข้อมูล HOSxP ช้าทั้งโรงพยาบาล
MAX_DAYS = int(os.getenv("ER_MAX_DAYS", "366"))


def _parse_date(v):
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        return None


def _range(max_days, default_d1):
    """(d1, d2) ที่ผ่านการตรวจแล้ว — ถูกต่อเข้า SQL จึงรับเฉพาะวันที่ที่ parse ได้"""
    today = date.today()
    d1 = _parse_date(request.args.get("d1")) or default_d1
    d2 = _parse_date(request.args.get("d2")) or today
    d2 = min(d2, today)
    d1 = min(d1, d2)
    if (d2 - d1).days >= max_days:
        d1 = d2 - timedelta(days=max_days - 1)
    return d1.isoformat(), d2.isoformat()


def _params():
    """(d1, d2, shift) ที่ผ่านการตรวจแล้ว — ค่าเหล่านี้ถูกต่อเข้า SQL จึงรับเฉพาะรูปแบบที่รู้จัก"""
    d1, d2 = _range(MAX_DAYS, date.today().replace(day=1))
    shift = request.args.get("shift") or "all"
    if shift not in queries.SHIFTS:
        shift = "all"
    return d1, d2, shift


@app.route("/")
def dashboard():
    d1, d2, shift = _params()
    return render_template("dashboard.html", d=queries.build(d1, d2, shift),
                           print_mode=request.args.get("print") == "1")


@app.route("/api/data.json")
def api_data():
    d1, d2, shift = _params()
    return jsonify(queries.build(d1, d2, shift))


def _fy_start(today=None):
    today = today or date.today()
    return date(today.year - (0 if today.month >= 10 else 1), 10, 1)


def _nurse_params():
    """ค่าเริ่มต้น = ตั้งแต่ต้นปีงบประมาณที่แล้ว (เห็นปีเต็ม 1 ปี + ปีปัจจุบัน)"""
    fy = _fy_start()
    return _range(nurse_report.MAX_DAYS, fy.replace(year=fy.year - 1))


# ---- รหัสเข้าดูรายงานพยาบาลตรวจแทนแพทย์ (มีชื่อพยาบาลรายคน) — ไม่ตั้ง ER_NP_PASS_HASH = เปิดดูได้เลย
# เก็บเฉพาะ hash: python -c "from werkzeug.security import generate_password_hash as g; print(g(input(), method='pbkdf2:sha256'))"
NP_PASS_HASH = os.getenv("ER_NP_PASS_HASH", "").strip()
#: เปลี่ยนรหัส = tag เปลี่ยน → session เดิมใช้ไม่ได้ทันที
_NP_TAG = hashlib.sha256(NP_PASS_HASH.encode()).hexdigest()[:16]
NP_LOGIN_HOURS = int(os.getenv("ER_NP_LOGIN_HOURS", "12"))
NP_MAX_FAIL = 10
NP_FAIL_WINDOW = 900
_np_fail = TTLCache(maxsize=512)

# gunicorn หลาย worker ต้องใช้ key เดียวกัน — ไม่ตั้ง ER_SECRET_KEY ก็ derive จาก hash ของรหัส
app.secret_key = (os.getenv("ER_SECRET_KEY")
                  or (hashlib.sha256(("erdash-session:" + NP_PASS_HASH).encode()).hexdigest() if NP_PASS_HASH
                      else secrets.token_hex(32)))
app.config.update(SESSION_COOKIE_NAME="erdash_session",   # ไม่ชนกับ cookie "session" ของแอปอื่นในโดเมนเดียวกัน
                  SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_HTTPONLY=True,
                  PERMANENT_SESSION_LIFETIME=timedelta(hours=NP_LOGIN_HOURS))
_np_token = URLSafeTimedSerializer(app.secret_key, salt="nurse-export")


def _np_ok():
    """ผ่านรหัสแล้ว หรือเป็น chromium ของปุ่มส่งออกที่ถือ token อายุสั้น"""
    if not NP_PASS_HASH or session.get("np") == _NP_TAG:
        return True
    try:
        return _np_token.loads(request.args.get("t", ""), max_age=120) == _NP_TAG
    except BadSignature:
        return False


#: เครื่อง reverse proxy ที่เชื่อ header IP ของผู้ใช้ได้ — คนอื่นส่ง header ปลอมมาเลี่ยงการนับรหัสผิดไม่ได้
TRUSTED_PROXIES = {x.strip() for x in os.getenv("ER_TRUSTED_PROXIES", "127.0.0.1,::1").split(",") if x.strip()}


def _client_ip():
    if request.remote_addr not in TRUSTED_PROXIES:
        return request.remote_addr
    xff = request.headers.get("X-Forwarded-For", "")
    return request.headers.get("CF-Connecting-IP") or (xff.split(",")[-1].strip() if xff else request.remote_addr)


def nurse_required(view):
    @functools.wraps(view)
    def wrapped(*a, **kw):
        if not _np_ok():
            return redirect(url_for("nurse_login", d1=request.args.get("d1"), d2=request.args.get("d2")))
        return view(*a, **kw)
    return wrapped


@app.route("/nurse/login", methods=["GET", "POST"])
def nurse_login():
    if not NP_PASS_HASH:
        return redirect(url_for("nurse"))
    src = request.form if request.method == "POST" else request.args
    back = {k: v.isoformat() for k in ("d1", "d2") if (v := _parse_date(src.get(k)))}
    error = None
    if request.method == "POST":
        ip = _client_ip()
        fails = _np_fail.get(ip, NP_FAIL_WINDOW) or 0
        if fails >= NP_MAX_FAIL:
            error = "ใส่รหัสผิดหลายครั้ง กรุณารอ 15 นาทีแล้วลองใหม่"
        elif check_password_hash(NP_PASS_HASH, request.form.get("passcode", "").strip()):
            session.clear()
            session.permanent = True
            session["np"] = _NP_TAG
            return redirect(url_for("nurse", **back))
        else:
            _np_fail.set(ip, fails + 1)
            time.sleep(1)                      # ชะลอการเดารหัส
            error = "รหัสไม่ถูกต้อง"
    return render_template("nurse_login.html", error=error, back=back), (401 if error else 200)


@app.route("/nurse/logout")
def nurse_logout():
    session.pop("np", None)
    return redirect(url_for("dashboard"))


def _nurse_presets():
    today, fy = date.today(), _fy_start()
    prev = fy.replace(year=fy.year - 1)
    last_m = today.replace(day=1) - timedelta(days=1)
    be = lambda d: d.year + 543 + 1
    out = [
        ("ปีงบ " + str(be(fy)), fy, today),
        ("ปีงบ " + str(be(prev)), prev, fy - timedelta(days=1)),
        ("2 ปีงบล่าสุด", prev, today),
        # ถอย 330 วันจากวันที่ 1 ของเดือนล่าสุด ตกในเดือนที่ 11 ก่อนหน้าเสมอ
        ("12 เดือนที่ครบ", (last_m.replace(day=1) - timedelta(days=330)).replace(day=1), last_m),
        ("3 ปีงบ", prev.replace(year=prev.year - 1), today),
    ]
    return [{"label": l, "d1": a.isoformat(), "d2": b.isoformat()} for l, a, b in out]


@app.route("/nurse")
@nurse_required
def nurse():
    d1, d2 = _nurse_params()
    return render_template("nurse.html", d=nurse_report.build(d1, d2), presets=_nurse_presets(),
                           print_mode=request.args.get("print") == "1", locked=bool(NP_PASS_HASH))


@app.route("/nurse.xlsx")
@nurse_required
def nurse_xlsx():
    d1, d2 = _nurse_params()
    blob = nurse_report.to_xlsx(nurse_report.build(d1, d2), hospital_name())
    return send_file(BytesIO(blob), as_attachment=True, download_name=f"er_nurse_{d1}_{d2}.xlsx",
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class _RenderLock:
    """เรนเดอร์ทีละงานทั้งเครื่อง — chromium กินแรมสูง (ล็อกไฟล์ข้าม worker ของ gunicorn)"""

    def __init__(self, path):
        self._path = path
        self._thread = threading.Lock()
        self._fd = None

    def acquire(self, timeout):
        deadline = time.monotonic() + timeout
        got = self._thread.acquire(timeout=timeout) if timeout > 0 else self._thread.acquire(blocking=False)
        if not got:
            return False
        if fcntl is None:
            return True
        fd = os.open(self._path, os.O_CREAT | os.O_RDWR, 0o600)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fd = fd
                return True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    os.close(fd)
                    self._thread.release()
                    return False
                time.sleep(0.5)

    def release(self):
        if self._fd is not None:
            fd, self._fd = self._fd, None
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        self._thread.release()


_render_lock = _RenderLock(os.path.join(tempfile.gettempdir(), "er_dashboard_render.lock"))
EXPORT_WAIT = int(os.getenv("ER_EXPORT_WAIT", "0"))
EXPORT_CACHE_TTL = int(os.getenv("ER_EXPORT_CACHE_TTL", "300"))
_export_cache = TTLCache(maxsize=8)
EXPORT_ENABLED = os.getenv("ER_EXPORT", "1") != "0"
MIME = {"png": "image/png", "jpg": "image/jpeg", "pdf": "application/pdf"}


@app.context_processor
def _export_flag():
    return {"export_enabled": EXPORT_ENABLED}


@app.route("/export.<fmt>")
def export(fmt):
    """ส่งออกหน้าแดชบอร์ดเป็น PNG / JPG / PDF ด้วย chromium headless (เรียกหน้าเดิมด้วย ?print=1)"""
    if fmt not in MIME or not EXPORT_ENABLED:
        return {"error": "รองรับเฉพาะ png, jpg, pdf" if EXPORT_ENABLED else "ปิดการส่งออกไฟล์"}, 404

    if request.args.get("page") == "nurse":       # รายงานพยาบาลตรวจแทนแพทย์
        if not _np_ok():
            return redirect(url_for("nurse_login", d1=request.args.get("d1"), d2=request.args.get("d2")))
        d1, d2 = _nurse_params()
        qs = urllib.parse.urlencode({"d1": d1, "d2": d2, "print": "1", "t": _np_token.dumps(_NP_TAG)})
        url = f"http://127.0.0.1:{os.getenv('PORT', '6200')}/nurse?{qs}"
        name = f"er_nurse_{d1}_{d2}.{fmt}"
    else:
        d1, d2, shift = _params()
        qs = urllib.parse.urlencode({"d1": d1, "d2": d2, "shift": shift, "print": "1"})
        url = f"http://127.0.0.1:{os.getenv('PORT', '6200')}/?{qs}"
        name = f"er_{d1}_{d2}{'' if shift == 'all' else '_' + shift}.{fmt}"
    blob = _export_cache.get(name, EXPORT_CACHE_TTL)
    if blob is not None:
        return send_file(BytesIO(blob), mimetype=MIME[fmt], as_attachment=True, download_name=name)
    if not _render_lock.acquire(timeout=EXPORT_WAIT):
        return ({"error": "กำลังส่งออกไฟล์ของผู้ใช้อื่นอยู่ ลองใหม่ในอีกครู่"}, 503, {"Retry-After": "30"})
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, name)
            render.render_to(fmt, url, out)
            blob = open(out, "rb").read()
        _export_cache.set(name, blob)
        return send_file(BytesIO(blob), mimetype=MIME[fmt], as_attachment=True, download_name=name)
    except Exception as e:
        app.logger.exception("export failed")
        return {"error": f"ส่งออกไม่สำเร็จ: {type(e).__name__}"}, 500
    finally:
        _render_lock.release()


@app.route("/manifest.webmanifest")
def manifest():
    ic = lambda f: url_for("static", filename="icon/" + f) + "?v=" + ICON_VERSION
    resp = jsonify({
        "name": "ER Dashboard — " + hospital_name(),
        "short_name": "ER Dashboard",
        "description": "แดชบอร์ดบริหารจัดการห้องฉุกเฉิน",
        "start_url": url_for("dashboard"),
        "scope": url_for("dashboard"),
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#fff1f1",
        "theme_color": "#b91c1c",
        "icons": [
            {"src": ic("icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": ic("icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": ic("icon-192-maskable.png"), "sizes": "192x192",
             "type": "image/png", "purpose": "maskable"},
            {"src": ic("icon-512-maskable.png"), "sizes": "512x512",
             "type": "image/png", "purpose": "maskable"},
        ],
    })
    resp.mimetype = "application/manifest+json"
    return resp


@app.errorhandler(queries.Busy)
def _busy(_e):
    return ({"error": "ระบบกำลังประมวลผลคำขออื่นอยู่ ลองใหม่ในอีกครู่"}, 503, {"Retry-After": "30"})


@app.route("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "0") == "1"
    app.run(host="127.0.0.1" if debug else os.getenv("ER_HOST", "0.0.0.0"),
            port=int(os.getenv("PORT", "6200")), debug=debug)
