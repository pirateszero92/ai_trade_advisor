class StrategyGateView {
  const StrategyGateView({
    required this.approved,
    required this.action,
    required this.setupDirection,
    required this.rejectionReasons,
    required this.confluence,
    required this.minConfluence,
    this.hasLiquiditySweep = false,
    this.hasSqueezeFire = false,
  });

  factory StrategyGateView.fromPayload(Map<String, dynamic>? payload) {
    final source = payload ?? const <String, dynamic>{};
    final strategyRaw = source['strategy'];
    final strategy = strategyRaw is Map
        ? Map<String, dynamic>.from(strategyRaw)
        : <String, dynamic>{};
    final approved = strategy['approved'] == true ||
        (strategy.isEmpty &&
            (source['actionable'] == true ||
                source['strategy_approved'] == true));

    String normalizeDirection(dynamic value) {
      final direction = value?.toString().trim().toLowerCase() ?? 'wait';
      return const {'long', 'short'}.contains(direction) ? direction : 'wait';
    }

    final rawAction =
        normalizeDirection(strategy['direction'] ?? source['direction']);
    final action = approved ? rawAction : 'wait';
    final setupDirection = normalizeDirection(strategy['setup_direction'] ??
        source['setup_direction'] ??
        (approved ? action : null));
    final reasonsRaw = strategy['rejection_reasons'] ??
        source['rejection_reasons'] ??
        const <dynamic>[];
    final reasons = reasonsRaw is List
        ? reasonsRaw
            .map((item) => item.toString().trim())
            .where((item) => item.isNotEmpty)
            .toList(growable: false)
        : const <String>[];
    final policyRaw = strategy['effective_policy'] ??
        source['effective_policy'] ??
        (source['market_regime'] is Map
            ? (source['market_regime'] as Map)['effective_policy'] ??
                (source['market_regime'] as Map)['policy']
            : null);
    final policy = policyRaw is Map
        ? Map<String, dynamic>.from(policyRaw)
        : const <String, dynamic>{};

    final evidence = source['evidence'] is List
        ? (source['evidence'] as List).map((e) => e.toString().toLowerCase()).toList()
        : <String>[];
    final triggerEvidence = source['trigger_evidence'] is List
        ? (source['trigger_evidence'] as List).map((e) => e.toString().toLowerCase()).toList()
        : <String>[];

    final hasSweep = source['liquidity_swept'] == true ||
        evidence.any((e) => e.contains('sweep')) ||
        triggerEvidence.any((e) => e.contains('sweep'));

    final hasSqueeze = source['squeeze_status'] == 'squeeze_fire' ||
        evidence.any((e) => e.contains('squeeze')) ||
        triggerEvidence.any((e) => e.contains('squeeze'));

    return StrategyGateView(
      approved: approved,
      action: action,
      setupDirection: setupDirection,
      rejectionReasons: reasons,
      confluence: ((source['confluence'] as num?)?.toInt() ?? 0).clamp(0, 100),
      minConfluence:
          ((policy['min_confluence'] as num?)?.toDouble() ?? 0).clamp(0, 100),
      hasLiquiditySweep: hasSweep,
      hasSqueezeFire: hasSqueeze,
    );
  }

  final bool approved;
  final String action;
  final String setupDirection;
  final List<String> rejectionReasons;
  final int confluence;
  final double minConfluence;
  final bool hasLiquiditySweep;
  final bool hasSqueezeFire;

  bool get allowsLong => approved && action == 'long';
  bool get allowsShort => approved && action == 'short';

  int get confluenceGap {
    final gap = minConfluence - confluence;
    return gap > 0 ? gap.ceil() : 0;
  }

  String get setupLabel {
    if (setupDirection == 'long') return 'BULLISH BIAS';
    if (setupDirection == 'short') return 'BEARISH BIAS';
    return 'NEUTRAL';
  }

  bool get isGradeS =>
      confluence >= 85 ||
      (confluence >= 75 && (hasLiquiditySweep || hasSqueezeFire));

  String get setupGradeLabel {
    if (isGradeS) return '👑 SETUP S';
    if (confluence >= 70) return '💎 SETUP A';
    if (confluence >= 55) return '⚖️ SETUP B';
    return '⏳ SETUP C';
  }

  String get gateScoreLabel {
    if (minConfluence <= 0) return '$confluence/100';
    return '$confluence/${minConfluence.round()}';
  }

  String get waitReasonThai {
    if (approved) return 'ผ่าน Strategy Gate';
    if (rejectionReasons.isEmpty) {
      return 'Strategy Gate ยังไม่อนุมัติ setup นี้';
    }
    return rejectionReasons.take(3).map(_translateReason).join(' • ');
  }

  static String _translateReason(String reason) {
    final confluence = RegExp(
            r'Confluence\s+([0-9.]+)\s*<\s*minimum\s+([0-9.]+)',
            caseSensitive: false)
        .firstMatch(reason);
    if (confluence != null) {
      final score = double.tryParse(confluence.group(1)!) ?? 0;
      final minimum = double.tryParse(confluence.group(2)!) ?? 0;
      final gap = (minimum - score).ceil().clamp(0, 100);
      return 'Confluence ${score.round()}/${minimum.round()} (ขาด $gap คะแนน)';
    }

    final lower = reason.toLowerCase();
    if (lower.contains('liquidity sweep')) {
      return 'ยังไม่พบ Liquidity Sweep ยืนยัน';
    }
    if (lower.contains('volume delta')) {
      return 'Volume Delta ยังไม่ยืนยันทิศทาง';
    }
    if (lower.contains('squeeze release')) {
      return 'ยังไม่เกิด Squeeze Release';
    }
    if (lower.contains('r:r')) return 'R:R ยังต่ำกว่าเกณฑ์';
    if (lower.contains('premium zone')) return 'ราคาอยู่ Premium Zone';
    if (lower.contains('discount zone')) return 'ราคาอยู่ Discount Zone';
    if (lower.contains('not aligned')) {
      return 'ทิศทางยังไม่สอดคล้องกับ Market Regime';
    }
    if (lower.contains('not ready') || lower.contains('data is not ready')) {
      return 'ข้อมูล Indicator/Regime ยังไม่พร้อม';
    }
    if (lower.contains('blocked')) return 'Market Regime ปิดรับคำสั่งใหม่';
    if (lower.contains('order block')) return 'ยังไม่พบ Order Block ตามทิศทาง';
    if (lower.contains('no trade direction')) return 'ยังไม่พบทิศทางเข้าเทรด';
    return reason;
  }
}
