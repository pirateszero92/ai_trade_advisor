import 'package:flutter/material.dart';

/// Reviewer-only AI panel. It never presents AI as trade/risk/execution authority.
class AiReviewPanel extends StatelessWidget {
  final Map<String, dynamic> review;
  final int coreScore;
  final int squeezeBonus;
  final int longEvidence;
  final int shortEvidence;
  const AiReviewPanel({
    super.key,
    required this.review,
    required this.coreScore,
    required this.squeezeBonus,
    this.longEvidence = 0,
    this.shortEvidence = 0,
  });

  @override
  Widget build(BuildContext context) {
    final status = review['status']?.toString() ?? 'not_requested';
    final verdict =
        review['verdict']?.toString().toUpperCase() ?? 'UNAVAILABLE';
    final comparisonVerdict =
        review['comparison_verdict']?.toString().toUpperCase();
    final displayedVerdict = comparisonVerdict ?? verdict;
    final reviewStage = review['review_stage']?.toString().toUpperCase() ?? '';
    final label = switch (status) {
      'pending' => switch (reviewStage) {
          'QUEUED' => 'อยู่ในคิว AI',
          'BLIND_READING' => 'กำลังอ่านกราฟแบบ Blind',
          'COMPARING' => 'กำลังเปรียบเทียบกับ Tri-Core',
          _ => 'กำลังเตรียม AI review',
        },
      'reviewed' => review['fast_tracked'] == true
          ? '👑 FAST-TRACK GRADE S (Audit ย้อนหลัง)'
          : 'ผลเปรียบเทียบ: $displayedVerdict',
      'discarded' => 'ผล review ถูก discard',
      'error' => 'review ไม่พร้อม',
      'queue_full' => 'คิว AI เต็ม',
      _ => 'ยังไม่เรียก AI',
    };
    final color = displayedVerdict == 'CONFLICT'
        ? Colors.redAccent
        : displayedVerdict == 'CAUTION' || verdict == 'CAUTION'
            ? Colors.orangeAccent
            : displayedVerdict == 'ALIGNED' || verdict == 'COHERENT'
                ? Colors.greenAccent
                : displayedVerdict == 'INSUFFICIENT_EVIDENCE'
                    ? Colors.orangeAccent
                    : Colors.lightBlueAccent;
    final independentRead = review['independent_market_read'];
    final marketThesis = independentRead is Map
        ? independentRead['market_thesis']?.toString()
        : null;

    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(9),
      decoration: BoxDecoration(
        color: const Color(0xFF162337),
        borderRadius: BorderRadius.circular(6),
      ),
      child: DefaultTextStyle(
        style:
            const TextStyle(fontSize: 11, color: Colors.white70, height: 1.4),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('AI MARKET REVIEW • $label',
              style: TextStyle(color: color, fontWeight: FontWeight.bold)),
          const Text(
            'อ่านตลาดแบบ Blind ก่อนเห็น Tri-Core • ไม่มีสิทธิ์เปลี่ยน Entry / SL / TP / Risk / Size',
            style: TextStyle(color: Colors.white38, fontSize: 10),
          ),
          if (marketThesis != null && marketThesis.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 5),
              child: Text('มุมมองตลาดอิสระ: $marketThesis'),
            ),
          if (displayedVerdict == 'CONFLICT')
            const Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text('พบ contradiction กับ deterministic candidate',
                  style: TextStyle(
                      color: Colors.redAccent, fontWeight: FontWeight.bold)),
            )
          else if (displayedVerdict == 'CAUTION' || verdict == 'CAUTION')
            const Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text('อนุมัติแบบระวัง — ลดขนาดความเสี่ยง 0.5x Risk',
                  style: TextStyle(
                      color: Colors.orangeAccent, fontWeight: FontWeight.bold)),
            )
          else if (displayedVerdict == 'INSUFFICIENT_EVIDENCE')
            const Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text('หลักฐานยังไม่พอสำหรับอนุญาตคำสั่ง — WAIT',
                  style: TextStyle(
                      color: Colors.orangeAccent, fontWeight: FontWeight.bold)),
            )
          else if (displayedVerdict == 'ALIGNED' || verdict == 'COHERENT')
            const Padding(
              padding: EdgeInsets.only(top: 4),
              child: Text('มุมมองตลาดอิสระสอดคล้องกับ Tri-Core',
                  style: TextStyle(
                      color: Colors.greenAccent, fontWeight: FontWeight.bold)),
            ),
          if (review['reason'] != null) Text('${review['reason']}'),
          if (review['conflicts'] is List &&
              (review['conflicts'] as List).isNotEmpty)
            Text('ข้อขัดแย้ง: ${(review['conflicts'] as List).join(' • ')}'),
          if ((review['management_note'] ?? '').toString().isNotEmpty)
            Text('Management context: ${review['management_note']}'),
          if (review['error'] != null) Text('${review['error']}'),
        ]),
      ),
    );
  }
}
