# 📖 คู่มือการใช้งานระบบ AI Trade Advisor (User Manual)
### ระบบที่ปรึกษาการเทรดอัจฉริยะด้วยแนวคิดสถาบัน Smart Money Concepts (SMC) & Apex AI

---

## 📑 สารบัญ (Table of Contents)
1. [ภาพรวมของระบบและปรัชญาการเทรด (System Overview & Philosophy)](#1-ภาพรวมของระบบและปรัชญาการเทรด)
2. [คู่มืออินดิเคเตอร์และโครงสร้างราคา SMC ที่ระบบใช้ (Indicators & SMC Suite)](#2-คู่มืออินดิเคเตอร์และโครงสร้างราคา-smc-ที่ระบบใช้)
3. [คู่มือการใช้งาน 5 หน้าจอหลักบน Mobile App](#3-คู่มือการใช้งาน-5-หน้าจอหลักบน-mobile-app)
   - [หน้า 1: หน้ากราฟและการวางแผนเทรด (Chart Screen & AI Blueprint)](#หน้า-1-หน้ากราฟและการวางแผนเทรด-chart-screen--ai-blueprint)
   - [หน้า 2: ระบบสแกนหาจุดเข้าเทรดเชิงรุก (Proactive Signals Screen)](#หน้า-2-ระบบสแกนหาจุดเข้าเทรดเชิงรุก-proactive-signals-screen)
   - [หน้า 3: สมุดบันทึกการเทรดและคะแนนวินัย (Trade Journal & Discipline Scorecard)](#หน้า-3-สมุดบันทึกการเทรดและคะแนนวินัย-trade-journal--discipline-scorecard)
   - [หน้า 4: ผู้ช่วยวิเคราะห์สถาบัน Apex AI (Apex AI Chat Advisor)](#หน้า-4-ผู้ช่วยวิเคราะห์สถาบัน-apex-ai-apex-ai-chat-advisor)
   - [หน้า 5: หน้าตั้งค่าระบบและความปลอดภัย (Settings Screen)](#หน้า-5-หน้าตั้งค่าระบบและความปลอดภัย-settings-screen)
4. [ระบบบริหารความเสี่ยงและการคำนวณขนาดไม้ (Risk Management Suite)](#4-ระบบบริหารความเสี่ยงและการคำนวณขนาดไม้-risk-management-suite)
5. [ระบบเสียงสรุปตลาดเช้าและการสตรีมราคาความเร็วสูง (Voice Briefing & WebSocket)](#5-ระบบเสียงสรุปตลาดเช้าและการสตรีมราคาความเร็วสูง-voice-briefing--websocket)
6. [กฎเหล็ก 7 ข้อสำหรับการเทรดสถาบัน (Institutional Trading Rules)](#6-กฎเหล็ก-7-ข้อสำหรับการเทรดสถาบัน-institutional-trading-rules)

---

## 1. ภาพรวมของระบบและปรัชญาการเทรด

**AI Trade Advisor (Apex AI)** ออกแบบมาเพื่อแก้ไขปัญหาที่เทรดเดอร์รายย่อยมักจะพ่ายแพ้ในตลาด เช่น **การ Overtrade**, **การเข้าเทรดโดยไร้วินัย**, **การใช้อารมณ์ตัดสินใจ**, และ**การไม่คำนวณระยะ Stop Loss ที่ถูกต้อง**

### ปรัชญาหลักของระบบ:
1. **ตามรอยสถาบัน (Trade with Smart Money)**: ไม่ไล่ราคาที่จุดสูงสุดหรือต่ำสุด แต่รอเข้าที่บริเวณ **Order Block** และ **FVG Imbalance** ในโซนลดราคา (**Discount Zone**)
2. **คุมความเสี่ยงตายตัว (Fixed Risk % Execution)**: กำหนดความเสี่ยงล่วงหน้า 0.5% – 3.0% ของพอร์ตเสมอ ระบบจะคำนวณขนาดไม้ (Position Size) ให้อัตโนมัติตามระยะ Stop Loss
3. **ป้องกันเงินทุนเชิงรุก (Capital Preservation First)**: ใช้ระบบ **Auto-Breakeven** ดึง Stop Loss มาบังทุนทันทีเมื่อราคาแตะ 1.5R เพื่อไม่ให้ไม้ที่ชนะกลายมาเป็นขาดทุน
4. **ประเมินวินัยอย่างต่อเนื่อง (Continuous Cognitive Feedback)**: ทุกไม้ที่ปิดลง AI จะเขียนบทวิเคราะห์ภาษาไทย ให้คะแนนดาว 1–5 ⭐ และคำนวณ **Discipline Score (0–100)** เพื่อสร้างนิสัยการเทรดที่ยั่งยืน

---

## 2. คู่มืออินดิเคเตอร์และโครงสร้างราคา SMC ที่ระบบใช้

ระบบผสานรวมการตรวจจับโครงสร้างราคาเชิงปริมาณ (Quantitative SMC Engine) ระดับสูง ได้แก่:

### 1. 🧱 Institutional Order Block (OB)
* **Bullish Order Block (กล่องสีฟ้า/เขียว 🟢)**: แท่งเทียนสีแดงแท่งสุดท้ายก่อนที่ราคาจะพุ่งขึ้นอย่างรุนแรง แสดงถึงร่องรอยการเข้าซื้อของสถาบัน เป็นแนวรับสำคัญ (Demand Zone)
* **Bearish Order Block (กล่องสีส้ม/แดง 🔴)**: แท่งเทียนสีเขียวแท่งสุดท้ายก่อนที่ราคาจะทุบตัวลงรุนแรง เป็นแนวต้านสำคัญ (Supply Zone)
* **การ Mitigation**: หากราคาปิดทะลุกล่อง Order Block ระบบจะถือว่าโซนนั้นถูกลบล้าง (Mitigated) และตัดออกจากกราฟทันที

### 2. ⚡ Fair Value Gap (FVG) / Imbalance (กล่องสีม่วง 🟣)
* **ความหมาย**: ช่องว่างของราคาที่เกิดจากการซื้อหรือขายข้างเดียวอย่างรวดเร็ว (3-Candle Structure) ทำให้ราคาวิ่งผ่านไปโดยไม่มีสภาพคล่องฝั่งตรงข้าม
* **การใช้งาน**: ราคาจะโน้มเอียงกลับมา "เติมเต็ม" (Mitigate) โซน FVG เสมอ ถือเป็นจุดสไนเปอร์ชั้นดีเมื่ออยู่ในทิศทางเดียวกับแนวโน้มหลัก

### 3. 📈 Break of Structure (BOS) vs Change of Character (CHoCH)
* **BOS (Break of Structure)**: ราคาทำจุดสูงสุดใหม่ (Higher High) ในขาขึ้น หรือทำจุดต่ำสุดใหม่ (Lower Low) ในขาลง บ่งบอกว่า**แนวโน้มเดิมยังแข็งแกร่ง**
* **CHoCH (Change of Character)**: ราคาเบรกโครงสร้างฝั่งตรงข้ามเป็นครั้งแรก บ่งบอกถึง**สัญญาณเตือนการกลับตัวของแนวโน้ม**

### 4. ⚖️ Premium / Discount Equilibrium (เส้นประสีเหลือง EQ 50%)
* **Equilibrium (50%)**: เส้นกึ่งกลางของกรอบราคาสวิงสูงสุด-ต่ำสุด
* **Discount Zone (< 50%)**: โซนราคาถูก — **อนุญาตให้เปิดออเดอร์ BUY / LONG เท่านั้น**
* **Premium Zone (> 50%)**: โซนราคาแพง — **อนุญาตให้เปิดออเดอร์ SELL / SHORT เท่านั้น**

### 5. 🎯 Liquidity Sweep & Equal Highs/Lows (EQH / EQL)
* **Liquidity Sweep**: การที่ไส้เทียนพุ่งทะลุจุดสูงสุดหรือต่ำสุดเดิมเพื่อกิน Stop Loss ของรายย่อย แล้วราคาดึงกลับทันที แสดงถึงการกวาดสภาพคล่องของสถาบัน
* **EQH / EQL**: จุดที่ราคามียอดเท่ากัน เป็นเป้าหมายสภาพคล่องที่สถาบันชอบลากราคาไปเคลียร์

### 6. 🐳 Cumulative Volume Delta (CVD) & Volume Absorption
* **CVD Absorption**: เมื่อราคากดลงทำ New Low แต่เส้น Volume Delta ยกตัวขึ้น แสดงว่าสถาบันกำลังตั้ง Limit Buy ออเดอร์ดูดซับแรงขายจนหมด เตรียมตัวเกิดการกลับตัวรุนแรง

---

## 3. คู่มือการใช้งาน 5 หน้าจอหลักบน Mobile App

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Chart    2. Signals    3. Journal    4. Apex AI  5. Set  │
└─────────────────────────────────────────────────────────────┘
```

---

### หน้า 1: หน้ากราฟและการวางแผนเทรด (Chart Screen & AI Blueprint)
หน้าหลักสำหรับการวิเคราะห์กราฟแท่งเทียนสดพร้อมเลเยอร์ SMC

1. **แถบสถานะด้านบน (Top Bar)**:
   * **สวิตช์เลือกตลาด**: สลับระหว่าง `CRYPTO` (Binance, Bybit, InnovestX THB), `FOREX & GOLD` (MT5, Yahoo), `STOCKS` (Alpaca, Apple, Tesla, NVDA)
   * **ปุ่มเลือกเหรียญ/คู่เงิน**: แตะเพื่อเปิดแผ่นเลือกคู่เงินพร้อมแสดงราคา Real-time
   * **สถานะการเชื่อมต่อ `• ⚡ WS (15ms)`**: แสดงว่าแอปกำลังรับราคาสดผ่าน Full-Duplex WebSocket
   * **ปุ่ม `🎙️ Briefing`**: แตะเพื่อฟังเสียงบรรยายสรุปสภาวะตลาดเช้า และดูจุด SMC สำคัญประจำวัน
2. **แถบ Multi-Timeframe Alignment Matrix (MTF Bar)**:
   * แสดงแนวโน้ม 4 Timeframe พร้อมกัน: `1D` | `4H` | `1H` | `15M`
   * ป้ายเกรดสถาบัน: `🌟 SUPREME A+` (4/4 TF), `💎 GRADE A` (3/4 TF), `⚖️ GRADE B`, `⏳ WAIT`
   * แตะที่แถบเพื่อเปิด **MTF Breakdown Sheet** ดูรายละเอียด FVG, OB, Volume Delta ของแต่ละ TF
3. **กราฟแท่งเทียนเชิงโต้ตอบ (Interactive Candlestick Chart)**:
   * แสดงกล่อง Order Block (เขียว/แดง), โซน FVG (ม่วง), เส้นประ EQ 50% และป้าย Liquidity Sweep
   * สามารถซูม ย่อ-ขยาย เลื่อนกราฟ และเปิด/ปิดเลเยอร์ SMC ได้ด้วยปุ่ม `LuxAlgo SMC`
4. **AI Blueprint Execution Suite**:
   * กล่องคำนวณจุดเข้า Entry, Stop Loss, Take Profit 1 (2.0R), และ Take Profit 2 (Runner)
   * **แถบเลือก Risk % (0.5%, 1.0%, 2.0%, 3.0%)**: คำนวณ Lot/Quantity ให้ทันที ป้องกันการโอเวอร์เทรด
   * สวิตช์ **`Auto-BE (1.5R)`** และ **`Trailing Stop`** สำหรับล็อกกำไรอัตโนมัติ

---

### หน้า 2: ระบบสแกนหาจุดเข้าเทรดเชิงรุก (Proactive Signals Screen)
ศูนย์รวมสัญญาณ SMC Confluence ที่ผ่านการคัดกรองจาก AI Background Scanner

1. **ตัวกรองและโหมดพอร์ต**:
   * สลับระหว่าง `🧪 พอร์ตจำลอง Paper ($)` และ `🔒 บัญชีจริง Live`
   * ฟิลเตอร์กรองตามหมวด: `ALL`, `CRYPTO`, `FOREX & GOLD`, `STOCKS`
2. **การ์ดสัญญาณ (Signal Card)**:
   * ป้ายบอกทิศทาง `🟢 BUY / LONG` หรือ `🔴 SELL / SHORT`
   * ป้ายเกรดความน่าจะเป็น: `🌟 SUPREME A+`, `💎 GRADE A`, `⚖️ GRADE B`
   * ป้ายสภาพคล่อง: `🐳 CVD Absorption`, `🧹 Liquidity Swept`
   * แถบ Confluence Score (0–100) และคำแนะนำเชิงกลยุทธ์จาก AI
3. **การส่งคำสั่งใน 1 คลิก (1-Click Execution)**:
   * แตะที่การ์ดสัญญาณเพื่อเปิดหน้าต่าง **Order Confirmation Modal**
   * เลือกประเภทคำสั่ง: `Limit Order ที่แนว OB/FVG` หรือ `Market Order ทันที`
   * ปรับแก้ความเสี่ยงหรือกดยืนยันเพื่อเปิดออเดอร์ทันที

---

### หน้า 3: สมุดบันทึกการเทรดและคะแนนวินัย (Trade Journal & Discipline Scorecard)
ศูนย์รวมประวัติการเทรด บันทึก PnL สด และคะแนนวินัยการเทรดสถาบัน

1. **3 แท็บการทำงาน**:
   * **⚡ Open Positions**: แสดงสถานะที่กำลังถืออยู่พร้อมสตรีม Mark Price และ Unrealized PnL สดทุก 300ms
   * **⏳ Pending Orders**: แสดงคำสั่ง Limit Order ที่กำลังตั้งรอดักราคาที่แนวรับแนวต้าน
   * **📜 History**: ประวัติไม้ที่ปิดแล้วทั้งหมด พร้อมการคำนวณ Realized PnL
2. **Institutional Discipline Scorecard (0–100)**:
   * **Discipline Score (0–100)**: วัดระดับวินัยและความเคร่งครัดต่อระบบ
   * **ป้าย Tier**: `🛡️ Strict Institutional Compliance` (≥85) | `⚖️ Moderate Discipline` (70–84) | `⚠️ Rule Leakage Warning` (<70)
   * **Mini Stats**: Plan Followed %, Win Rate %, Realized Avg R:R, Average Star Rating ⭐
   * **Top Performing Edge**: สรุปรูปแบบ Setup ที่ทำกำไรได้สูงสุด (เช่น *Order Block Retest*, *Liquidity Sweep CHoCH*)
3. **AI Post-Trade Audit Modal Sheet**:
   * แตะที่การ์ดประวัติการเทรด เพื่อเปิดแผ่นวิเคราะห์ผลการเทรดโดย AI
   * แสดงคะแนนดาว `⭐⭐⭐⭐⭐`, ผลกำไรขาดทุน, และแท็กความสำเร็จ (เช่น `🎯 TP Smashed`, `🛡️ Auto-BE Shielded`, `🚀 Trailing Locked`)
   * กล่อง **Institutional AI Critique** และ **💡 ข้อคิดและบทเรียน (Key Lesson Learned)**
   * ปุ่ม **`🔄 Re-Audit Trade with AI`** สำหรับเรียก AI วิเคราะห์ทบทวนซ้ำ

---

### หน้า 4: ผู้ช่วยวิเคราะห์สถาบัน Apex AI (Apex AI Chat Advisor)
ห้องสนทนากับ AI ควอนต์ผู้เชี่ยวชาญด้าน SMC และโครงสร้างตลาด

1. **Isolated Per-Symbol Memory**: AI จะจดจำบริบทและแผนการเทรดแยกตามแต่ละเหรียญ/คู่เงิน
2. **Context-Injected Intelligence**: ทุกคำถามที่ส่งไป ระบบจะแนบข้อมูลราคาปัจจุบัน, สวิงไฮ/โลว์, โซน Order Block และสถานะพอร์ตให้ AI วิเคราะห์ประกอบแบบ Real-time
3. **ปุ่ม Quick Prompts**: เช่น `📊 สรุปโครงสร้าง SMC`, `🎯 วางเป้า Take Profit`, `🛡️ จุด Stop Loss ที่ปลอดภัย`

---

### หน้า 5: หน้าตั้งค่าระบบและความปลอดภัย (Settings Screen)
1. **การเชื่อมต่อ Backend (API Connection)**: ปรับแต่ง Base URL (เช่น `http://10.0.2.2:8000` สำหรับ Emulator หรือ Domain Production)
2. **AI Provider Fallback Chain**:
   * **Local LLM**: ต่อเชื่อมกับ LM Studio / Ollama (`http://localhost:1234/v1`)
   * **Google Gemini**: ใส่ API Key และเลือกรุ่น `gemini-2.0-flash`
   * **OpenRouter**: ใส่ API Key สำหรับใช้งาน Claude 3.5 Sonnet หรือ DeepSeek V3
3. **การตั้งค่าความเสี่ยงบัญชี (Risk Settings)**:
   * กำหนด Max Risk ต่อไม้ (เช่น 1.0%), Max Daily Loss (3.0%), Max Open Positions
4. **การเชื่อมต่อโบรเกอร์และ Exchange**:
   * รองรับ **InnovestX (SCBX Digital Asset Open API)** มีระบบความปลอดภัย HMAC-SHA256
   * รองรับ **Binance**, **Bybit**, **MetaTrader 5 (MT5)**, และ **Alpaca Markets**

---

## 4. ระบบบริหารความเสี่ยงและการคำนวณขนาดไม้ (Risk Management Suite)

### สูตรคำนวณขนาด Position Size อัตโนมัติ:
$$\text{Position Size} = \frac{\text{ยอดเงินในพอร์ต (Account Capital)} \times \text{Risk \% (เช่น 1\%)}}{\left|\text{ราคาเข้า (Entry)} - \text{ราคาตัดขาดทุน (Stop Loss)}\right|}$$

* **ตัวอย่าง**: พอร์ต \$100,000 เลือกความเสี่ยง 1% (\$1,000)
  * เข้าซื้อ BTC ที่ \$80,000 วาง SL ที่ \$79,000 (ระยะห่าง \$1,000)
  * ขนาดไม้ที่ระบบคำนวณ = $\frac{\$1,000}{\$1,000} = 1.0000\text{ BTC}$
  * หากราคาชน SL จะขาดทุนไม่เกิน \$1,000 (1%) พอดี 100%

### ระบบบันไดกำไร 3 ระดับ (Automated Trade Management Ladder):
1. **Level 1 (เมื่อกำไรแตะ 1.5R)**: ย้าย Stop Loss มาที่จุดเข้าทันที (**Auto-Breakeven**) ป้องกันการขาดทุน
2. **Level 2 (เมื่อแตะเป้า TP1 / 2.0R)**: ปิดทำกำไร 50% ของขนาด Position เพื่อล็อกเงินสดเข้ากระเป๋า
3. **Level 3 (Runner 50% ที่เหลือ)**: เปิดใช้งาน **Dynamic Trailing Stop** เลื่อนตามสวิงโครงสร้างราคา SMC เพื่อรันเทรนด์ยาว

---

## 5. ระบบเสียงสรุปตลาดเช้าและการสตรีมราคาความเร็วสูง

### Full-Duplex WebSocket Push Hub (`/ws/stream`):
* ระบบสตรีมข้อมูลผ่าน WebSocket แทนการ Polling ส่งผลให้:
  * **Latency ลดเหลือเพียง 15–30ms** (จากเดิม 500ms+)
  * ประหยัดการใช้งานอินเทอร์เน็ตและแบตเตอรี่มือถือ
  * ได้รับการแจ้งเตือนสัญญาณและราคาแบบเสี้ยววินาที

### Proactive AI Morning Voice Briefing:
* แตะที่ไอคอน `🎙️ Briefing` บนหน้า Chart เพื่อเปิดรับฟังการวิเคราะห์ประจำวัน:
  * วิเคราะห์พฤติกรรมเจ้ามือและการสะสมสภาพคล่อง (Accumulation / Manipulation)
  * ตรวจสอบโซน Order Block และ FVG ประจำวัน
  * สรุป 3 อันดับคู่เงิน/เหรียญที่มี Confluence สูงสุดประจำวัน

---

## 6. กฎเหล็ก 7 ข้อสำหรับการเทรดสถาบัน (Institutional Trading Rules)

1. 🚫 **ห้ามเปิด Buy ใน Premium Zone และห้ามเปิด Sell ใน Discount Zone** โดยเด็ดขาด
2. 🚫 **ห้ามเข้าเทรดหากไม่มีจุด Stop Loss ที่อ้างอิงกับโครงสร้างสวิง (Swing High/Low)**
3. 🚫 **ห้ามเสี่ยงเกิน 2% – 3% ต่อหนึ่งออเดอร์**
4. 🛡️ **เปิดใช้งาน Auto-Breakeven เสมอเมื่อราคาเริ่มวิ่งไปในทิศทางที่ถูกต้อง**
5. ⭐ **ให้ความสำคัญกับสัญญาณเกรด `🌟 SUPREME A+` และ `💎 GRADE A` เป็นอันดับแรก**
6. 🧘 **หากตลาดผันผวนผิดปกติหรือมีสัญญาณเกรด `⏳ WAIT` ให้อยู่เฉยๆ และถือเงินสด 100%**
7. 📜 **ทบทวนบทเรียนจาก AI Post-Trade Review ทุกครั้งหลังปิดออเดอร์เพื่อรักษาวินัยให้อยู่ในระดับ Institutional Compliance (≥85 คะแนน)**
