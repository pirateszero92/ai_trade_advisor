import 'package:flutter/material.dart';
import '../../app/theme.dart';

class JournalScreen extends StatelessWidget {
  const JournalScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: const Text('Trade Journal'),
        backgroundColor: AppColors.surface,
      ),
      body: Column(
        children: [
          _buildSummaryBar(),
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: 5,
              itemBuilder: (ctx, i) => _TradeCard(
                symbol: i % 2 == 0 ? 'BTC/USDT' : 'XAUUSD',
                direction: i % 3 == 0 ? 'SHORT' : 'LONG',
                pnl: i % 2 == 0 ? 2.3 * (i + 1) : -1.1 * (i + 1),
                rr: 2.3,
                date: '2024-08-${15 - i}',
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildSummaryBar() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: [
          _stat('Win Rate', '62%', AppColors.bullish),
          _stat('Avg R:R', '2.4', AppColors.neutral),
          _stat('Total PnL', '+4.8%', AppColors.bullish),
          _stat('Trades', '13', Colors.white70),
        ],
      ),
    );
  }

  Widget _stat(String label, String value, Color color) {
    return Column(
      children: [
        Text(label, style: const TextStyle(fontSize: 10, color: Colors.white38)),
        Text(value, style: TextStyle(fontSize: 14, color: color, fontWeight: FontWeight.bold)),
      ],
    );
  }
}

class _TradeCard extends StatelessWidget {
  final String symbol, direction, date;
  final double pnl, rr;

  const _TradeCard({
    required this.symbol,
    required this.direction,
    required this.pnl,
    required this.rr,
    required this.date,
  });

  @override
  Widget build(BuildContext context) {
    final isWin = pnl > 0;
    final color = isWin ? AppColors.bullish : AppColors.bearish;
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: color.withOpacity(0.15),
          child: Icon(
            isWin ? Icons.trending_up : Icons.trending_down,
            color: color,
            size: 18,
          ),
        ),
        title: Row(
          children: [
            Text(symbol, style: const TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(width: 8),
            Text(direction, style: TextStyle(fontSize: 12, color: color)),
          ],
        ),
        subtitle: Text('$date  •  R:R $rr', style: const TextStyle(fontSize: 11, color: Colors.white38)),
        trailing: Text(
          '${isWin ? '+' : ''}${pnl.toStringAsFixed(2)}%',
          style: TextStyle(color: color, fontWeight: FontWeight.bold, fontSize: 15),
        ),
      ),
    );
  }
}
