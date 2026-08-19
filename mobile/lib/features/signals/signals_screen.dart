import 'package:flutter/material.dart';
import '../../app/theme.dart';

class SignalsScreen extends StatelessWidget {
  const SignalsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: const Text('SMC Signals'),
        backgroundColor: AppColors.surface,
        actions: [
          IconButton(icon: const Icon(Icons.filter_list), onPressed: () {}),
        ],
      ),
      body: ListView.builder(
        padding: const EdgeInsets.all(12),
        itemCount: 5,
        itemBuilder: (ctx, i) => _SignalCard(
          symbol: i % 2 == 0 ? 'BTC/USDT' : 'XAUUSD',
          direction: i % 3 == 0 ? 'SHORT' : 'LONG',
          timeframe: i % 2 == 0 ? '1H' : '4H',
          confluence: 60 + i * 8,
          message:
              'Bullish CHoCH + Liquidity sweep below EQL. Price entering discount zone with bullish OB confluence.',
          time: '${i + 1}h ago',
        ),
      ),
    );
  }
}

class _SignalCard extends StatelessWidget {
  final String symbol, direction, timeframe, message, time;
  final int confluence;

  const _SignalCard({
    required this.symbol,
    required this.direction,
    required this.timeframe,
    required this.confluence,
    required this.message,
    required this.time,
  });

  @override
  Widget build(BuildContext context) {
    final isLong = direction == 'LONG';
    final color = isLong ? AppColors.bullish : AppColors.bearish;
    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                  decoration: BoxDecoration(
                    color: color.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: color, width: 1),
                  ),
                  child: Text(
                    direction,
                    style: TextStyle(color: color, fontWeight: FontWeight.bold, fontSize: 12),
                  ),
                ),
                const SizedBox(width: 8),
                Text(symbol, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: const Color(0xFF252540),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(timeframe, style: const TextStyle(fontSize: 11, color: Colors.white70)),
                ),
                const SizedBox(width: 8),
                Text(time, style: const TextStyle(fontSize: 11, color: Colors.white38)),
              ],
            ),
            const SizedBox(height: 10),
            Text(message, style: const TextStyle(fontSize: 13, color: Colors.white70, height: 1.4)),
            const SizedBox(height: 10),
            Row(
              children: [
                const Text('Confluence: ', style: TextStyle(fontSize: 12, color: Colors.white38)),
                Text(
                  '$confluence/100',
                  style: TextStyle(fontSize: 12, color: color, fontWeight: FontWeight.bold),
                ),
                const Spacer(),
                TextButton(
                  onPressed: () {},
                  child: const Text('Details →'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
