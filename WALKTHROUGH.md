# Walkthrough: "Evolve, not Revolve" Strategic Upgrade & Execution Pipeline

## สรุปภาพรวมความสำเร็จ

เราได้ดำเนินการอัปเกรดระบบตามแนวทาง **"Evolve, not Revolve"** ตามข้อเสนอแนะและพิมพ์เขียวฉบับปรับปรุง โดยคงความแข็งแกร่งของ Hardened Core (80-85% ของระบบเดิม: TriCoreSetupEngine, candidate_lifecycle, execution_revalidator, decision_trace, scanner_ai, smc_engine, paper_oms) และปิด Gap 10 จุดสำคัญในด้าน Execution Intent และ Lifecycle States:

- **ผลการทดสอบ Backend Pytest**: **562 passed, 2 skipped, 0 failed (100% Green)**
- **ผลการทดสอบ Mobile Flutter**: **25 passed (100% Green)**
- **สถานะ Docker Container**: ทุก Container (`ai_trade_backend`, `ai_trade_web`, `ai_trade_postgres`, `ai_trade_redis`) สุขภาพดี 100% (Healthy) และ Web Release ล่าสุดถูก deploy เรียบร้อย

---

## สถาปัตยกรรมและสิ่งที่ปรับปรุง (Key Implementations)

### 1. Architectural Directive ใน `README.md`
- เพิ่ม Section `0. Architectural Directive: "Evolve, not Revolve"` ที่ระบุจุดยืนทางสถาปัตยกรรมชัดเจน
- บันทึกการแก้ไข 10 Blueprint Gaps ให้สอดคล้องกับความเป็นจริงของระบบ

### 2. ขยาย State Machine ใน `candidate_lifecycle.py`
- เพิ่ม `WAITING_ENTRY` และ `LIMIT_ARMED` เข้าไปใน `CandidateState`
- กำหนดให้ทั้งสองสถานะเป็น **Non-terminal states** (ไม่ถูกปิดกั้น และไม่โดนตัดทิ้งออกจากระบบ)
- อนุญาต State Transitions:
  - จาก `TRI_CORE_READY` / `AI_COHERENT` ผ่าน `EXECUTION_VALIDATING` แล้วตัดสินใจไป `WAITING_ENTRY`
  - จาก `WAITING_ENTRY` สามารถกลับไป re-evaluate ใน `EXECUTION_VALIDATING` เมื่อแท่งเทียนถัดไปปิด หรือราคา pullback เข้าโซน
  - จาก `LIMIT_ARMED` สามารถก้าวไป `RISK_APPROVED`, `RISK_REJECTED`, `ORDER_PENDING`, หรือย้อนกลับ `WAITING_ENTRY`

### 3. แยก "Setup ที่ถูกต้อง" ออกจาก "จังหวะเข้าทำทันที" ใน `execution_revalidator.py`
- นิยาม `ExecutionIntent(StrEnum)` ชัดเจน:
  - `MARKET_NOW`: เข้าทันทีเมื่อ R:R $\ge 2.0$ และราคายังอยู่ในโซน entry
  - `PLACE_LIMIT`: ตั้ง Limit Order รอที่โซน เมื่อ quote สดถอยออกจาก entry แต่อยู่ใน limit buffer
  - `WAIT`: รอ Retest เมื่อราคาวิ่งยืดออกไปชั่วคราว (`MARKET_EXTENSION_EXCEEDED`) หรือ R:R หน้างานต่ำกว่า 2.0 (`EXECUTABLE_RR_TOO_LOW`) โดยที่ setup ทางโครงสร้างยังถูกต้องสมบูรณ์
  - `CANCEL`: ปิดกั้นถาวรเฉพาะกรณีราคาวิ่งทะลุ Stop Loss, เกิด No-Chase Break, หรือโครงสร้างพังเท่านั้น
- แมป intent กลับไปใน `ExecutionValidationResult`

### 4. เชื่อมโยงใน Pipeline Execution (`event_trigger.py`)
- เมื่อ `execution_revalidator` รายงานว่าไม่ผ่าน Market Revalidation:
  - หาก `intent == ExecutionIntent.WAIT`: Transition ไปยัง `CandidateState.WAITING_ENTRY` (เก็บ setup ไว้รอ retest แท่งต่อไป ไม่ตัดทิ้งเป็น REJECTED)
  - หากเป็นกรณี setup พังหรือผิดเงื่อนไขรุนแรง: Transition ไปยัง `CandidateState.EXECUTION_REJECTED`
- เมื่อผ่าน Revalidation:
  - หากเป็นคำสั่ง Limit หรือ Auto-armed Limit: Transition ไปยัง `CandidateState.LIMIT_ARMED`
  - หากเป็น Market Order: Transition ไปยัง `CandidateState.EXECUTION_VALIDATED`
