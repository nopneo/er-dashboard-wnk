# ติดตั้ง ER Dashboard สำหรับโรงพยาบาลที่ใช้ HOSxP

แดชบอร์ดห้องฉุกเฉินแบบเว็บ (Flask) อ่านข้อมูลสดจากฐานข้อมูล HOSxP (MariaDB/MySQL) **อ่านอย่างเดียว**
ใช้เวลาติดตั้งราว 15 นาที

> คู่มือฉบับเต็มพร้อมภาพประกอบ: `ER_Dashboard_install_manual.pdf` (อยู่ใน zip และใน `docs/` ของ repo)

**ขั้นตอนโดยย่อ:** ติดตั้ง (ข้อ 2 หรือ 3) → ใส่ค่าฐานข้อมูลใน `.env` → **`python setup_check.py`**
→ เปิดหน้า **`/check`** จากเครื่องในโรงพยาบาล → ตั้งรหัส `ER_*` ตามที่หน้านั้นแจ้ง (ข้อ 6)
→ เทียบตัวเลขกับรายงาน ER เดิม 1–2 สัปดาห์ → `ER_DATA_AUDITED=1`

## 0. ก่อนนำไปใช้ — อ่านก่อนติดตั้ง

โปรแกรมนี้แจกฟรีจากโรงพยาบาลวังน้ำเขียว ใช้กับข้อมูลจริงของ รพ.วังน้ำเขียว
แต่ **HOSxP ของแต่ละโรงพยาบาลตั้งรหัสไม่เหมือนกัน** (ประเภทการมา `ovstist` เช่น EMS / รับส่งต่อ / นัด,
การจำหน่ายจาก ER `er_dch_type`, ประเภทผู้ป่วย ER `er_pt_type`, แผนก ER, คอลัมน์ "เริ่มตรวจ" ใน `service_time` …)
ถ้ารหัสไม่ตรง แดชบอร์ดยังแสดงตัวเลขได้ตามปกติ **แต่ตัวเลขจะเพี้ยนจากความจริงโดยไม่มีข้อความเตือน**
ผู้ที่นำไปใช้จึงต้องตรวจข้อมูลของตัวเองก่อนนำตัวเลขไปรายงานหรือตัดสินใจ

| # | ทำอะไร | เครื่องมือ |
|---|---|---|
| 1 | ตรวจสภาพแวดล้อม: Python ไลบรารี เขตเวลา Chrome การเชื่อมต่อฐานข้อมูล สิทธิ์อ่านอย่างเดียว ตารางที่ต้องใช้ ภาษาไทย visit ER ล่าสุด | `python setup_check.py` |
| 2 | Data Audit: รหัสที่แอปใช้ เทียบกับชื่อรหัสและจำนวนในข้อมูลจริง + ค่า `ER_*` ที่ควรตั้ง · ความครบของเวลาใน `er_regist` · แหล่งเวลาพบแพทย์ | หน้า **`/check`** (ข้อ 6) |
| 3 | ทดลองใช้คู่กับรายงาน ER เดิมของโรงพยาบาล **อย่างน้อย 1–2 สัปดาห์** — เทียบจำนวนผู้ป่วย ER ระดับความเร่งด่วน Admit / Refer / EMS | แดชบอร์ด + รายงานเดิม |
| 4 | ตรวจแล้วตัวเลขตรง → ตั้ง `ER_DATA_AUDITED=1` (ซ่อนแถบเตือน “ตัวเลขยังไม่ได้ตรวจกับข้อมูลของโรงพยาบาลนี้” บนแดชบอร์ด) | `.env` |

**เงื่อนไขการใช้:** สัญญาอนุญาต MIT ([LICENSE](LICENSE)) — ใช้ แก้ไข แจกต่อได้ทั้งหน่วยงานรัฐและเอกชน
โดยคงข้อความลิขสิทธิ์ไว้ · โปรแกรมให้ “ตามสภาพ” **ไม่มีการรับประกัน** ความถูกต้องของตัวเลขในโรงพยาบาลของท่าน
เป็นความรับผิดชอบของผู้นำไปใช้ (ข้อ 1–4 ด้านบน) · ส่วนประกอบของผู้อื่นดู [NOTICE.md](NOTICE.md)

