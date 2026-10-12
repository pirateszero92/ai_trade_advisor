import 'package:flutter/material.dart';
import 'authority_pipeline_view.dart';

class AuthorityPipelinePanel extends StatelessWidget {
  const AuthorityPipelinePanel(
      {super.key, required this.payload, this.compact = false});

  final Map<String, dynamic>? payload;
  final bool compact;

  Color _color(AuthorityStageView stage) {
    if (stage.isCaution) return Colors.orangeAccent;
    if (stage.isPositive) return Colors.greenAccent;
    if (stage.isNegative) return Colors.redAccent;
    return Colors.amberAccent;
  }

  @override
  Widget build(BuildContext context) {
    final view = AuthorityPipelineView.fromPayload(payload);
    if ((payload?['authority_status']) is! Map) {
      return const SizedBox.shrink();
    }

    return Container(
      width: double.infinity,
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: const Color(0xFF111B2A),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: Colors.white12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(children: [
            const Expanded(
              child: Text('DECISION PIPELINE',
                  style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                      color: Colors.white70,
                      letterSpacing: 0.8)),
            ),
            Text(view.lifecycleState.replaceAll('_', ' '),
                style: const TextStyle(
                    fontSize: 9,
                    color: Colors.lightBlueAccent,
                    fontWeight: FontWeight.bold)),
          ]),
          if (view.lifecycleReason.isNotEmpty && !compact)
            Padding(
              padding: const EdgeInsets.only(top: 3),
              child: Text(view.lifecycleReason,
                  style: const TextStyle(fontSize: 9, color: Colors.white38)),
            ),
          const SizedBox(height: 8),
          ...view.stages.map((stage) => Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Container(
                        width: 7,
                        height: 7,
                        margin: const EdgeInsets.only(top: 4),
                        decoration: BoxDecoration(
                            color: _color(stage), shape: BoxShape.circle)),
                    const SizedBox(width: 7),
                    SizedBox(
                        width: 70,
                        child: Text(stage.name,
                            style: const TextStyle(
                                fontSize: 10,
                                color: Colors.white70,
                                fontWeight: FontWeight.bold))),
                    Expanded(
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                                stage.status.replaceAll('_', ' ').toUpperCase(),
                                style: TextStyle(
                                    fontSize: 10,
                                    color: _color(stage),
                                    fontWeight: FontWeight.bold)),
                            if (stage.primaryMetric.isNotEmpty ||
                                stage.secondaryMetric.isNotEmpty)
                              Text(
                                  [stage.primaryMetric, stage.secondaryMetric]
                                      .where((e) => e.isNotEmpty)
                                      .join(' • '),
                                  style: const TextStyle(
                                      fontSize: 9, color: Colors.cyanAccent)),
                            if (!compact && stage.reason.isNotEmpty)
                              Text(stage.reason,
                                  style: const TextStyle(
                                      fontSize: 9, color: Colors.white54)),
                          ]),
                    ),
                  ],
                ),
              )),
        ],
      ),
    );
  }
}
