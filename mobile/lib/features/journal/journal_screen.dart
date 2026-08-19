import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';

class JournalScreen extends StatefulWidget {
  const JournalScreen({super.key});

  @override
  State<JournalScreen> createState() => _JournalScreenState();
}

class _JournalScreenState extends State<JournalScreen> {
  List<Map<String, dynamic>> _trades = [];
  bool _isLoading = true;

  @override
  void initState() {
    super.initState();
    _fetchTrades();
  }

  Future<void> _fetchTrades() async {
    try {
      final dio = Dio();
      final resp = await dio.get('http://127.0.0.1:8000/api/v1/trades/');
      final List<dynamic> list = resp.data['trades'] ?? [];
      setState(() {
        _trades = list.map((e) => Map<String, dynamic>.from(e as Map)).toList();
        _isLoading = false;
      });
    } catch (e) {
      setState(() => _isLoading = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final closed = _trades.where((t) => t['status'] == 'closed').toList();
    final wins = closed.where((t) => ((t['pnl'] ?? 0) as num) > 0).toList();
    final winRate = closed.isNotEmpty ? ((wins.length / closed.length) * 100).toStringAsFixed(0) : '0';
    final totalPnl = closed.fold(0.0, (acc, t) => acc + (((t['pnl'] ?? 0) as num).toDouble()));

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: const Text('Trade Journal'),
        backgroundColor: AppColors.surface,
        actions: [
          IconButton(icon: const Icon(Icons.refresh), onPressed: _fetchTrades),
        ],
      ),
      body: Column(
        children: [
          _buildSummaryBar(winRate, totalPnl, _trades.length),
          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator(color: AppColors.bullish))
                : _trades.isEmpty
                    ? const Center(
                        child: Text(
                          'No trade history yet.\nOpen and close paper trades on the Chart screen!',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: AppColors.textMuted),
                        ),
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.all(12),
                        itemCount: _trades.length,
                        itemBuilder: (ctx, i) {
                          final t = _trades[i];
                          final sym = t['symbol'] ?? 'BTC/USDT';
                          final dir = (t['direction'] ?? 'long').toString().toUpperCase();
                          final pnl = ((t['pnl'] ?? 0.0) as num).toDouble();
                          final pnlPct = ((t['pnl_pct'] ?? 0.0) as num).toDouble();
                          final status = t['status'] ?? 'open';
                          final date = (t['opened_at'] ?? '').toString().split('T').first;

                          return _TradeCard(
                            symbol: sym,
                            direction: dir,
                            pnl: pnlPct,
                            pnlUsd: pnl,
                            status: status,
                            rr: 2.3,
                            date: date,
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }

  Widget _buildSummaryBar(String winRate, double totalPnl, int count) {
    final isPos = totalPnl >= 0;
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: [
          _stat('Win Rate', '$winRate%', AppColors.bullish),
          _stat('Avg R:R', '2.4', AppColors.neutral),
          _stat('Total PnL', '${isPos ? '+' : ''}\$${totalPnl.toStringAsFixed(2)}', isPos ? AppColors.bullish : AppColors.bearish),
          _stat('Total Trades', '$count', Colors.white70),
        ],
      ),
    );
  }

  Widget _stat(String label, String value, Color color) {
    return Column(
      children: [
        Text(label, style: const TextStyle(fontSize: 10, color: Colors.white38)),
        const SizedBox(height: 2),
        Text(value, style: TextStyle(fontSize: 14, color: color, fontWeight: FontWeight.bold)),
      ],
    );
  }
}

class _TradeCard extends StatelessWidget {
  final String symbol, direction, date, status;
  final double pnl, pnlUsd, rr;

  const _TradeCard({
    required this.symbol,
    required this.direction,
    required this.pnl,
    required this.pnlUsd,
    required this.status,
    required this.rr,
    required this.date,
  });

  @override
  Widget build(BuildContext context) {
    final isWin = pnlUsd > 0;
    final isOpen = status == 'open';
    final color = isOpen ? AppColors.neutral : (isWin ? AppColors.bullish : AppColors.bearish);

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: ListTile(
        leading: CircleAvatar(
          backgroundColor: color.withOpacity(0.15),
          child: Icon(
            isOpen ? Icons.lock_clock : (isWin ? Icons.trending_up : Icons.trending_down),
            color: color,
            size: 18,
          ),
        ),
        title: Row(
          children: [
            Text(symbol, style: const TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(width: 8),
            Text(direction, style: TextStyle(fontSize: 12, color: color, fontWeight: FontWeight.bold)),
            const Spacer(),
            if (isOpen)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: AppColors.neutral.withOpacity(0.15),
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(color: AppColors.neutral),
                ),
                child: const Text('OPEN', style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: AppColors.neutral)),
              ),
          ],
        ),
        subtitle: Text('$date  •  R:R $rr', style: const TextStyle(fontSize: 11, color: Colors.white38)),
        trailing: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(
              '${pnlUsd >= 0 ? '+' : ''}\$${pnlUsd.toStringAsFixed(2)}',
              style: TextStyle(color: color, fontWeight: FontWeight.bold, fontSize: 14),
            ),
            Text(
              '(${pnl >= 0 ? '+' : ''}${pnl.toStringAsFixed(2)}%)',
              style: TextStyle(color: color, fontSize: 11),
            ),
          ],
        ),
      ),
    );
  }
}
