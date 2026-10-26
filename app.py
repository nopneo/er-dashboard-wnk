"""ER Dashboard — แดชบอร์ดบริหารจัดการห้องฉุกเฉิน (HOSxP)

รันเอง:      python app.py            → http://127.0.0.1:6200/
production:  gunicorn -b 0.0.0.0:6200 app:app   (ออกผ่าน sub-path เช่น /erdash/)
"""

import os
import re
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

from flask import Flask, jsonify, render_template, request, send_file, url_for

import db
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

VERSION = "1.0.0"
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
    return resp


#: ช่วงวันที่ยาวสุดที่ดึงได้ต่อครั้ง — กันการดึงหลายปีจนฐานข้อมูล HOSxP ช้าทั้งโรงพยาบาล
MAX_DAYS = int(os.getenv("ER_MAX_DAYS", "366"))


def _parse_date(v):
    try:
        return date.fromisoformat(v)
    except (TypeError, ValueError):
        return None


def _params():
    """(d1, d2, shift) ที่ผ่านการตรวจแล้ว — ค่าเหล่านี้ถูกต่อเข้า SQL จึงรับเฉพาะรูปแบบที่รู้จัก"""
    today = date.today()
    d1 = _parse_date(request.args.get("d1")) or today.replace(day=1)
    d2 = _parse_date(request.args.get("d2")) or today
    d2 = min(d2, today)
    d1 = min(d1, d2)
    if (d2 - d1).days >= MAX_DAYS:
        d1 = d2 - timedelta(days=MAX_DAYS - 1)
    shift = request.args.get("shift") or "all"
    if shift not in queries.SHIFTS:
        shift = "all"
    return d1.isoformat(), d2.isoformat(), shift


@app.route("/")
def dashboard():
    d1, d2, shift = _params()
    return render_template("dashboard.html", d=queries.build(d1, d2, shift),
                           print_mode=request.args.get("print") == "1")


@app.route("/api/data.json")
def api_data():
    d1, d2, shift = _params()
    return jsonify(queries.build(d1, d2, shift))


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
