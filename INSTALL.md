# ติดตั้ง ER Dashboard สำหรับโรงพยาบาลที่ใช้ HOSxP

แดชบอร์ดห้องฉุกเฉินแบบเว็บ (Flask) อ่านข้อมูลสดจากฐานข้อมูล HOSxP (MariaDB/MySQL) **อ่านอย่างเดียว**
ใช้เวลาติดตั้งราว 15 นาที

> คู่มือฉบับเต็มพร้อมภาพประกอบ: `ER_Dashboard_install_manual.pdf` (อยู่ใน zip และใน `docs/` ของ repo)

## 1. สิ่งที่ต้องมี

- เครื่องที่ต่อถึงฐานข้อมูล HOSxP ได้ (แนะนำให้ต่อ **Slave/Replica** ไม่ใช่ฐานหลัก)
- Python 3.9 ขึ้นไป · Chrome/Edge/Chromium ถ้าจะใช้ปุ่มส่งออก JPG/PNG/PDF
- บัญชีฐานข้อมูลที่ **SELECT อย่างเดียว** (แดชบอร์ดไม่เขียนข้อมูล และตั้ง session เป็น READ ONLY ให้อีกชั้น)
- ห้องฉุกเฉินใช้ระบบ ER ของ HOSxP (`er_regist` มี `enter_er_time`)

สร้างบัญชีอ่านอย่างเดียว (ตัวอย่าง):

```sql
CREATE USER 'er_ro'@'%' IDENTIFIED BY '<รหัสผ่านยาว ๆ>';
GRANT SELECT ON hos.* TO 'er_ro'@'%';      -- จำกัด host ให้แคบที่สุดเท่าที่ทำได้
```

## 2. ติดตั้งบน Linux

```bash
unzip er_dashboard-1.0.0.zip -d /opt && cd /opt/er_dashboard
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env                          # ใส่ DB_HOST / DB_USER / DB_PASS
.venv/bin/python app.py                                    # ทดลอง: http://127.0.0.1:6200/
```

รันเป็นบริการ (systemd) ด้วยผู้ใช้ธรรมดา ไม่ใช้ root — `/etc/systemd/system/er-dashboard.service`:

```ini
[Unit]
Description=ER Dashboard
After=network.target

[Service]
User=erdash
Group=erdash
WorkingDirectory=/opt/er_dashboard
ExecStart=/opt/er_dashboard/.venv/bin/gunicorn --bind 0.0.0.0:6200 --workers 3 --timeout 120 \
  --access-logfile - --access-logformat '%(t)s %(m)s %(U)s %(s)s %(L)ss' app:app
Restart=always
# chromium (ปุ่มส่งออก) ต้องมี HOME ที่เขียนได้
Environment=HOME=/var/lib/erdash
StateDirectory=erdash
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

```bash
useradd -r -s /sbin/nologin erdash
chown -R root:root /opt/er_dashboard                       # แอปแก้โค้ดตัวเองไม่ได้
chown root:erdash /opt/er_dashboard/.env && chmod 640 /opt/er_dashboard/.env
systemctl daemon-reload && systemctl enable --now er-dashboard
```

## 3. ติดตั้งบน Windows

```powershell
python -m venv .venv ; .venv\Scripts\pip install -r requirements.txt
copy .env.example .env ; notepad .env
.venv\Scripts\waitress-serve --listen=0.0.0.0:6200 app:app
```

## 4. ตั้งค่า (.env)

| ค่า | ความหมาย |
|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASS` | ฐานข้อมูล HOSxP (บัญชีอ่านอย่างเดียว) |
| `PORT` | พอร์ตของแอป (ค่าเริ่มต้น 6200) |
| `ER_HOSPITAL_NAME`, `ER_HOSPITAL_NAME_EN` | ชื่อโรงพยาบาล (ถ้าไม่ใส่อ่านจาก `opdconfig`) / ชื่อภาษาอังกฤษใต้ชื่อ |
| `ER_CONTACT`, `ER_DEV_CONTACT` | ช่องทางติดต่อที่แสดงท้ายหน้า |
| `ER_DEPCODE` | รหัสแผนก ER ใน `kskdepartment` (ค่าเริ่มต้น `011`) ใช้นับผู้ป่วยที่ส่งมาจากแผนกอื่น |
| `ER_NOT_DEPCODES` | รหัสที่ไม่ใช่แผนกจริง คั่นด้วย `,` (ค่าเริ่มต้น `999` = กลับบ้าน) |
| `ER_ST_DOCTOR_COL` | คอลัมน์ `service_time` ที่ ER กด "เริ่มตรวจ" (ค่าเริ่มต้น `service5`) — ดูหัวข้อ 6 |
| `ER_BOARDING_MIN` | เข้า ER → Admit/Refer เกินกี่นาทีถือว่าค้างนาน (ค่าเริ่มต้น 120) |
| `ER_RED_MAX_MIN` | เกณฑ์บล็อก 17 ผู้ป่วยระดับ 1 อยู่ใน ER เกินกี่นาที (ค่าเริ่มต้น 60) |
| `ER_MCI_MIN`, `ER_MCI_WINDOW` | อุบัติเหตุหมู่: จำนวนรายขั้นต่ำ (5) ภายในกี่นาที (60) |
| `ER_MAX_DAYS` | ช่วงวันที่ยาวสุดที่ดึงได้ต่อครั้ง (ค่าเริ่มต้น 366) |
| `ER_CACHE_TTL` | อายุแคชข้อมูลเป็นวินาที (ค่าเริ่มต้น 120, 0 = ปิด) |
| `ER_DB_TIMEOUT` | เวลาสูงสุดต่อคำสั่ง SQL เป็นวินาที (ค่าเริ่มต้น 60) |
| `ER_EXPORT` | `0` = ปิดปุ่มส่งออกทั้งหมด (เครื่องที่ไม่มี chromium) |
| `ER_EXPORT_WAIT` | ส่งออก: รอคิวได้นานสุดกี่วินาที (ค่าเริ่มต้น 0 = ไม่ว่างตอบ 503 ทันที) |
| `ER_EXPORT_CACHE_TTL` | เก็บไฟล์ที่ส่งออกแล้วให้ผู้ขอช่วงเดียวกัน (วินาที, ค่าเริ่มต้น 300) |
| `ER_CHROME_NO_SANDBOX` | `1` = ปิด sandbox ของ chromium (ค่าเริ่มต้นปิดเฉพาะเมื่อรันด้วย root / บน Windows) |
| `ER_HOST` | ที่อยู่ที่ฟัง เมื่อรัน `python app.py` (ค่าเริ่มต้น 0.0.0.0) |

