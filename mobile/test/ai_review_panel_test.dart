import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_trade_advisor/core/trading/ai_review_panel.dart';

void main() {
  testWidgets('AI advisory stays visibly separate from order authority',
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
    ))));
    expect(find.textContaining('AI ADVISORY'), findsOneWidget);
    expect(find.textContaining('ไม่มีสิทธิ์เลือกทิศทาง'), findsOneWidget);
    expect(find.textContaining('SQZ bonus +0/10'), findsOneWidget);
    expect(find.textContaining('Move BE at 1R'), findsOneWidget);
  });
}
