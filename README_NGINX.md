# AI Trade Advisor — Nginx Web Application Guide (v1.0)

คู่มือการ Build และ Deploy **AI Trade Advisor v1.0** บน **Nginx Web Application** สำหรับการทดสอบ (UAT), ตรวจจับข้อบกพร่อง (Bug Hunting) และปรับแต่งฟีเจอร์เพิ่มเติม

---

## 🏗️ สถาปัตยกรรมระบบ (Architecture)

```
                       ┌──────────────────────────────────────────────┐
                       │               USER BROWSER                   │
                       │           http://localhost:80                │
                       └──────────────────────┬───────────────────────┘
                                              │
                                              ▼
                       ┌──────────────────────────────────────────────┐
                       │               NGINX SERVER                   │
                       │   (Reverse Proxy, Compression & SPA Router)  │
                       └──────┬───────────────────────┬───────────────┘
                              │                       │
           Static Web Files   │                       │  /api/v1/  &  /ws
         (/usr/share/nginx/html)                      ▼
                              │            ┌──────────────────────────┐
                              ▼            │     FASTAPI BACKEND      │
                    ┌──────────────────┐   │       (Port 8000)        │
                    │   FLUTTER WEB    │   ├──────────────────────────┤
                    │  (SMC Frontend)  │   │  • SMC Engine (OB/FVG)   │
                    └──────────────────┘   │  • Binance Vision Feed   │
                                           │  • Apex AI Advisor       │
                                           │  • Paper Trading Engine  │
                                           └──────────┬───────────────┘
                                                      │
                                           ┌──────────┴───────────────┐
                                           │  PostgreSQL 16 & Redis 7 │
                                           └──────────────────────────┘
```

---

## 🚀 วิธีการรันด้วย Docker Compose (แนะนำ)

### 1. Build และรันอัตโนมัติด้วยคำสั่งเดียว:
เปิด PowerShell ในโฟลเดอร์โปรเจกต์:
```powershell
.\scripts\build_and_run_nginx.ps1
```
หรือรันคำสั่งมาตรฐาน:
```bash
# 1. Build Flutter Web
cd mobile
flutter build web
cd ..

# 2. Start all services with Docker Compose
docker compose up --build -d
```

### 2. เข้าใช้งาน:
* **Web Application**: [http://localhost](http://localhost) หรือ [http://localhost:3000](http://localhost:3000)
* **Backend API Docs (Swagger UI)**: [http://localhost:8000/docs](http://localhost:8000/docs)
* **Backend Health Check**: [http://localhost/health](http://localhost/health)

---

## 🛠️ รายละเอียดการตั้งค่า Nginx (`nginx/nginx.conf`)

* **Gzip Compression**: บีบอัดไฟล์ `.wasm`, `.js`, `.json`, `.css` ให้โหลดเร็วขึ้น 5-10 เท่า
* **SPA Routing**: รองรับ Routing ของ Flutter Web (`try_files $uri $uri/ /index.html;`) หมดปัญหา Refresh หน้าจอแล้วเจอ 404
* **Reverse Proxy**:
  * `/api/` $\rightarrow$ ส่งต่อไปยัง FastAPI Backend (`http://backend:8000/api/`)
  * `/health` $\rightarrow$ ส่งต่อไปยัง FastAPI Health Check
  * `/ws` $\rightarrow$ รองรับ Full-duplex WebSocket สำหรับ Live Feed และ Apex AI Chat Streaming
* **Cache Policy**: แคชไฟล์ static assets (Wasm, JS, Fonts, Images) นาน 7 วันเพื่อประสิทธิภาพสูงสุด
