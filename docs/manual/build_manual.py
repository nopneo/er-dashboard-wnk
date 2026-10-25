"""สร้างคู่มือติดตั้ง PDF จาก manual.html → docs/ER_Dashboard_install_manual.pdf

    python docs/manual/build_manual.py

ใช้ chromium ตัวเดียวกับปุ่มส่งออก (render.find_browser) · ใส่เลขเวอร์ชันจาก app.py ให้เอง
"""

import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

import render  # noqa: E402


def main():
    version = re.search(r'VERSION = "([^"]+)"', open(os.path.join(ROOT, "app.py"), encoding="utf-8").read())[1]
    html = open(os.path.join(HERE, "manual.html"), encoding="utf-8").read().replace("{{VERSION}}", version)
    src = os.path.join(HERE, "_manual_build.html")          # อยู่ข้าง manual.html เพื่อให้ path รูป/ฟอนต์ถูก
    open(src, "w", encoding="utf-8").write(html)
    out = os.path.join(ROOT, "docs", "ER_Dashboard_install_manual.pdf")
    try:
        browser = render.find_browser()
        with tempfile.TemporaryDirectory() as profile:
            subprocess.run(render._base_args(browser, profile) + [
                "--no-pdf-header-footer", "--virtual-time-budget=8000",
                f"--print-to-pdf={out}", "file:///" + src.replace("\\", "/"),
            ], check=True, timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        os.remove(src)
    print(out, os.path.getsize(out), "bytes", "version", version)


if __name__ == "__main__":
    main()
