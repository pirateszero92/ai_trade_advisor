import 'package:ai_trade_advisor/core/trading/strategy_gate_view.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('rejected bullish setup remains WAIT and cannot execute long', () {
    final gate = StrategyGateView.fromPayload({
      'confluence': 72,
      'bias': 'bullish',
      'strategy': {
        'approved': false,
        'direction': 'wait',
        'setup_direction': 'long',
        'rejection_reasons': [
          'Confluence 72 < minimum 75.0',
          'Liquidity sweep required but not detected',
        ],
        'effective_policy': {'min_confluence': 75},
      },
    });

    expect(gate.approved, isFalse);
    expect(gate.action, 'wait');
    expect(gate.setupLabel, 'BULLISH BIAS');
    expect(gate.allowsLong, isFalse);
    expect(gate.setupGradeLabel, '💎 SETUP A');
    expect(gate.gateScoreLabel, '72/75');
    expect(gate.waitReasonThai, contains('ขาด 3 คะแนน'));
    expect(gate.waitReasonThai, contains('Liquidity Sweep'));
  });

  test('approved short setup enables only short execution', () {
    final gate = StrategyGateView.fromPayload({
      'confluence': 81,
      'strategy': {
        'approved': true,
        'direction': 'short',
        'setup_direction': 'short',
        'rejection_reasons': <String>[],
        'effective_policy': {'min_confluence': 75},
      },
    });

    expect(gate.allowsLong, isFalse);
    expect(gate.allowsShort, isTrue);
    expect(gate.action, 'short');
    expect(gate.waitReasonThai, 'ผ่าน Strategy Gate');
  });

  test('legacy flattened signal remains supported', () {
    final gate = StrategyGateView.fromPayload({
      'strategy_approved': true,
      'direction': 'LONG',
      'setup_direction': 'LONG',
      'confluence': 70,
      'effective_policy': {'min_confluence': 65},
    });

    expect(gate.allowsLong, isTrue);
    expect(gate.allowsShort, isFalse);
  });

  test('Grade S setup recognized when confluence >= 85 or has liquidity sweep/squeeze fire', () {
    final gate1 = StrategyGateView.fromPayload({
      'confluence': 86,
      'strategy': {'approved': true, 'direction': 'long', 'setup_direction': 'long'},
    });
    expect(gate1.isGradeS, isTrue);
    expect(gate1.setupGradeLabel, '👑 SETUP S');

    final gate2 = StrategyGateView.fromPayload({
      'confluence': 78,
      'liquidity_swept': true,
      'strategy': {'approved': true, 'direction': 'long', 'setup_direction': 'long'},
    });
    expect(gate2.isGradeS, isTrue);
    expect(gate2.setupGradeLabel, '👑 SETUP S');

    final gate3 = StrategyGateView.fromPayload({
      'confluence': 76,
      'squeeze_status': 'squeeze_fire',
      'strategy': {'approved': true, 'direction': 'long', 'setup_direction': 'long'},
    });
    expect(gate3.isGradeS, isTrue);
    expect(gate3.setupGradeLabel, '👑 SETUP S');
  });
}
