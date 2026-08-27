import 'package:flutter_test/flutter_test.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter/widgets.dart';
import 'package:ai_trade_advisor/main.dart';

void main() {
  testWidgets('Smoke test App loads', (WidgetTester tester) async {
    await tester.pumpWidget(const ProviderScope(child: AITradeAdvisorApp()));
    expect(find.byType(AITradeAdvisorApp), findsOneWidget);

    // Dispose explicitly and let Dio cancellation callbacks drain.
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.pumpAndSettle();
  });
}
