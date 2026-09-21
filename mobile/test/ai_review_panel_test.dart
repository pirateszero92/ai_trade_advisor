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
    expect(find.textContaining('AI ADVISORY & SCALPER GATE'), findsOneWidget);
    expect(find.textContaining('AI SCALPER GATE: ได้รับการอนุมัติโดย AI Day Trader (Approved)'), findsOneWidget);
    expect(find.textContaining('SQZ bonus +0/10'), findsOneWidget);
    expect(find.textContaining('Move BE at 1R'), findsOneWidget);
  });

  testWidgets('AI Scalper Gate displays veto message when CONFLICT',
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
        'reason': 'Chasing high into resistance with positive delta exhaustion',
      },
    ))));
    expect(find.textContaining('AI ADVISORY & SCALPER GATE • ตรวจแล้ว: CONFLICT'), findsOneWidget);
    expect(find.textContaining('AI SCALPER GATE: คำสั่งถูกยับยั้งโดย AI Day Trader (Vetoed)'), findsOneWidget);
  });
}
