class AuthorityStageView {
  const AuthorityStageView({
    required this.name,
    required this.status,
    this.reason = '',
    this.primaryMetric = '',
    this.secondaryMetric = '',
  });

  final String name;
  final String status;
  final String reason;
  final String primaryMetric;
  final String secondaryMetric;

  bool get isPositive => const {
        'ready',
        'coherent',
        'fast_track',
        'fast_tracked',
        'validated',
        'approved',
        'open',
        'closed',
        'partial',
        'partially_filled',
        'submitted',
        'market_ready',
        'limit_armed',
        'order_pending',
        'filled',
        'waiting_for_fill',
      }.contains(status.toLowerCase());

  bool get isCaution => status.toLowerCase() == 'caution';

  bool get isNegative => const {
        'conflict',
        'blocked',
        'unavailable',
        'rejected',
        'error',
        'invalidated',
        'expired',
        'cancelled',
        'canceled',
        'caution',
      }.contains(status.toLowerCase());
}

class AuthorityPipelineView {
  factory AuthorityPipelineView.fromPayload(Map<String, dynamic>? payload) {
    final root = payload ?? const <String, dynamic>{};

    // Active pipeline authority derives directly from backend authority_status
    final raw = root['authority_status'];
    final status =
        raw is Map ? Map<String, dynamic>.from(raw) : const <String, dynamic>{};

    Map<String, dynamic> section(String key) {
      final value = status[key];
      return value is Map
          ? Map<String, dynamic>.from(value)
          : <String, dynamic>{};
    }

    String fmtRr(dynamic value) {
      final n = value is num ? value.toDouble() : double.tryParse('$value');
      return n == null ? '' : 'RR ${n.toStringAsFixed(2)}';
    }

    String fmtAge(dynamic value) {
      final n = value is num ? value.toDouble() : double.tryParse('$value');
      return n == null ? '' : 'Quote ${n.toStringAsFixed(1)}s';
    }

    final tri = section('tri_core');
    final ai = section('ai_review');
    final execution = section('execution');
    final risk = section('risk');
    final order = section('order');
    final oms = section('oms');
    final conflicts = ai['conflicts'] is List
        ? (ai['conflicts'] as List)
            .map((e) => e.toString())
            .where((e) => e.isNotEmpty)
            .toList()
        : <String>[];
    final aiStatus = (ai['status'] ?? 'not_requested').toString().toLowerCase();
    final aiVerdict = (ai['verdict'] ?? '').toString().toUpperCase();
    final aiReviewStage = (ai['review_stage'] ?? '').toString().toUpperCase();
    final aiDisplayStatus = switch (aiStatus) {
      'not_requested' => 'NOT_REQUESTED',
      'pending' => switch (aiReviewStage) {
          'QUEUED' => 'AI_QUEUED',
          'BLIND_READING' => 'BLIND_READING',
          'COMPARING' => 'COMPARING_WITH_TRICORE',
          _ => 'PENDING',
        },
      'queue_full' => 'QUEUE_FULL',
      'reviewed' => ai['fast_tracked'] == true
          ? 'FAST_TRACK'
          : (aiVerdict.isNotEmpty ? aiVerdict : 'UNAVAILABLE'),
      _ => aiVerdict.isNotEmpty ? aiVerdict : aiStatus.toUpperCase(),
    };

    final aiPolicy =
        (ai['policy'] ?? status['ai_policy'] ?? '').toString().toLowerCase();
    final bool isAdvisory =
        aiPolicy == 'advisory' || ai['required'] == false;

    final triStage = AuthorityStageView(
      name: isAdvisory ? '1. Decision Engine' : 'TriCore',
      status: (tri['status'] ?? 'unknown').toString(),
      reason: () {
        final parts = [
          if ((tri['direction'] ?? '').toString().isNotEmpty &&
              (tri['direction'] ?? '').toString().toLowerCase() != 'wait')
            (tri['direction'] ?? '').toString().toUpperCase(),
          if ((tri['grade'] ?? '').toString().isNotEmpty &&
              (tri['grade'] ?? '').toString().toUpperCase() != 'WAIT')
            'Grade ${(tri['grade'] ?? '').toString().toUpperCase()}',
          if ((tri['setup_type'] ?? '').toString().isNotEmpty)
            (tri['setup_type'] ?? '').toString(),
        ];
        if (parts.isNotEmpty) return parts.join(' • ');
        if (tri['reasons'] is List && (tri['reasons'] as List).isNotEmpty) {
          return (tri['reasons'] as List).first.toString();
        }
        return (tri['direction'] ?? '').toString().toUpperCase();
      }(),
      primaryMetric: fmtRr(tri['structural_rr']),
    );

    final aiStage = AuthorityStageView(
      name: isAdvisory ? 'AI Advisor' : 'AI Review',
      status: isAdvisory && aiStatus == 'not_requested'
          ? 'OUT_OF_LOOP'
          : (isAdvisory && aiStatus == 'reviewed'
              ? '$aiVerdict (ADVISORY)'
              : aiDisplayStatus),
      reason: (ai['failure_type'] ?? '').toString(),
      primaryMetric:
          ai['risk_score'] != null ? 'Score ${ai['risk_score']}/100' : '',
      secondaryMetric: isAdvisory
          ? 'Non-blocking • 1.0x'
          : (ai['suggested_risk_multiplier'] != null &&
                  (ai['suggested_risk_multiplier'] as num) < 1.0
              ? '${ai['suggested_risk_multiplier']}x Risk'
              : (ai['risk_rating'] ?? '').toString()),
    );

    final execStage = AuthorityStageView(
      name: 'Execution',
      status: (execution['status'] ?? 'not_run').toString(),
      reason: (execution['reason_code'] ??
              ((execution['reasons'] is List &&
                      (execution['reasons'] as List).isNotEmpty)
                  ? (execution['reasons'] as List).join(' • ')
                  : ''))
          .toString(),
      primaryMetric: [
        fmtRr(execution['structural_rr']),
        fmtRr(execution['executable_rr'])
      ].where((e) => e.isNotEmpty).join(' → '),
      secondaryMetric: [
        if (execution['auto_armed_limit'] == true)
          'LIMIT ARMED'
        else if ((execution['quote_freshness'] ?? '').toString().isNotEmpty)
          (execution['quote_freshness'] ?? '').toString(),
        if (execution['auto_armed_limit'] == true &&
            execution['executable_entry'] != null)
          'Entry ${execution['executable_entry']}',
        fmtAge(execution['quote_age_seconds']),
      ].where((e) => e.isNotEmpty).join(' • '),
    );

    final riskStage = AuthorityStageView(
      name: 'Risk',
      status: (risk['status'] ?? 'not_run').toString(),
      reason: (risk['reason'] ?? '').toString(),
      primaryMetric: risk['risk_pct'] is num
          ? 'Risk ${(risk['risk_pct'] as num).toStringAsFixed(2)}%'
          : '',
      secondaryMetric: risk['position_size'] is num
          ? 'Max size ${(risk['position_size'] as num).toString()}'
          : '',
    );

    final orderStage = AuthorityStageView(
      name: 'Order',
      status: (order['status'] ?? 'not_ready').toString(),
      reason: order['manual_override_required'] == true
          ? 'Final pipeline incomplete; manual action requires explicit override'
          : '',
    );

    final omsStage = AuthorityStageView(
      name: isAdvisory ? '3. OMS' : 'OMS',
      status: (oms['status'] ?? 'not_submitted').toString(),
      reason: (oms['close_reason'] ?? '').toString(),
      primaryMetric: (oms['order_type'] ?? '').toString(),
      secondaryMetric: oms['fill_price'] is num
          ? 'Fill ${(oms['fill_price'] as num).toString()}'
          : '',
    );

    final riskGuardStage = AuthorityStageView(
      name: '2. Risk & Guard',
      status: () {
        final execSt = (execution['status'] ?? '').toString().toLowerCase();
        final rCode = (execution['reason_code'] ?? '').toString();
        if (execSt == 'limit_armed') return 'LIMIT_ARMED';
        if (execSt == 'waiting_retest' || rCode == 'EXECUTABLE_RR_TOO_LOW') {
          return 'WAITING_RETEST';
        }
        if ((risk['status'] ?? '').toString().toLowerCase() == 'rejected' ||
            status['final_block_stage'] == 'RISK') {
          return 'RISK_BLOCKED';
        }
        if (execSt == 'rejected' ||
            status['final_block_stage'] == 'EXECUTION_REVALIDATION') {
          return 'EXEC_REJECTED';
        }
        if (execSt == 'validated' &&
            (risk['status'] ?? '').toString().toLowerCase() == 'approved') {
          return 'APPROVED';
        }
        if (execSt == 'validated') return 'VALIDATED';
        if ((risk['status'] ?? '').toString().toLowerCase() == 'approved') {
          return 'APPROVED';
        }
        return (execution['status'] ?? risk['status'] ?? 'not_run').toString();
      }(),
      reason: (execution['reason_code'] ??
              ((execution['reasons'] is List &&
                      (execution['reasons'] as List).isNotEmpty)
                  ? (execution['reasons'] as List).join(' • ')
                  : (risk['reason'] ?? '')))
          .toString(),
      primaryMetric: [
        if (execution['executable_rr'] != null)
          fmtRr(execution['executable_rr'])
        else if (execution['structural_rr'] != null)
          fmtRr(execution['structural_rr']),
        if (risk['risk_pct'] is num)
          'Risk ${(risk['risk_pct'] as num).toStringAsFixed(1)}%',
      ].where((e) => e.isNotEmpty).join(' • '),
      secondaryMetric: [
        if (execution['auto_armed_limit'] == true &&
            execution['executable_entry'] != null)
          'Limit ${execution['executable_entry']}'
        else if (execution['auto_armed_limit'] == true)
          'LIMIT ARMED'
        else if ((execution['quote_freshness'] ?? '').toString().isNotEmpty)
          (execution['quote_freshness'] ?? '').toString(),
        fmtAge(execution['quote_age_seconds']),
      ].where((e) => e.isNotEmpty).join(' • '),
    );

    return AuthorityPipelineView(
      lifecycleState: (status['lifecycle_state'] ?? 'UNKNOWN').toString(),
      lifecycleReason:
          (status['lifecycle_reason'] ?? status['lifecycle_reason_code'] ?? '')
              .toString(),
      triCore: triStage,
      ai: aiStage,
      execution: execStage,
      risk: riskStage,
      order: orderStage,
      oms: omsStage,
      isAdvisory: isAdvisory,
      decisionEngine: triStage,
      riskGuard: riskGuardStage,
      omsExecution: omsStage,
      aiAdvisory: aiStage,
    );
  }