**แจ้งปัญหา:** ก่อนแจ้ง ให้รัน `python setup_check.py` แล้วแก้ข้อ ✘ ก่อน — ปัญหาติดตั้งส่วนใหญ่มาจากสภาพแวดล้อมของเครื่อง
(เวอร์ชัน Python, firewall, สิทธิ์ฐานข้อมูล, เขตเวลา) · ถ้ายังไม่หาย หรือเป็นปัญหาของหน้าจอโปรแกรม แจ้งที่
[GitHub Issues](https://github.com/nopneo/er-dashboard-wnk/issues) พร้อมผลของ `setup_check.py` และเวอร์ชันที่ใช้
— **ห้ามแนบข้อมูลผู้ป่วย** (HN ชื่อ เลขบัตร) ชื่อบุคลากร หรือรหัสผ่าน

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
unzip er_dashboard-1.3.0.zip -d /opt && cd /opt/er_dashboard
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env                          # ใส่ DB_HOST / DB_USER / DB_PASS
.venv/bin/python setup_check.py                            # ตรวจความพร้อม (ข้อ 0) — แก้ข้อ ✘ ก่อน
.venv/bin/python app.py                                    # ทดลอง: http://127.0.0.1:6200/ แล้วเปิด /check
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
.venv\Scripts\python setup_check.py
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
| `ER_WALKIN_CODES` `ER_APPT_CODES` `ER_REFER_IN_CODES` `ER_EMS_ALS_CODES` `ER_EMS_FR_CODES` | `ovst.ovstist` ของ ลงทะเบียนเอง/Kiosk (`01,05`) · มาตามนัด (`02`) · รับส่งต่อ (`03`) · EMS ALS (`04`) · EMS FR (`06`) — หลายรหัสคั่นด้วย `,` |
| `ER_DCH_HOME` `ER_DCH_REFER` `ER_DCH_ADMIT` `ER_DCH_OBS` `ER_DCH_REFUSE` `ER_DCH_ESCAPE` | `er_regist.er_dch_type` ของ กลับบ้าน (`1`) · Refer (`2`) · Admit (`3`) · สังเกตอาการ (`5`) · ปฏิเสธการรักษา (`7`) · หนีกลับ (`8`) |
| `ER_SVC_PT_TYPES` / `ER_AUTOPSY_PT_TYPES` | `er_regist.er_pt_type` ของ รับบริการ ทำแผล ฉีดยา (`4`) / ชันสูตรศพ (`6`) |
| `ER_PDX_DIAGTYPE` | `ovstdiag.diagtype` ของวินิจฉัยหลัก (ค่าเริ่มต้น `1`) |
| `ER_DOCTOR_PROVIDER_TYPES` / `ER_DOCTOR_POSITION_IDS` | `doctor.provider_type_code` (`01`) / `position_id` (`1`) ที่นับเป็นแพทย์ ในรายงานพยาบาลตรวจแทนแพทย์ |
| `ER_DATA_AUDITED` | `1` = ตรวจกับข้อมูลของ รพ. แล้ว (ข้อ 0) — ซ่อนแถบเตือนบนแดชบอร์ด |
| `ER_LAN_NETS` | เครือข่ายเพิ่มเติมที่เปิดหน้า `/check` ได้ คั่นด้วย `,` (ค่าเริ่มต้นเปิดเฉพาะ IP ภายใน) — คำขอที่ผ่าน Cloudflare / cloudflared (มี `CF-Connecting-IP`) เปิดได้**เฉพาะ** IP ในค่านี้ เช่น IP สาธารณะของ รพ. |
| `ER_CHECK_OPEN` | `1` = เปิดหน้า `/check` ได้ทุกที่ (ไม่แนะนำ — หน้านั้นบอกโครงสร้างรหัสของ HOSxP) |
| `ER_BOARDING_MIN` | เข้า ER → Admit/Refer เกินกี่นาทีถือว่าค้างนาน (ค่าเริ่มต้น 120) |
| `ER_RED_MAX_MIN` | เกณฑ์บล็อก 17 ผู้ป่วยระดับ 1 อยู่ใน ER เกินกี่นาที (ค่าเริ่มต้น 60) |
| `ER_MCI_MIN`, `ER_MCI_WINDOW` | อุบัติเหตุหมู่: จำนวนรายขั้นต่ำ (5) ภายในกี่นาที (60) |
| `ER_MAX_DAYS` | ช่วงวันที่ยาวสุดที่ดึงได้ต่อครั้ง (ค่าเริ่มต้น 366) |
| `ER_NP_START` / `ER_NP_END` | ช่วงเวลาของรายงานพยาบาลตรวจแทนแพทย์ (ค่าเริ่มต้น 16:00 / 20:00) |
| `ER_NURSE_PROVIDER_TYPES` | `doctor.provider_type_code` ที่นับเป็นพยาบาล คั่นด้วยจุลภาค (ค่าเริ่มต้น 03) |
| `ER_NP_PASS_HASH` | hash (pbkdf2) ของรหัสเข้าดูรายงานพยาบาลตรวจแทนแพทย์ — ไม่ตั้ง = ปิดหน้ารายงาน · สร้างด้วย `python -c "from werkzeug.security import generate_password_hash as g; print(g(input(), method='pbkdf2:sha256'))"` แล้วใส่ใน `.env` ครอบด้วย `'...'` |
| `ER_NP_OPEN` | `1` = เปิดรายงานพยาบาลโดยไม่ใช้รหัส (เฉพาะเครื่องที่เข้าได้จาก LAN เท่านั้น) · มี `ER_NP_PASS_HASH` แล้วค่านี้ไม่มีผล |
| `ER_SECRET_KEY` | key สำหรับ cookie เข้าระบบ (สุ่มยาว ๆ เช่น `python -c "import secrets; print(secrets.token_hex(32))"`) — ไม่ตั้งจะสร้างจาก `ER_NP_PASS_HASH` |
| `ER_NP_LOGIN_HOURS` | อยู่ในระบบได้นานกี่ชั่วโมงหลังใส่รหัส (ค่าเริ่มต้น 12) |
| `ER_TRUSTED_PROXIES` | IP ของ reverse proxy ที่เชื่อ header `X-Forwarded-For` ได้ (ค่าเริ่มต้น `127.0.0.1,::1`) — ใช้นับรหัสผิดต่อ IP · ถ้า Apache อยู่คนละเครื่องให้ใส่ IP เครื่องนั้น |
| `ER_CF_RANGES` | ช่วง IP ของ Cloudflare คั่นด้วยจุลภาค (ค่าเริ่มต้นตาม cloudflare.com/ips) — เชื่อ `CF-Connecting-IP` เฉพาะคำขอที่มาจากช่วงนี้ |
| `ER_NP_MAX_DAYS` | ช่วงวันที่ยาวสุดของรายงานพยาบาลตรวจแทนแพทย์ (ค่าเริ่มต้น 1827 = 5 ปี) |
| `ER_CACHE_TTL` | อายุแคชข้อมูลเป็นวินาที (ค่าเริ่มต้น 120, 0 = ปิด) |
| `ER_MAX_BUILDS` | จำนวนช่วงวันที่ที่คำนวณใหม่พร้อมกันได้ต่อโปรเซส (ค่าเริ่มต้น 2) — กันฐานข้อมูล HOSxP ช้า |
| `ER_BUILD_WAIT` | วินาทีที่รอคิวคำนวณก่อนตอบ 503 "ลองใหม่" (ค่าเริ่มต้น 60) |
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

## 6. ปรับให้ตรงกับการบันทึกของโรงพยาบาล (หน้า `/check`)

แต่ละโรงพยาบาลตั้งค่า HOSxP ต่างกัน — เปิด **`http://<เซิร์ฟเวอร์>:6200/check`** จากเครื่องในโรงพยาบาล
(ค่าเริ่มต้นตรวจ 30 วันล่าสุด แสดงเฉพาะจำนวนนับ ไม่มีข้อมูลรายคน · เปิดจากภายนอกไม่ได้ ดู `ER_LAN_NETS`)

- **บนสุด** สรุปเรื่องที่ควรแก้ · **ตาราง 1** รหัสที่แอปใช้ เทียบกับชื่อรหัสและ % ของ visit ER ที่พบจริง
  พร้อมค่า `ER_*` ที่ควรตั้ง เช่น รหัส `07 EMS BLS` มีผู้ป่วยแต่แอปไม่นับเป็น EMS → เสนอ `ER_EMS_FR_CODES=06,07`
- **ตาราง 2–7** รหัสทุกตัวที่พบ (ประเภทการมา การจำหน่าย ประเภทผู้ป่วย ประเภทวินิจฉัย ระดับ แผนกแรก) ว่าแอปนับเป็นกลุ่มไหน
- **ตาราง 8–9** ความครบของเวลาใน `er_regist` / เวลา Admit / Refer และแหล่งเวลาพบแพทย์ + คอลัมน์ `service_time` ทั้งหมด
- ตั้งค่าใน `.env` แล้วรีสตาร์ตแอป เปิด `/check` อีกครั้งจนไม่มีข้อ “ไม่ตรง” · กด “ยืนยันว่าตรวจแล้ว” เพื่อซ่อนแถบเตือน
  เฉพาะเบราว์เซอร์นั้น หรือตั้ง `ER_DATA_AUDITED=1` ให้ทุกเครื่อง
- ผลอัตโนมัติจับได้แค่สิ่งที่ “ดูผิดปกติ” จากชื่อรหัส — ต้องเทียบกับรายงานเดิมด้วยเสมอ (ข้อ 0)

ถ้าไม่สะดวกเปิดหน้าเว็บ ตรวจเองด้วย SQL ได้ตามนี้:

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

- `python setup_check.py` ไม่มีข้อ ✘ และหน้า `/check` ไม่มีข้อ “ไม่ตรง”
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
docker exec <ชื่อ container> python setup_check.py      # ตรวจความพร้อม (ข้อ 0)
```

## 9. อัปเดตเป็นเวอร์ชันใหม่

แตก zip ใหม่ทับโฟลเดอร์เดิม (ไฟล์ `.env`, `static/img/hospital_logo.png`, `static/img/hero.jpg` ของคุณไม่อยู่ใน zip จึงไม่ถูกทับ)
แล้วรีสตาร์ตบริการ · ดูสิ่งที่เปลี่ยนใน [CHANGELOG.md](CHANGELOG.md)
· อัปเดตจาก 1.2.x เป็น 1.3.0: ค่าเดิมใช้ได้ทันที (รหัสเริ่มต้นเท่าเดิม) แต่จะมีแถบเตือนบนแดชบอร์ดจนกว่าจะตรวจหน้า `/check` และตั้ง `ER_DATA_AUDITED=1`
