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
    this.entryStatus = '',
    this.entryBlockReason = '',
    this.coreSetupGrade = 'WAIT',
    this.longEvidence = 0,
    this.shortEvidence = 0,
  });

  factory StrategyGateView.fromPayload(Map<String, dynamic>? payload) {
    final source = payload ?? const <String, dynamic>{};
    final strategyRaw = source['strategy'];
    final strategy = strategyRaw is Map
        ? Map<String, dynamic>.from(strategyRaw)
        : <String, dynamic>{};
    final triCoreRaw = source['tri_core_setup'];
    final triCore = triCoreRaw is Map
        ? Map<String, dynamic>.from(triCoreRaw)
        : const <String, dynamic>{};
    final approved = strategy['approved'] == true ||
        (strategy.isEmpty && triCore['actionable'] == true);

    String normalizeDirection(dynamic value) {
      final direction = value?.toString().trim().toLowerCase() ?? 'wait';
      return const {'long', 'short'}.contains(direction) ? direction : 'wait';
    }

    final rawAction = normalizeDirection(
        strategy['direction'] ?? triCore['direction'] ?? source['direction']);
    final action = approved ? rawAction : 'wait';
    final setupDirection = normalizeDirection(strategy['setup_direction'] ??
        source['setup_direction'] ??
        triCore['direction'] ??
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
    final scenarioRaw = source['scenario'];
    final scenario = scenarioRaw is Map
        ? Map<String, dynamic>.from(scenarioRaw)
        : const <String, dynamic>{};
    final reactionRaw = source['reaction'];
    final reaction = reactionRaw is Map
        ? Map<String, dynamic>.from(reactionRaw)
        : const <String, dynamic>{};
    final entryStatus = (scenario['entry_status'] ??
            reaction['entry_status'] ??
            source['entry_status'] ??
            '')
        .toString()
        .trim()
        .toUpperCase();
    final entryBlockReason = (scenario['entry_block_reason'] ??
            reaction['entry_block_reason'] ??
            source['entry_block_reason'] ??
            '')
        .toString()
        .trim();
    final policyRaw = strategy['effective_policy'] ??
        source['effective_policy'] ??
        (source['market_regime'] is Map
            ? (source['market_regime'] as Map)['effective_policy'] ??
                (source['market_regime'] as Map)['policy']
            : null);
    final policy = policyRaw is Map
        ? Map<String, dynamic>.from(policyRaw)
        : const <String, dynamic>{};
    final indicatorRaw = source['indicator_decision'];
    final indicator = indicatorRaw is Map
        ? Map<String, dynamic>.from(indicatorRaw)
        : const <String, dynamic>{};
    final directionalRaw = indicator['directional_scores'];
    final directional = directionalRaw is Map
        ? Map<String, dynamic>.from(directionalRaw)
        : const <String, dynamic>{};

    final sweepDirection = source['sweep_direction']?.toString().toLowerCase();
    final hasSweep = source['liquidity_swept'] == true &&
        ((action == 'long' && sweepDirection == 'low') ||
            (action == 'short' && sweepDirection == 'high'));

    final squeezeMomentum =
        (source['squeeze_momentum'] as num?)?.toDouble() ?? 0;
    final hasSqueeze = source['squeeze_status'] == 'squeeze_fire' &&
        ((action == 'long' && squeezeMomentum > 0) ||
            (action == 'short' && squeezeMomentum < 0));

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
      entryStatus: entryStatus,
      entryBlockReason: entryBlockReason,
      coreSetupGrade: (triCore['grade'] ?? 'WAIT').toString().toUpperCase(),
      longEvidence: ((directional['long'] as num?)?.toInt() ?? 0).clamp(0, 100),
      shortEvidence:
          ((directional['short'] as num?)?.toInt() ?? 0).clamp(0, 100),
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
  final String entryStatus;
  final String entryBlockReason;
  final String coreSetupGrade;
  final int longEvidence;
  final int shortEvidence;

  bool get confirmedNoEntry => entryStatus == 'CONFIRMED_NO_ENTRY';
  bool get pressureWarning => entryStatus == 'PRESSURE_WARNING';

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

  String get directionBadgeLabel {
    if (approved) {
      if (action == 'long') return 'LONG';
      if (action == 'short') return 'SHORT';
      return 'WAIT';
    }
    if (confirmedNoEntry && setupDirection == 'long') {
      return 'LONG CONFIRMED · NO ENTRY';
    }
    if (confirmedNoEntry && setupDirection == 'short') {
      return 'SHORT CONFIRMED · NO ENTRY';
    }
    if (pressureWarning && setupDirection == 'long') {
      return 'BULLISH PRESSURE · NOT ENTRY';
    }
    if (pressureWarning && setupDirection == 'short') {
      return 'BEARISH PRESSURE · NOT ENTRY';
    }
    if (setupDirection == 'long') return 'LONG BIAS · NOT ENTRY';
    if (setupDirection == 'short') return 'SHORT BIAS · NOT ENTRY';
    return 'NEUTRAL · WAIT';
  }

  bool get isGradeS => coreSetupGrade == 'S';

  String get setupGradeLabel {
    if (isGradeS) return '👑 SETUP S';
    if (coreSetupGrade == 'A') return '💎 SETUP A';
    return '⏳ NO ACTIVE SETUP';
  }

  String get gateScoreLabel {
    if (minConfluence <= 0) return '$confluence/100';
    return '$confluence/${minConfluence.round()}';
  }

  String get evidenceScoreLabel => 'Evidence L$longEvidence/S$shortEvidence';

  String get waitReasonThai {
    if (approved) return 'ผ่าน Strategy Gate';
    if (confirmedNoEntry) {
      final detail = entryBlockReason.isEmpty
          ? 'จุดเข้าที่เหลือไม่ผ่านเกณฑ์ความเสี่ยง'
          : _translateReason(entryBlockReason);
      return 'สัญญาณยืนยันแล้ว แต่ไม่เปิดสถานะ/ห้ามไล่ราคา — $detail';
    }
    if (pressureWarning) {
      return 'คำเตือนล่วงหน้า: แรงกดดันต่อโซนสูง แต่ยังไม่ยืนยันคำสั่ง';
    }
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
    if (lower.contains('zone reaction') && lower.contains('confirmed')) {
      return 'ยังไม่มีแท่งเทียนยืนยันการเด้ง/ปฏิเสธโซน';
    }
    if (lower.contains('liquidity sweep')) {
      return 'ยังไม่พบ Liquidity Sweep ยืนยัน';
    }
    if (lower.contains('volume delta')) {
      return 'Volume Delta ยังไม่ยืนยันทิศทาง';
    }
    if (lower.contains('squeeze release')) {
      return 'ยังไม่เกิด Squeeze Release';
    }
    if (lower.contains('liquidity target') && lower.contains('no opposing')) {
      return 'ยังไม่มีเป้าสภาพคล่องฝั่งตรงข้ามที่ใช้วาง Take Profit';
    }
    if (lower.contains('price extended') && lower.contains('atr')) {
      return 'ราคาออกห่างจากโซนยืนยันมากเกินไป ห้ามไล่ราคา';
    }
    if (lower.contains('entry window expired')) {
      return 'หน้าต่างเข้าเทรดหมดอายุแล้ว ห้ามไล่ราคา';
    }
    if (lower.contains('reaction invalidated')) {
      return 'แท่งเทียนล่าสุดทำให้ปฏิกิริยานี้ใช้เข้าเทรดไม่ได้แล้ว';
    }
    if (lower.contains('r:r')) return 'R:R ยังต่ำกว่าเกณฑ์';
    if (lower.contains('premium zone')) return 'ราคาอยู่ Premium Zone';
    if (lower.contains('discount zone')) return 'ราคาอยู่ Discount Zone';
    if (lower.contains('data is not ready') ||
        lower.contains('coverage below') ||
        lower.contains('indicator core data')) {
      return 'ข้อมูล Indicator/Regime ยังไม่พร้อม';
    }
    if (lower.contains('gate is not ready')) return 'Strategy Gate ยังไม่พร้อม';
    if (lower.contains('market structure is neutral')) {
      return 'โครงสร้างตลาดเป็น Sideway (Neutral)';
    }
    if (lower.contains('blocked')) return 'Market Regime ปิดรับคำสั่งใหม่';
    if (lower.contains('order block')) return 'ยังไม่พบ Order Block ตามทิศทาง';
    if (lower.contains('no trade direction')) return 'ยังไม่พบทิศทางเข้าเทรด';
    return reason;
  }
}
