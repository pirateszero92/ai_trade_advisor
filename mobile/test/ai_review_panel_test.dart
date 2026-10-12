import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_trade_advisor/core/trading/ai_review_panel.dart';

void main() {
  testWidgets('AI reviewer stays visibly separate from order authority',
      (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: AiReviewPanel(
          coreScore: 70,
          squeezeBonus: 0,
          review: {
            'mode': 'shadow',
            'status': 'reviewed',
            'verdict': 'COHERENT',
            'reason': 'support recovery',
            'management_note': 'Move BE at 1R'
          },
        ),
      ),
    ));
    expect(find.textContaining('AI MARKET REVIEW • ผลเปรียบเทียบ: COHERENT'),
        findsOneWidget);
    expect(find.textContaining('อ่านตลาดแบบ Blind'), findsOneWidget);
    expect(find.textContaining('ไม่มีสิทธิ์เปลี่ยน Entry'), findsOneWidget);
    expect(find.textContaining('สอดคล้องกับ Tri-Core'), findsOneWidget);
    expect(find.textContaining('Move BE at 1R'), findsOneWidget);
    expect(find.textContaining('SCALPER GATE'), findsNothing);
    expect(find.textContaining('Approved'), findsNothing);
  });

  testWidgets(
      'AI reviewer displays contradiction without claiming veto authority',
      (tester) async {
    await tester.pumpWidget(const MaterialApp(
      home: Scaffold(
        body: AiReviewPanel(
          coreScore: 70,
          squeezeBonus: 0,
          review: {
            'mode': 'shadow',
            'status': 'reviewed',
            'verdict': 'CONFLICT',
            'reason':
                'Chasing high into resistance with positive delta exhaustion',
          },
        ),
      ),
    ));
    expect(find.textContaining('AI MARKET REVIEW • ผลเปรียบเทียบ: CONFLICT'),
        findsOneWidget);
    expect(find.textContaining('พบ contradiction'), findsOneWidget);
    expect(find.textContaining('Vetoed'), findsNothing);
  });
}
