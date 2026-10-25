"""สร้างชุดติดตั้งสำหรับโรงพยาบาลอื่น → dist/er_dashboard-<version>.zip

ใส่เฉพาะไฟล์กลาง ไม่มี .env / โลโก้และภาพพื้นหลังของ รพ.ผู้พัฒนา
"""

import os
import re
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["app.py", "cache.py", "db.py", "queries.py", "render.py",
         "requirements.txt", ".env.example", "README.md", "INSTALL.md", "SECURITY.md", "CHANGELOG.md",
         "Dockerfile", ".dockerignore", "LICENSE", "NOTICE.md"]
DIRS = ["templates", "static"]
#: คู่มือติดตั้ง PDF — สร้างด้วย python docs/manual/build_manual.py
MANUAL = os.path.join("docs", "ER_Dashboard_install_manual.pdf")
SKIP = {os.path.join("static", "img", "hospital_logo.png"), os.path.join("static", "img", "hero.jpg")}


def main():
    version = re.search(r'VERSION = "([^"]+)"', open(os.path.join(HERE, "app.py"), encoding="utf-8").read())[1]
    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    out = os.path.join(HERE, "dist", f"er_dashboard-{version}.zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in FILES:
            z.write(os.path.join(HERE, f), f"er_dashboard/{f}")
        for d in DIRS:
            for root, _, names in os.walk(os.path.join(HERE, d)):
                for n in sorted(names):
                    full = os.path.join(root, n)
                    rel = os.path.relpath(full, HERE)
                    if rel in SKIP or "__pycache__" in rel:
                        continue
                    z.write(full, "er_dashboard/" + rel.replace(os.sep, "/"))
        if os.path.exists(os.path.join(HERE, MANUAL)):
            z.write(os.path.join(HERE, MANUAL), "er_dashboard/" + os.path.basename(MANUAL))
        else:
            print("ไม่มี", MANUAL, "— ชุดติดตั้งนี้ไม่มีคู่มือ PDF")
    print(out, os.path.getsize(out), "bytes")
    return out


if __name__ == "__main__":
    main()