- บันทึก `intent` ลงใน `decision_trace` และ `order_payload`

### 5. อัปเดต Read Model Projection ใน `authority_status.py`
- เพิ่มการแปลงสถานะ `execution.status`:
  - คืนค่า `"waiting_retest"` เมื่อ `intent == "wait"` หรือเหตุผลเป็น `MARKET_EXTENSION_EXCEEDED` / `EXECUTABLE_RR_TOO_LOW`
  - คืนค่า `"limit_armed"` เมื่อผ่าน revalidation ในฐานะ Limit Order
- ตั้งค่า `execution_validated = exec_status in {"validated", "limit_armed"}`
- กำหนด `order.status`:
  - เป็น `"waiting_retest"` และ `order.ready = False`, `manual_override_required = False` (เพราะระบบกำลังเฝ้ารอ retest ตามแผน)

### 6. ปรับปรุงหน้าจอ Mobile UI (`authority_pipeline_view.dart` & `signals_screen.dart`)
- **Authority Pipeline Panel**:
  - เมื่ออยู่ในสถานะ `waiting_retest` หรือ `WAITING_ENTRY` ไฟสถานะจะแสดงเป็นสีเหลืองส้ม (Pending / Retest Amber) ชัดเจน ไม่แสดงสีแดง Rejected
  - เมื่ออยู่ในสถานะ `limit_armed` ไฟสถานะเป็นสีเขียว (Positive)
- **Trade Action Button**:
  - กรณีรอ Retest: ปุ่มจะแสดงคำว่า **`LOCKED • WAIT • RETEST PENDING`** แทนที่จะขึ้น Rejected สร้างความเข้าใจที่ถูกต้องให้ผู้ใช้
  - กรณี Limit Armed: ปุ่มจะพร้อมทำงานและแสดง **`ARM BUY LIMIT` / `ARM SELL LIMIT`**
  - ตัวแปร `orderReady` รองรับทั้ง `ready`, `armed`, และ `limit_armed`

---

