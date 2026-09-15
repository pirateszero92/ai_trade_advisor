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
    expect(gate.directionBadgeLabel, 'LONG BIAS · NOT ENTRY');
    expect(gate.allowsLong, isFalse);
    expect(gate.setupGradeLabel, '⏳ NO ACTIVE SETUP');
    expect(gate.gateScoreLabel, '72/75');
    expect(gate.waitReasonThai, contains('ขาด 3 คะแนน'));
    expect(gate.waitReasonThai, contains('Liquidity Sweep'));
  });

  test('approved short setup enables only short execution', () {
    final gate = StrategyGateView.fromPayload({
      'confluence': 81,
      'tri_core_setup': {
        'actionable': true,
        'direction': 'short',
        'grade': 'A'
      },
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
    expect(gate.directionBadgeLabel, 'SHORT');
    expect(gate.waitReasonThai, 'ผ่าน Strategy Gate');
  });

  test('rejected short setup is labelled bias and never entry', () {
    final gate = StrategyGateView.fromPayload({
      'confluence': 82,
      'strategy': {
        'approved': false,
        'direction': 'wait',
        'setup_direction': 'short',
        'rejection_reasons': ['Scenario is observation-only'],
      },
    });

    expect(gate.approved, isFalse);
    expect(gate.action, 'wait');
    expect(gate.directionBadgeLabel, 'SHORT BIAS · NOT ENTRY');
    expect(gate.allowsShort, isFalse);
  });

  test('flattened actionable flag cannot bypass canonical tri-core', () {
    final gate = StrategyGateView.fromPayload({
      'strategy_approved': true,
      'direction': 'LONG',
      'setup_direction': 'LONG',
      'confluence': 70,
      'effective_policy': {'min_confluence': 65},
    });

    expect(gate.allowsLong, isFalse);
    expect(gate.allowsShort, isFalse);
  });

  test(
      'confirmed reaction rejected by entry geometry is not labelled awaiting confirmation',
      () {
    final gate = StrategyGateView.fromPayload({
      'confluence': 73,
      'scenario': {
        'entry_status': 'CONFIRMED_NO_ENTRY',
        'entry_block_reason': 'structural R:R 1.28 < 1.50; do not chase',
      },
      'strategy': {
        'approved': false,
        'direction': 'wait',
        'setup_direction': 'long',
        'rejection_reasons': [
          'Confirmed LONG reaction — no entry: structural R:R 1.28 < 1.50; do not chase',
        ],
      },
    });

    expect(gate.approved, isFalse);
    expect(gate.confirmedNoEntry, isTrue);
    expect(gate.directionBadgeLabel, 'LONG CONFIRMED · NO ENTRY');
    expect(gate.waitReasonThai, contains('สัญญาณยืนยันแล้ว'));
    expect(gate.waitReasonThai, contains('ห้ามไล่ราคา'));
    expect(gate.allowsLong, isFalse);
  });

  test('pressure warning is directional but never executable', () {
    final gate = StrategyGateView.fromPayload({
      'confluence': 66,
      'scenario': {
        'entry_status': 'PRESSURE_WARNING',
        'pressure_warning': {'side': 'bearish', 'score': 90},
      },
      'strategy': {
        'approved': false,
        'direction': 'wait',
        'setup_direction': 'short',
        'rejection_reasons': [
          'BEARISH pressure warning 90/100 — confirmation pending; no entry',
        ],
      },
    });

    expect(gate.pressureWarning, isTrue);
    expect(gate.directionBadgeLabel, 'BEARISH PRESSURE · NOT ENTRY');
    expect(gate.waitReasonThai, contains('คำเตือนล่วงหน้า'));
    expect(gate.allowsShort, isFalse);
  });

  test('Grade comes only from deterministic tri-core metadata', () {
    final gate1 = StrategyGateView.fromPayload({
      'confluence': 86,
      'tri_core_setup': {'actionable': true, 'direction': 'long', 'grade': 'S'},
      'strategy': {
        'approved': true,
        'direction': 'long',
        'setup_direction': 'long'
      },
    });
    expect(gate1.isGradeS, isTrue);
    expect(gate1.setupGradeLabel, '👑 SETUP S');

    final gate2 = StrategyGateView.fromPayload({
      'confluence': 78,
      'tri_core_setup': {'actionable': true, 'direction': 'long', 'grade': 'A'},
      'liquidity_swept': true,
      'sweep_direction': 'low',
      'strategy': {
        'approved': true,
        'direction': 'long',
        'setup_direction': 'long'
      },
    });
    expect(gate2.isGradeS, isFalse);
    expect(gate2.setupGradeLabel, '💎 SETUP A');

    final gate3 = StrategyGateView.fromPayload({
      'confluence': 76,
      'tri_core_setup': {'actionable': true, 'direction': 'long', 'grade': 'A'},
      'squeeze_status': 'squeeze_fire',
      'squeeze_momentum': 1.0,
      'strategy': {
        'approved': true,
        'direction': 'long',
        'setup_direction': 'long'
      },
    });
    expect(gate3.isGradeS, isFalse);
    expect(gate3.setupGradeLabel, '💎 SETUP A');

    final adverseSweep = StrategyGateView.fromPayload({
      'confluence': 78,
      'tri_core_setup': {'actionable': true, 'direction': 'long', 'grade': 'A'},
      'liquidity_swept': true,
      'sweep_direction': 'high',
      'strategy': {
        'approved': true,
        'direction': 'long',
        'setup_direction': 'long'
      },
    });
    expect(adverseSweep.isGradeS, isFalse);
  });
}