  final String lifecycleState;
  final String lifecycleReason;
  final AuthorityStageView triCore;
  final AuthorityStageView ai;
  final AuthorityStageView execution;
  final AuthorityStageView risk;
  final AuthorityStageView order;
  final AuthorityStageView oms;
  final bool isAdvisory;
  final AuthorityStageView decisionEngine;
  final AuthorityStageView riskGuard;
  final AuthorityStageView omsExecution;
  final AuthorityStageView aiAdvisory;

  const AuthorityPipelineView({
    required this.lifecycleState,
    required this.lifecycleReason,
    required this.triCore,
    required this.ai,
    required this.execution,
    required this.risk,
    required this.order,
    required this.oms,
    this.isAdvisory = false,
    AuthorityStageView? decisionEngine,
    AuthorityStageView? riskGuard,
    AuthorityStageView? omsExecution,
    AuthorityStageView? aiAdvisory,
  })  : decisionEngine = decisionEngine ?? triCore,
        riskGuard = riskGuard ?? execution,
        omsExecution = omsExecution ?? oms,
        aiAdvisory = aiAdvisory ?? ai;

  bool get orderReady => const {
        'ready',
        'armed',
        'limit_armed',
      }.contains(order.status.toLowerCase());

  bool get isClosed =>
      lifecycleState.toUpperCase() == 'CLOSED' ||
      oms.status.toLowerCase() == 'closed';

  bool get isOrderPending =>
      lifecycleState.toUpperCase() == 'ORDER_PENDING' ||
      const {'pending', 'new', 'accepted'}.contains(oms.status.toLowerCase());

  bool get isPositionOpen =>
      lifecycleState.toUpperCase() == 'OPEN' ||
      const {'open', 'partial', 'partially_filled'}
          .contains(oms.status.toLowerCase());

  List<AuthorityStageView> get stages => isAdvisory
      ? [decisionEngine, riskGuard, omsExecution]
      : [triCore, ai, execution, risk, order, oms];
}
