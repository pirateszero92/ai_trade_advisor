import 'package:flutter/material.dart';

/// AI output is labelled separately from the executable Strategy/Risk gate.
class AiReviewPanel extends StatelessWidget {
  final Map<String, dynamic> review;
  final int coreScore;
  final int squeezeBonus;
  final int longEvidence;
  final int shortEvidence;
  const AiReviewPanel(
      {super.key,
      required this.review,
      required this.coreScore,
      required this.squeezeBonus,
      this.longEvidence = 0,
      this.shortEvidence = 0});

  @override
  Widget build(BuildContext context) {
    final status = review['status']?.toString() ?? 'not_requested';
    final verdict = review['verdict']?.toString() ?? 'UNAVAILABLE';
    final label = switch (status) {
      'pending' => 'กำลังรอ / วิเคราะห์ snapshot',
      'reviewed' => 'ตรวจแล้ว: $verdict',
      'error' => 'วิเคราะห์ไม่สำเร็จ',
      'queue_full' => 'คิวเต็ม',
      _ => 'ยังไม่เรียก AI',
    };
    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(9),
      decoration: BoxDecoration(
          color: const Color(0xFF162337),
          borderRadius: BorderRadius.circular(6)),
      child: DefaultTextStyle(
        style:
            const TextStyle(fontSize: 11, color: Colors.white70, height: 1.4),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(
              'SMC+CVD evidence L$longEvidence/S$shortEvidence • selected $coreScore/100 • SQZ bonus +$squeezeBonus/10',
              style: const TextStyle(color: Colors.cyanAccent)),
          Text('AI ADVISORY & SCALPER GATE • $label',
              style: TextStyle(
                  color: verdict == 'CONFLICT'
                      ? Colors.redAccent
                      : (verdict == 'COHERENT'
                          ? Colors.greenAccent
                          : Colors.lightBlueAccent),
                  fontWeight: FontWeight.bold)),
          if (verdict == 'CONFLICT')
            Container(
              margin: const EdgeInsets.symmetric(vertical: 4),
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: Colors.red.withOpacity(0.2),
                borderRadius: BorderRadius.circular(4),
                border: Border.all(color: Colors.redAccent),
              ),
              child: const Text(
                '⛔ AI SCALPER GATE: คำสั่งถูกยับยั้งโดย AI Day Trader (Vetoed)',
                style: TextStyle(
                    color: Colors.redAccent, fontWeight: FontWeight.bold),
              ),
            )
          else if (verdict == 'COHERENT')
            Container(
              margin: const EdgeInsets.symmetric(vertical: 4),
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: Colors.green.withOpacity(0.2),
                borderRadius: BorderRadius.circular(4),
                border: Border.all(color: Colors.greenAccent),
              ),
              child: const Text(
                '⚡ AI SCALPER GATE: ได้รับการอนุมัติโดย AI Day Trader (Approved)',
                style: TextStyle(
                    color: Colors.greenAccent, fontWeight: FontWeight.bold),
              ),
            )
          else
            const Text(
              'AI SCALPER GATE: ตรวจสอบความถูกต้องของออเดอร์ก่อนอนุมัติเข้าเทรดสไตล์ Day Trade / Scalp',
              style: TextStyle(color: Colors.amberAccent),
            ),
          if (review['reason'] != null) Text('${review['reason']}'),
          if (review['conflicts'] is List &&
              (review['conflicts'] as List).isNotEmpty)
            Text('ข้อขัดแย้ง: ${(review['conflicts'] as List).join(' • ')}'),
          if ((review['management_note'] ?? '').toString().isNotEmpty)
            Text('การจัดการสถานะ: ${review['management_note']}'),
          if (review['error'] != null) Text('${review['error']}'),
        ]),
      ),
    );
  }
}
