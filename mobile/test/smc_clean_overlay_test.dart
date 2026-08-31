import 'package:ai_trade_advisor/features/chart/smc_interactive_chart.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('clean SMC zones keep only nearest active zone', () {
    final zones = selectCleanSmcZones(
      [
        {'top': 120.0, 'bottom': 119.0, 'mitigated': false},
        {'top': 102.0, 'bottom': 101.0, 'mitigated': false},
        {'top': 100.5, 'bottom': 99.5, 'mitigated': true},
      ],
      null,
      101.5,
    );

    expect(zones, hasLength(1));
    expect(zones.single['top'], 102.0);
  });

  test('clean SMC zones use active fallback when list is empty', () {
    final zones = selectCleanSmcZones(
      const [],
      {'top': 101.0, 'bottom': 100.0, 'mitigated': false},
      100.5,
    );

    expect(zones, hasLength(1));
    expect(zones.single['bottom'], 100.0);
  });
}
