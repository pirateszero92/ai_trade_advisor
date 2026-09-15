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

  test('chart mode keeps a bounded set of nearest active zones', () {
    final zones = selectCleanSmcZones(
      [
        {'zone_id': 'far', 'top': 120.0, 'bottom': 119.0},
        {'zone_id': 'nearest', 'top': 102.0, 'bottom': 101.0},
        {'zone_id': 'second', 'top': 104.0, 'bottom': 103.0},
      ],
      null,
      101.5,
      maxZones: 2,
    );

    expect(zones, hasLength(2));
    expect(zones.map((zone) => zone['zone_id']), ['nearest', 'second']);
  });

  test('clean SMC zones reject malformed and duplicate geometry', () {
    final zones = selectCleanSmcZones(
      [
        {'zone_id': 'same', 'top': 102.0, 'bottom': 101.0},
        {'zone_id': 'same', 'top': 102.0, 'bottom': 101.0},
        {'zone_id': 'inverted', 'top': 100.0, 'bottom': 101.0},
      ],
      null,
      101.5,
      maxZones: 5,
    );

    expect(zones, hasLength(1));
    expect(zones.single['zone_id'], 'same');
  });
}
