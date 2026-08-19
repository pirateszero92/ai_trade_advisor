import 'dart:async';
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
  Timer? _liveTimer;

  @override
  void initState() {
    super.initState();
    _fetchTrades();
    _startLiveTicker();
  }

  @override
  void dispose() {
    _liveTimer?.cancel();
    super.dispose();
  }

  void _startLiveTicker() {
    _liveTimer = Timer.periodic(const Duration(milliseconds: 1400), (_) {
      if (!mounted || _trades.isEmpty) return;
      setState(() {
        for (var t in _trades) {
          if (t['status'] == 'open') {
            final entry = (t['entry'] as num?)?.toDouble() ?? 100.0;
            final cur = (t['live_price'] as num?)?.toDouble() ?? entry;
            final delta = (DateTime.now().millisecond % 5 - 2) * 0.04;
            final newLive = double.parse((cur + delta).clamp(1.0, 100000.0).toStringAsFixed(2));
            t['live_price'] = newLive;

            final isLong = (t['direction'] ?? 'long').toString().toLowerCase() == 'long';
            final size = (t['position_size'] ?? t['size'] ?? 1.0) as num;
            final pnl = isLong ? (newLive - entry) * size.toDouble() : (entry - newLive) * size.toDouble();
            final pnlPct = entry > 0 ? (isLong ? (newLive - entry) / entry : (entry - newLive) / entry) * 100 : 0.0;

            t['live_pnl'] = double.parse(pnl.toStringAsFixed(2));
            t['live_pnl_pct'] = double.parse(pnlPct.toStringAsFixed(2));
          }
        }
      });
    });
  }

  Future<void> _fetchTrades() async {
    try {
      final dio = Dio();
      final resp = await dio.get('http://127.0.0.1:8000/api/v1/trades/');
      final List<dynamic> list = resp.data['trades'] ?? [];
      setState(() {
        _trades = list.map((e) {
          final m = Map<String, dynamic>.from(e as Map);
          final entry = (m['entry'] as num?)?.toDouble() ?? 100.0;
          m['live_price'] = entry;
          m['live_pnl'] = 0.0;
          m['live_pnl_pct'] = 0.0;
          return m;
        }).toList();
        _isLoading = false;
      });
    } catch (e) {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _closeTrade(String tradeId) async {
    try {
      final dio = Dio();
      final t = _trades.firstWhere((e) => e['id']?.toString() == tradeId, orElse: () => {});
      final closePrice = (t['live_price'] as num?)?.toDouble() ?? (t['entry'] as num?)?.toDouble() ?? 100.0;

      await dio.post(
        'http://127.0.0.1:8000/api/v1/trades/$tradeId/close',
        data: {
          'close_price': closePrice,
          'reason': 'Manual Close from Journal',
        },
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text('✅ Closed position @ \$$closePrice', style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
          ),
        );
      }
      _fetchTrades();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(backgroundColor: AppColors.bearish, content: Text('Failed to close: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final closed = _trades.where((t) => t['status'] == 'closed').toList();
    final openList = _trades.where((t) => t['status'] == 'open').toList();

    final wins = closed.where((t) => ((t['pnl'] ?? 0) as num) > 0).toList();
    final winRate = closed.isNotEmpty ? ((wins.length / closed.length) * 100).toStringAsFixed(0) : '0';
    final realizedPnl = closed.fold(0.0, (acc, t) => acc + (((t['pnl'] ?? 0) as num).toDouble()));
    final unrealizedPnl = openList.fold(0.0, (acc, t) => acc + (((t['live_pnl'] ?? 0) as num).toDouble()));

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: const Text('Trade Journal & Performance'),
        backgroundColor: AppColors.surface,
        actions: [
          IconButton(icon: const Icon(Icons.refresh), tooltip: 'Refresh Journal', onPressed: _fetchTrades),
        ],
      ),
      body: Column(
        children: [
          _buildSummaryBar(winRate, realizedPnl, unrealizedPnl, openList.length, closed.length),
          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator(color: AppColors.bullish))
                : _trades.isEmpty
                    ? const Center(
                        child: Text(
                          'No trade history yet.\nOpen paper trades on the Chart or Signals screen!',
                          textAlign: TextAlign.center,
                          style: TextStyle(color: AppColors.textMuted),
                        ),
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.all(12),
                        itemCount: _trades.length,
                        itemBuilder: (ctx, i) {
                          final t = _trades[i];
                          final id = t['id']?.toString() ?? '';
                          final tag = t['tag'] ?? '#POS-$id';
                          final sym = t['symbol'] ?? 'BTC/USDT';
                          final dir = (t['direction'] ?? 'long').toString().toUpperCase();
                          final entry = (t['entry'] as num?)?.toDouble() ?? 0.0;
                          final livePrice = (t['live_price'] as num?)?.toDouble() ?? entry;
                          final size = (t['position_size'] ?? t['size'] ?? 1.0) as num;

                          final status = t['status'] ?? 'open';
                          final isOpen = status == 'open';

                          final pnl = isOpen ? ((t['live_pnl'] ?? 0.0) as num).toDouble() : ((t['pnl'] ?? 0.0) as num).toDouble();
                          final pnlPct = isOpen ? ((t['live_pnl_pct'] ?? 0.0) as num).toDouble() : ((t['pnl_pct'] ?? 0.0) as num).toDouble();
                          final date = (t['opened_at'] ?? '').toString().split('T').first;

                          return _TradeCard(
                            id: id,
                            tag: tag,
                            symbol: sym,
                            direction: dir,
                            entry: entry,
                            livePrice: livePrice,
                            size: size.toDouble(),
                            pnl: pnlPct,
                            pnlUsd: pnl,
                            status: status,
                            rr: 2.3,
                            date: date,
                            onClose: () => _closeTrade(id),
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }

  Widget _buildSummaryBar(String winRate, double realizedPnl, double unrealizedPnl, int openCount, int closedCount) {
    final isRealPos = realizedPnl >= 0;
    final isUnrealPos = unrealizedPnl >= 0;

    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: [
          _stat('Win Rate', '$winRate%', AppColors.bullish),
          _stat('Open Orders', '$openCount Active', const Color(0xFF00E5FF)),
          _stat(
            'Live UnPnL',
            '${isUnrealPos ? '+' : ''}\$${unrealizedPnl.toStringAsFixed(2)}',
            isUnrealPos ? AppColors.bullish : AppColors.bearish,
          ),
          _stat(
            'Realized PnL',
            '${isRealPos ? '+' : ''}\$${realizedPnl.toStringAsFixed(2)}',
            isRealPos ? AppColors.bullish : AppColors.bearish,
          ),
        ],
      ),
    );
  }

  Widget _stat(String label, String value, Color color) {
    return Column(
      children: [
        Text(label, style: const TextStyle(fontSize: 10, color: Colors.white38)),
        const SizedBox(height: 2),
        Text(value, style: TextStyle(fontSize: 13, color: color, fontWeight: FontWeight.bold, fontFamily: 'monospace')),
      ],
    );
  }
}

class _TradeCard extends StatelessWidget {
  final String id, tag, symbol, direction, date, status;
  final double entry, livePrice, size, pnl, pnlUsd, rr;
  final VoidCallback onClose;

  const _TradeCard({
    required this.id,
    required this.tag,
    required this.symbol,
    required this.direction,
    required this.entry,
    required this.livePrice,
    required this.size,
    required this.pnl,
    required this.pnlUsd,
    required this.status,
    required this.rr,
    required this.date,
    required this.onClose,
  });

  @override
  Widget build(BuildContext context) {
    final isWin = pnlUsd >= 0;
    final isOpen = status == 'open';
    final isLong = direction == 'LONG';
    final sideColor = isLong ? AppColors.bullish : AppColors.bearish;
    final pnlColor = isWin ? AppColors.bullish : AppColors.bearish;

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          children: [
            Row(
              children: [
                // Direction Badge
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: sideColor.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: sideColor, width: 0.8),
                  ),
                  child: Text(direction, style: TextStyle(color: sideColor, fontWeight: FontWeight.bold, fontSize: 11)),
                ),
                const SizedBox(width: 8),
                Text(symbol, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15)),
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: isOpen ? const Color(0xFF00E5FF).withOpacity(0.15) : const Color(0xFF252540),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: Text(
                    isOpen ? '● OPEN' : 'CLOSED',
                    style: TextStyle(
                      fontSize: 10,
                      fontWeight: FontWeight.bold,
                      color: isOpen ? const Color(0xFF00E5FF) : Colors.white54,
                    ),
                  ),
                ),
                const Spacer(),
                Text(
                  '${isWin ? '+' : ''}\$${pnlUsd.toStringAsFixed(2)} (${isWin ? '+' : ''}${pnl.toStringAsFixed(2)}%)',
                  style: TextStyle(color: pnlColor, fontWeight: FontWeight.bold, fontSize: 14, fontFamily: 'monospace'),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Text(tag, style: const TextStyle(fontSize: 11, color: Color(0xFF93C5FD), fontWeight: FontWeight.w600)),
                const SizedBox(width: 8),
                Text('• Entry: \$${entry.toStringAsFixed(2)}', style: const TextStyle(fontSize: 11, color: Colors.white54)),
                if (isOpen) ...[
                  const SizedBox(width: 6),
                  Text('• Live: \$${livePrice.toStringAsFixed(2)}', style: const TextStyle(fontSize: 11, color: AppColors.bullish, fontWeight: FontWeight.bold)),
                ],
                const Spacer(),
                Text(date, style: const TextStyle(fontSize: 11, color: Colors.white38)),
                if (isOpen) ...[
                  const SizedBox(width: 10),
                  ElevatedButton(
                    onPressed: onClose,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.bearish.withOpacity(0.2),
                      foregroundColor: AppColors.bearish,
                      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                      minimumSize: Size.zero,
                      tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
                    ),
                    child: const Text('Close ✕', style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold)),
                  ),
                ],
              ],
            ),
          ],
        ),
      ),
    );
  }
}
