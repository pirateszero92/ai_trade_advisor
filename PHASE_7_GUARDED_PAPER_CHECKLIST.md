# Phase 7 — Guarded Paper Release Checklist

เป้าหมายของ Phase 7 คือยืนยันการทำงานใน Paper Trading ก่อนพิจารณา Live

## ก่อนเริ่ม

- [ ] `TRADING_MODE=paper`
- [ ] AI policy เป็น `guarded`
- [ ] `execution_shadow_compare_enabled=true`
- [ ] Paper OMS พร้อมใช้งาน
- [ ] Market data เป็นข้อมูล realtime และไม่ stale
- [ ] ตรวจสอบว่าไม่มี active position ที่ไม่ทราบที่มา

## ทดสอบการตัดสินใจ

- [ ] setup ไม่ผ่าน ต้องไม่สร้าง order
- [ ] AI `CAUTION` เข้าได้ด้วย risk ที่ลดลง
- [ ] AI `CONFLICT` ต้องไม่เข้า
- [ ] AI `UNAVAILABLE` ต้องไม่เข้า
- [ ] systemic risk block ต้องแสดงที่ `SYSTEMIC_RISK`
- [ ] ทุก candidate มี `final_block_stage` และ `final_block_reason`

## ทดสอบการส่งคำสั่ง

- [ ] Adaptive market entry ส่งได้เฉพาะ setup ที่ผ่าน revalidation
- [ ] Limit order มี `valid_until`
- [ ] Limit order หมดอายุแล้วไม่ถูกเติมภายหลัง
- [ ] Order ทุกตัวมี SL และ TP
- [ ] ไม่เกิด duplicate position จาก snapshot/event เดิม
- [ ] Shadow comparison ถูกบันทึกทุกครั้ง

## ทดสอบความผิดพลาด

- [ ] ปิด Local LLM แล้วตรวจว่า provider เป็น `offline`
- [ ] ส่งข้อมูล quote stale แล้วต้องไม่ส่ง order
- [ ] restart backend แล้ว OMS ยัง recovery สถานะ Paper ได้
- [ ] ทดสอบ position เต็ม capacity
- [ ] ทดสอบ limit ไม่ fill ภายใน timeout

## เกณฑ์ผ่าน

- ไม่มี order ที่ไม่มี SL/TP
- ไม่มี direction mismatch
- ไม่มี duplicate order จาก decision เดิม
- ทุกการ block มีเหตุผลตรวจสอบย้อนหลังได้
- Paper run ต่อเนื่องอย่างน้อย 7 วัน หรือครบจำนวนสัญญาณที่กำหนด
- ยังไม่เปิด Live จนกว่าจะ review ผลลัพธ์และอนุมัติแยกต่างหาก

