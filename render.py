"""ออกรายงานเป็นไฟล์รูป PNG (และ PDF) จากหน้าแดชบอร์ด

ใช้ Chrome/Edge โหมด headless ที่ติดตั้งอยู่แล้ว ไม่ต้องลงไลบรารีเพิ่ม

ตัวอย่าง
    python render.py                                   # เดือนปัจจุบัน ทุกเวร
    python render.py --d1 2026-09-01 --d2 2026-09-30   # ระบุช่วงวันที่
    python render.py --shift B --pdf                   # เฉพาะเวรบ่าย + ออก PDF ด้วย

สคริปต์จะสตาร์ต Flask ให้เองถ้ายังไม่มีตัวไหนรันอยู่ที่พอร์ตนั้น
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "out")

#: ความกว้าง .sheet (1120) + padding เผื่อ scrollbar
WIDTH = 1632
#: สูงเผื่อไว้ก่อน แล้วค่อยตัดพื้นหลังส่วนเกินทิ้งด้วย Pillow
HEIGHT = 4600

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    "/usr/lib64/chromium-browser/headless_shell",   # AlmaLinux: dnf install chromium-headless
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
]


def _no_sandbox():
    """ปิด sandbox ของ chromium เฉพาะเมื่อจำเป็น — sandbox กันโค้ดในหน้าเว็บไม่ให้แตะเครื่อง

    Linux: รันด้วย root ต้องปิด (chromium ไม่ยอมเปิด sandbox ให้ root) · user ทั่วไปเปิดไว้
    Windows: ปิดตามเดิม (service มักรันด้วย SYSTEM ซึ่ง sandbox ใช้ไม่ได้)
    env IPD_CHROME_NO_SANDBOX=1 บังคับปิด (เช่นเครื่องที่ปิด user namespace ไว้)
    """
    if os.getenv("ER_CHROME_NO_SANDBOX") == "1":
        return True
    geteuid = getattr(os, "geteuid", None)
    return geteuid is None or geteuid() == 0


def _base_args(browser, profile):
    """headless_shell รับแค่ --headless ส่วน Chrome/Edge รุ่นใหม่ใช้ --headless=new"""
    flag = "--headless" if "headless_shell" in browser else "--headless=new"
    return ([browser, flag, "--disable-gpu"] + (["--no-sandbox"] if _no_sandbox() else [])
            + ["--disable-dev-shm-usage", f"--user-data-dir={profile}"])


def find_browser():
    for b in BROWSERS:
        if os.path.exists(b):
            return b
    for name in ("chromium", "google-chrome", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    raise SystemExit("ไม่พบ Chrome/Edge/Chromium สำหรับ render รูป")


def port_open(port):
    with socket.socket() as s:
        s.settimeout(0.7)
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_server(port):
    """สตาร์ต Flask ถ้ายังไม่มีตัวไหนฟังอยู่ คืน process ที่ต้องปิดทีหลัง (หรือ None)"""
    if port_open(port):
        return None
    env = dict(os.environ, PORT=str(port), FLASK_DEBUG="0")
    proc = subprocess.Popen([sys.executable, "app.py"], cwd=HERE, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if port_open(port):
            return proc
        time.sleep(0.5)
    proc.terminate()
    raise SystemExit(f"สตาร์ต Flask ที่พอร์ต {port} ไม่สำเร็จ")


def shot(browser, url, png_path, scale=2):
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(_base_args(browser, profile) + [
            "--hide-scrollbars",
            f"--force-device-scale-factor={scale}",   # 2 = คมระดับ retina
            "--virtual-time-budget=8000",             # รอให้ฟอนต์/เลย์เอาต์นิ่ง
            f"--window-size={WIDTH},{HEIGHT}",
            f"--screenshot={png_path}",
            url,
        ], check=True, timeout=180,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def trim_bottom(png_path):
    """ตัดพื้นหลังว่างด้านล่างทิ้ง เหลือเฉพาะส่วนที่มีเนื้อหา"""
    from PIL import Image

    im = Image.open(png_path).convert("RGB")
    w, h = im.size
    bg = im.getpixel((4, h - 4))
    last = h - 1
    for y in range(h - 1, 0, -1):
        row = im.crop((0, y, w, y + 1)).getcolors(maxcolors=w * 2) or []
        if not (len(row) == 1 and row[0][1] == bg):
            last = y
            break
    pad = int(14 * (w / WIDTH))          # เผื่อขอบล่างตามอัตราส่วน device scale
    im.crop((0, 0, w, min(last + pad, h))).save(png_path)
    return im.size[0], min(last + pad, h)


def to_pdf(browser, url, pdf_path):
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run(_base_args(browser, profile) + [
            "--no-pdf-header-footer", "--virtual-time-budget=8000",
            f"--print-to-pdf={pdf_path}", url,
        ], check=True, timeout=180,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def to_jpg(png_path, jpg_path, quality=88):
    """แปลง PNG ที่ตัดขอบแล้วเป็น JPEG (ไฟล์เล็กกว่ามาก เหมาะกับส่งไลน์)"""
    from PIL import Image

    Image.open(png_path).convert("RGB").save(
        jpg_path, "JPEG", quality=quality, optimize=True, progressive=True)


#: ความกว้างหน้ากระดาษของ PDF (A3 = 297 มม.) — ความสูงคิดตามสัดส่วนภาพ
PDF_PAGE_WIDTH_MM = 297


def png_to_pdf(png_path, pdf_path):
    """ห่อภาพที่ตัดขอบแล้วเป็น PDF หน้าเดียว

    ใช้วิธีนี้แทน --print-to-pdf ของ chromium เพราะ chromium ตัดตามขนาดกระดาษ
    ทำให้อินโฟกราฟิกแตกเป็น 5 หน้า ส่วนวิธีนี้ได้หน้าเดียวตรงกับ PNG เป๊ะ
    """
    from PIL import Image

    im = Image.open(png_path).convert("RGB")
    dpi = im.width / (PDF_PAGE_WIDTH_MM / 25.4)      # ให้กว้างเท่าหน้า A3 พอดี
    im.save(pdf_path, "PDF", resolution=dpi)
    return im.size


def render_to(fmt, url, out_path):
    """เรนเดอร์หน้า url ออกเป็นไฟล์ตามชนิดที่ขอ — ใช้ร่วมกันระหว่าง CLI และเว็บ

    fmt: 'png' | 'jpg' | 'pdf'  (ทั้งสามชนิดมาจากภาพเดียวกัน หน้าตาจึงตรงกันเสมอ)
    """
    browser = find_browser()
    png = out_path if fmt == "png" else out_path + ".tmp.png"
    shot(browser, url, png)
    trim_bottom(png)
    if fmt == "jpg":
        to_jpg(png, out_path)
    elif fmt == "pdf":
        png_to_pdf(png, out_path)
    if png != out_path:
        os.remove(png)
    return out_path


def main():
    today = date.today()
    ap = argparse.ArgumentParser(description="ออกรายงาน ER Dashboard เป็นไฟล์รูป/PDF")
    ap.add_argument("--d1", default=today.replace(day=1).isoformat())
    ap.add_argument("--d2", default=today.isoformat())
    ap.add_argument("--shift", default="all", choices=["all", "D", "C", "B"])
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "6200")))
    ap.add_argument("--out", default=None, help="ชื่อไฟล์ออก (ดีฟอลต์ out/er_<d1>_<d2>.<นามสกุล>)")
    ap.add_argument("--fmt", default="png", choices=["png", "jpg", "pdf"],
                    help="ชนิดไฟล์หลัก (ดีฟอลต์ png)")
    ap.add_argument("--pdf", action="store_true", help="ออกไฟล์ PDF เพิ่มอีกไฟล์")
    a = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)
    stem = os.path.join(OUT_DIR, f"er_{a.d1}_{a.d2}"
                                 f"{'' if a.shift == 'all' else '_' + a.shift}")
    out = a.out or f"{stem}.{a.fmt}"

    qs = urllib.parse.urlencode({"d1": a.d1, "d2": a.d2, "shift": a.shift, "print": "1"})
    url = f"http://127.0.0.1:{a.port}/?{qs}"

    proc = ensure_server(a.port)
    try:
        urllib.request.urlopen(url, timeout=120).read()   # ให้ query ทำงานก่อนค่อย render
        render_to(a.fmt, url, out)
        print(f"{a.fmt.upper():4}: {out}  ({os.path.getsize(out) // 1024} KB)")
        if a.pdf and a.fmt != "pdf":
            pdf = stem + ".pdf"
            render_to("pdf", url, pdf)
            print(f"PDF : {pdf}  ({os.path.getsize(pdf) // 1024} KB)")
    finally:
        if proc:
            proc.terminate()


if __name__ == "__main__":
    main()