**โลโก้และภาพพื้นหลัง** — วาง PNG สี่เหลี่ยมจัตุรัสที่ `static/img/hospital_logo.png` (ไม่มี = ใช้โลโก้กลาง)
และภาพแนวนอนที่ `static/img/hero.jpg` เป็นพื้นแถบหัว (ไม่มี = พื้นไล่สีแดง) แล้วรีสตาร์ตแอป

## 5. เปิดผ่าน reverse proxy (ทางเลือก)

วางใต้ sub-path เช่น `https://<โดเมน รพ.>/erdash/` (Apache) — ใส่ **ก่อน** บรรทัด catch-all `ProxyPass /` ถ้ามี:

```apache
ProxyPass /erdash/ http://127.0.0.1:6200/
ProxyPassReverse /erdash/ http://127.0.0.1:6200/
<Location /erdash/>
  RequestHeader set X-Forwarded-Prefix /erdash
  RequestHeader set X-Forwarded-Proto https
</Location>
```

แอปอ่าน `X-Forwarded-Prefix` / `X-Forwarded-Proto` สร้างลิงก์ให้ถูกเอง · แก้ค่าตั้งของ Apache ทุกครั้ง: สำรองไฟล์ →
`httpd -t` → ผ่านค่อย `systemctl reload httpd` · ถ้าอยู่หลัง Cloudflare ให้เปลี่ยน `VERSION` ใน `app.py`
ทุกครั้งที่แก้ CSS (ใช้เป็น `?v=` กันแคช)

**ถ้าเปิดออกอินเทอร์เน็ต อ่าน [SECURITY.md](SECURITY.md) ก่อน**

## 6. ปรับให้ตรงกับการบันทึกของโรงพยาบาล

แต่ละโรงพยาบาลตั้งค่า HOSxP ต่างกัน ตรวจ 3 เรื่องนี้หลังติดตั้ง:

1. **คอลัมน์ "เริ่มตรวจ" ใน `service_time`** — หาว่า ER กดเวลาลงคอลัมน์ไหน:
   ```sql
   SELECT service4_dep, service5_dep, service11_dep, service12_dep, COUNT(*)
   FROM service_time s JOIN er_regist e ON e.vn = s.vn
   WHERE e.vstdate >= CURDATE() - INTERVAL 7 DAY GROUP BY 1,2,3,4 ORDER BY 5 DESC LIMIT 5;
   ```
   คอลัมน์ที่ `_dep` เป็นรหัสแผนก ER และเวลาอยู่หลังเวลาเข้า ER คือคอลัมน์ที่ใช้ → ตั้ง `ER_ST_DOCTOR_COL`
2. **รหัสแผนก ER** — `SELECT depcode, department FROM kskdepartment` แล้วตั้ง `ER_DEPCODE`
3. **ระดับความเร่งด่วน** — ใช้ `er_regist.er_emergency_level_id` (1-5) ถ้าว่างใช้ `er_emergency_type.export_code`
   บล็อก 11 แสดง % ที่ยังไม่ลงระดับ

**เวรแบ่งตามเวลาเข้า ER** 00:00–08:00 ดึก · 08:01–16:00 เช้า · 16:01–23:59 บ่าย

## 7. ตรวจสอบหลังติดตั้ง

- `GET /healthz` ตอบ `{"status":"ok"}`
- เปิดหน้าแรกแล้วจำนวนผู้มา ER ตรงกับรายงาน ER ของ HOSxP ในช่วงเดียวกัน
- บล็อก 11 "มีเวลาพบแพทย์" ต่ำผิดปกติ → ตรวจ `ER_ST_DOCTOR_COL` (หัวข้อ 6)
- ปุ่มส่งออกไม่ทำงาน: ตรวจว่ามี Chrome/Edge/Chromium (หรือตั้ง `ER_EXPORT=0`) และดู log ของบริการ
  (`journalctl -u er-dashboard -f`)
- เลือกช่วง 1 ปีจะโหลดราว 30 วินาที (ค้นตาราง Lab/X-ray/ยาเพิ่ม) — ช่วงรายเดือนราว 3 วินาที

## 8. Docker

```bash
docker build -t er-dashboard .                          # ไม่มีปุ่มส่งออก (image เล็ก)
docker build --target export -t er-dashboard-export .   # มี chromium
docker run -p 8080:8080 --env-file .env er-dashboard
```

## 9. อัปเดตเป็นเวอร์ชันใหม่

แตก zip ใหม่ทับโฟลเดอร์เดิม (ไฟล์ `.env`, `static/img/hospital_logo.png`, `static/img/hero.jpg` ของคุณไม่อยู่ใน zip จึงไม่ถูกทับ)
แล้วรีสตาร์ตบริการ · ดูสิ่งที่เปลี่ยนใน [CHANGELOG.md](CHANGELOG.md)
