import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ai_trade_advisor/core/trading/authority_pipeline_panel.dart';
import 'package:ai_trade_advisor/core/trading/authority_pipeline_view.dart';

void main() {
  final payload = <String, dynamic>{
    'authority_status': {
      'lifecycle_state': 'RISK_APPROVED',
      'lifecycle_reason': 'risk approved',
      'tri_core': {
        'status': 'ready',
        'direction': 'long',
        'grade': 'A',
        'setup_type': 'sweep_reversal',
        'structural_rr': 3.0,
      },
      'ai_review': {
        'status': 'reviewed',
        'verdict': 'COHERENT',
        'reason': 'No material contradiction',
      },
      'execution': {
        'status': 'validated',
        'reason_code': 'OK',
        'structural_rr': 3.0,
        'executable_rr': 2.4,
        'quote_freshness': 'fresh',
        'quote_age_seconds': 0.7,
      },
      'risk': {
        'status': 'approved',
        'risk_pct': 0.75,
        'position_size': 1.25,
      },
      'order': {'status': 'submitted', 'ready': false},
      'oms': {
        'status': 'open',
        'order_type': 'market',
        'fill_price': 100.1,
      },
    },
  };

  test('authority view keeps structural and executable RR distinct', () {
    final view = AuthorityPipelineView.fromPayload(payload);
    expect(view.triCore.primaryMetric, 'RR 3.00');
    expect(view.execution.primaryMetric, 'RR 3.00 → RR 2.40');
    expect(view.execution.secondaryMetric, contains('fresh'));
    expect(view.execution.secondaryMetric, contains('Quote 0.7s'));
    expect(view.risk.primaryMetric, 'Risk 0.75%');
    expect(view.risk.secondaryMetric, 'Max size 1.25');
    expect(view.orderReady, isFalse);
    expect(view.oms.status, 'open');
    expect(view.isPositionOpen, isTrue);
    expect(view.isClosed, isFalse);
  });

  test('closed OMS outcome is exposed as terminal instead of open', () {
    final closed = <String, dynamic>{
      'authority_status': {
        'lifecycle_state': 'CLOSED',
        'tri_core': {'status': 'ready', 'grade': 'A'},
        'ai_review': {'verdict': 'COHERENT'},
        'execution': {'status': 'validated'},
        'risk': {'status': 'approved'},
        'order': {'status': 'closed', 'ready': false},
        'oms': {
          'status': 'closed',
          'close_reason': 'Stop Loss (SL Hit)',
        },
      },
    };
    final view = AuthorityPipelineView.fromPayload(closed);
    expect(view.isClosed, isTrue);
    expect(view.isPositionOpen, isFalse);
    expect(view.orderReady, isFalse);
    expect(view.oms.reason, 'Stop Loss (SL Hit)');
  });

  testWidgets('pipeline renders six independent authority stages',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: AuthorityPipelinePanel(payload: payload)),
    ));

    expect(find.text('DECISION PIPELINE'), findsOneWidget);
    expect(find.text('TriCore'), findsOneWidget);
    expect(find.text('AI Review'), findsOneWidget);
    expect(find.text('Execution'), findsOneWidget);
    expect(find.text('Risk'), findsOneWidget);
    expect(find.text('Order'), findsOneWidget);
    expect(find.text('OMS'), findsOneWidget);
    expect(find.textContaining('RR 3.00 → RR 2.40'), findsOneWidget);
    expect(find.textContaining('Quote 0.7s'), findsOneWidget);
  });

  testWidgets('stale quote and conflict remain explicit rejection states',
      (tester) async {
    final rejected = <String, dynamic>{
      'authority_status': {
        'lifecycle_state': 'EXECUTION_REJECTED',
        'tri_core': {'status': 'ready', 'structural_rr': 2.8},
        'ai_review': {'status': 'reviewed', 'verdict': 'CONFLICT'},
        'execution': {
          'status': 'rejected',
          'reason_code': 'QUOTE_STALE',
          'quote_freshness': 'stale',
          'quote_age_seconds': 31.0,
          'structural_rr': 2.8,
        },
        'risk': {'status': 'not_run'},
        'order': {
          'status': 'not_ready',
          'ready': false,
          'manual_override_required': true,
        },
        'oms': {'status': 'not_submitted'},
      },
    };
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: AuthorityPipelinePanel(payload: rejected)),
    ));
    expect(find.text('CONFLICT'), findsOneWidget);
    expect(find.text('REJECTED'), findsOneWidget);
    expect(find.textContaining('QUOTE_STALE'), findsOneWidget);
    expect(find.textContaining('stale'), findsOneWidget);
    expect(find.text('NOT READY'), findsOneWidget);
  });

  test('not requested AI is not rendered as unavailable', () {
    final waitPayload = <String, dynamic>{
      'authority_status': {
        'lifecycle_state': 'DETECTED',
        'tri_core': {
          'status': 'wait',
          'direction': 'long',
          'grade': 'WAIT',
          'setup_type': 'breaker_retest',
        },
        'ai_review': {
          'status': 'not_requested',
          'verdict': 'UNAVAILABLE',
        },
        'execution': {'status': 'not_run'},
        'risk': {'status': 'not_run'},
        'order': {'status': 'not_ready'},
        'oms': {'status': 'not_submitted'},
      },
    };

    final view = AuthorityPipelineView.fromPayload(waitPayload);

    expect(view.ai.status, 'NOT_REQUESTED');
    expect(view.ai.isNegative, isFalse);
  });

  test('AI caution is fail-closed rather than positive', () {
    final cautionPayload = <String, dynamic>{
      'authority_status': {
        'lifecycle_state': 'AI_CAUTION',
        'tri_core': {'status': 'ready'},
        'ai_review': {
          'status': 'reviewed',
          'verdict': 'CAUTION',
          'reason': 'Opposing CVD evidence remains unresolved',
        },
        'execution': {'status': 'not_run'},
        'risk': {'status': 'not_run'},
        'order': {'status': 'not_ready', 'ready': false},
        'oms': {'status': 'not_submitted'},
      },
    };

    final view = AuthorityPipelineView.fromPayload(cautionPayload);

    expect(view.ai.status, 'CAUTION');
    expect(view.ai.isNegative, isTrue);
    expect(view.orderReady, isFalse);
  });

  test('pending AI review exposes each live processing stage', () {
    Map<String, dynamic> stagePayload(String stage) => <String, dynamic>{
          'authority_status': {
            'lifecycle_state': 'AI_REVIEW_PENDING',
            'tri_core': {
              'status': 'ready',
              'direction': 'long',
              'grade': 'A',
              'setup_type': 'sweep_reversal',
            },
            'ai_review': {
              'status': 'pending',
              'verdict': 'PENDING',
              'review_stage': stage,
            },
            'execution': {'status': 'not_run'},
            'risk': {'status': 'not_run'},
            'order': {'status': 'not_ready'},
            'oms': {'status': 'not_submitted'},
          },
        };

    expect(AuthorityPipelineView.fromPayload(stagePayload('QUEUED')).ai.status,
        'AI_QUEUED');
    expect(
        AuthorityPipelineView.fromPayload(stagePayload('BLIND_READING'))
            .ai
            .status,
        'BLIND_READING');
    expect(
        AuthorityPipelineView.fromPayload(stagePayload('COMPARING')).ai.status,
        'COMPARING_WITH_TRICORE');
  });

  test(
      'authority status payload maps correctly to waiting retest and limit armed states',
      () {
    final statusPayload = <String, dynamic>{
      'authority_status': {
        'candidate_id': 'snap-sol:event-sol',
        'symbol': 'SOL/USDT',
        'lifecycle_state': 'LIMIT_ARMED',
        'lifecycle_reason': 'Range Fade • Limit entry armed at causal zone',
        'tri_core': {
          'status': 'ready',
          'direction': 'short',
          'grade': 'A',
          'structural_rr': 3.0,
        },
        'ai_review': {
          'status': 'reviewed',
          'verdict': 'COHERENT',
        },
        'execution': {
          'status': 'limit_armed',
          'intent': 'place_limit',
          'auto_armed_limit': true,
          'structural_rr': 3.0,
          'executable_rr': 3.0,
          'executable_entry': 119.5,
        },
        'risk': {
          'status': 'approved',
          'risk_pct': 1.0,
        },
        'order': {
          'status': 'ready',
          'ready': true,
        },
        'oms': {
          'status': 'not_submitted',
        },
      },
    };

    final view = AuthorityPipelineView.fromPayload(statusPayload);

    expect(view.lifecycleState, 'LIMIT_ARMED');
    expect(view.triCore.status, 'ready');
    expect(view.triCore.primaryMetric, 'RR 3.00');
    expect(view.execution.status, 'limit_armed');
    expect(view.execution.isNegative, isFalse);
    expect(view.execution.isPositive, isTrue);
    expect(view.risk.status, 'approved');
    expect(view.orderReady, isTrue);
  });

  testWidgets('renders 3-tier execution pipeline and out-of-loop AI in advisory mode',
      (tester) async {
    final advisoryPayload = <String, dynamic>{
      'authority_status': {
        'lifecycle_state': 'WAITING_RETEST',
        'lifecycle_reason': 'Armed limit at structural entry',
        'tri_core': {
          'status': 'ready',
          'direction': 'short',
          'grade': 'A',
          'setup_type': 'ping_pong',
          'structural_rr': 2.5,
        },
        'ai_review': {
          'status': 'reviewed',
          'verdict': 'PASS',
          'policy': 'advisory',
          'required': false,
          'reason': 'Market structure is aligned',
        },
        'execution': {
          'status': 'waiting_retest',
          'reason_code': 'EXECUTABLE_RR_TOO_LOW',
          'structural_rr': 2.5,
          'executable_rr': 1.5,
        },
        'risk': {
          'status': 'approved',
          'risk_pct': 2.0,
        },
        'order': {'status': 'ready'},
        'oms': {
          'status': 'not_submitted',
        },
      },
    };

    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: AuthorityPipelinePanel(payload: advisoryPayload)),
    ));

    expect(find.text('3-TIER EXECUTION PIPELINE'), findsOneWidget);
    expect(find.text('1. Decision Engine'), findsOneWidget);
    expect(find.text('2. Risk & Guard'), findsOneWidget);
    expect(find.text('3. OMS'), findsOneWidget);
    expect(find.text('AI ADVISOR (OUT-OF-LOOP):'), findsOneWidget);
    expect(find.textContaining('PASS (ADVISORY)'), findsOneWidget);
    expect(find.textContaining('Non-blocking'), findsOneWidget);
  });
}