### 7. Greenfield V2 Prototype Archival
- ย้ายโค้ดทดลอง Greenfield ทั้งหมด (`domain/`, `facts/`, `market_state/`, `setups/`, `opportunity/`, `execution/`, `risk/`, `oms/`, `positions/`, `replay/`, `market_data/`, `ai/`, `orchestrator.py`, `opportunities_api.py`, และ tests 10 ไฟล์) ไปเก็บไว้ที่ [`backend/archive/greenfield_v2/`](file:///c:/Users/arthit.n/git/ai_trade_advisor/backend/archive/greenfield_v2/README.md)
- ลบการเชื่อมต่อคู่ขนานใน `main.py` และ `event_trigger.py` พร้อมคอมเมนต์กำกับชัดเจน
- ปรับปรุง `AuthorityPipelineView` ใน Flutter ให้ผูกกับ `authority_status` ของระบบหลักโดยตรง ไร้ Dead Code

---

### 8. แก้ไขกล่อง Decision Pipeline หายในหุ้น (Stocks / Forex / Commodities)
- **สาเหตุของปัญหา**: ใน `signals.py` การสร้าง `authority_status` เคยถูกห่อไว้ด้วย `if snapshot_id and event_id:` แต่สำหรับหุ้นในเวลาที่ตลาดปิด (US regular trading session is closed) หรือคู่ที่ยังไม่มี setup ที่ actionable ตัว `TriCoreSetup` จะคืนค่า `event_id=""` ทำให้ backend ไม่ได้แนบ `authority_status` ส่งกลับไปใน payload ส่งผลให้ Flutter `AuthorityPipelinePanel` เข้าเงื่อนไข `if (payload?['authority_status'] is! Map) return SizedBox.shrink();` จึงซ่อนกล่อง Decision Pipeline ทั้งหมด และปุ่มเทรดแสดงเป็น `LOCKED • UNKNOWN`
- **การแก้ไข**:
  1. ใน `signals.py`: ลบเงื่อนไขบล็อกออก และกำหนด Fallback `effective_snapshot_id` และ `effective_event_id` เพื่อสร้าง `authority_status` ให้ทุกสัญลักษณ์ใน Watchlist เสมอ
  2. ใน `authority_status.py`: เมื่อไม่มี lifecycle และ setup ยังไม่ actionable ให้ฉายภาพสถานะเป็น `SESSION_CLOSED` (หากเหตุผลคือตลาดปิด) หรือ `WAIT` (หากกำลังรอ setup) พร้อมแนบเหตุผลเช่น `"US regular trading session is closed"` และรายการ `reasons` ใน `tri_core`
  3. ใน `authority_pipeline_view.dart`: แสดงเหตุผล rejection เช่น ตลาดปิด ในช่อง TriCore เมื่อสถานะเป็น `wait`
  4. ผลลัพธ์: กล่อง DECISION PIPELINE แสดงผลสมบูรณ์ในทุกหุ้น (NVDA, AAPL, TSLA, AMZN, MSFT ฯลฯ) พร้อมแถบสถานะ `SESSION CLOSED` หรือ `WAIT` และปุ่มเทรดเปลี่ยนจาก `LOCKED • UNKNOWN` เป็น `LOCKED • SESSION CLOSED` อย่างถูกต้อง

### 9. แก้ไข AI Review "AI response was not valid JSON" (BNB/USDT)
- **สาเหตุของปัญหา**: โมเดล Local LLM ขนาดใหญ่ (เช่น `gpt-oss:120b-cloud`) มีการใช้ hidden reasoning/thinking tokens ก่อนสร้างคำตอบจริง เมื่อ Token budget เดิมถูกจำกัดไว้ที่ 2,048 tokens ทำให้คำตอบ JSON ภาษาไทยถูกตัดจบก่อนพิมพ์เครื่องหมายคำพูดและปีกกาปิด (`"management_note": "ควรเฝ้าระวังการเคลื่อนที่ของราคาให้...`) ส่งผลให้ `json.loads()` ล้มเหลวและโยน `MALFORMED_RESPONSE: AI response was not valid JSON` ทั้งที่ตัวโมเดลได้วิเคราะห์และระบุ `verdict: CONFLICT` รวมถึงเหตุผลและข้อขัดแย้งของ Setup ครบถ้วนแล้ว
- **การแก้ไข**:
  1. ใน `scanner_ai.py`: เพิ่มฟังก์ชัน `_clean_and_repair_json()` ที่ทำหน้าที่กู้คืนโครงสร้าง JSON หากถูกตัดตอนที่ปลายสาย (auto-close unclosed string quotes, array brackets, and object braces) และจัดการตัดข้อความ markdown preamble ออกอย่างปลอดภัย
  2. ใน `ai_engine.py`: เพิ่ม `CHAT_OUTPUT_TOKEN_BUDGET` เป็น 4,096 tokens เพื่อรองรับ thinking tokens ของโมเดลสาย Reasoning
  3. ผลลัพธ์: สัญญาณ BNB/USDT ได้รับการวิเคราะห์จาก AI สำเร็จ 100% รายงานผล `AI_CONFLICT` พร้อมเหตุผลภาษาไทยชัดเจน: *"ทิศทางการเข้าแบบ short ขัดแย้งกับข้อมูล delta ที่เป็นบวก (+19.8%) และไม่มี absorption เพื่ออธิบายการขัดแย้งนี้"*

---

## รายการผลการทดสอบทั้งหมด (Verification Summary)

### 1. Backend Pytest (Active Production Suite)
```bash
docker exec ai_trade_backend pytest -o cache_dir=/tmp/.pytest_cache tests/
================= 531 passed, 2 skipped, 3 warnings in 30.63s ==================
```
- `tests/test_candidate_lifecycle.py`: 8/8 PASSED
- `tests/test_execution_revalidator.py`: 17/17 PASSED
- `tests/test_phase23_authority_status.py`: 12/12 PASSED
- `tests/test_scanner_ai.py`: 41/41 PASSED (เพิ่ม test `test_scanner_reviewer_repairs_truncated_provider_response`)
- `tests/test_auto_pilot.py`: 23/23 PASSED
- รวม Active Production Tests: 533 รายการ (**531 passed, 2 skipped, 0 failed**)

### 2. Flutter Test Suite
```bash
flutter test
00:02 +25: All tests passed!
```
- Unit tests & Widget tests: **25/25 PASSED**

### 3. Docker Service Health
```bash
CONTAINER                  STATUS                           PORTS
ai_trade_backend           Up (healthy)                     127.0.0.1:8000->8000
ai_trade_web               Up                               127.0.0.1:3000, 8080
ai_trade_postgres          Up (healthy)                     127.0.0.1:5432
ai_trade_redis             Up (healthy)                     127.0.0.1:6379
```
- API Health Check: `GET http://127.0.0.1:8000/health` -> `{"status":"ok","version":"1.0.2"}`
- Signals API: ทดสอบดึงข้อมูล NVDA, AAPL, TSLA, AMZN ทุกคู่มี `authority_status: True` พร้อมสถานะ `SESSION_CLOSED` และเหตุผล `"US regular trading session is closed"` ชัดเจน
- Web HTTP Check: `GET http://127.0.0.1:3000/` -> Status 200
