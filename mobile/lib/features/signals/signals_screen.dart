import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';

class SignalsScreen extends StatefulWidget {
  const SignalsScreen({super.key});

  @override
  State<SignalsScreen> createState() => _SignalsScreenState();
}

class _SignalsScreenState extends State<SignalsScreen> {
  List<Map<String, dynamic>> _signals = [];
  bool _isLoading = true;
  bool _isScanning = false;
  String _selectedFilter = 'all';

  @override
  void initState() {
    super.initState();
    _fetchSignals();
  }

  Future<void> _fetchSignals() async {
    setState(() => _isLoading = true);
    try {
      final dio = Dio();
      final resp = await dio.get('http://127.0.0.1:8000/api/v1/signals/');
      final List<dynamic> list = resp.data['signals'] ?? [];
      setState(() {
        _signals = list.map((e) => Map<String, dynamic>.from(e as Map)).toList();
        _isLoading = false;
      });
    } catch (e) {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _triggerScan() async {
    setState(() => _isScanning = true);
    try {
      final dio = Dio();
      final resp = await dio.post('http://127.0.0.1:8000/api/v1/signals/scan');
      final List<dynamic> list = resp.data['signals'] ?? [];
      setState(() {
        _signals = list.map((e) => Map<String, dynamic>.from(e as Map)).toList();
        _isScanning = false;
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text('✅ ${resp.data['message'] ?? 'Scan complete'}', style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
          ),
        );
      }
    } catch (e) {
      setState(() => _isScanning = false);
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(backgroundColor: AppColors.bearish, content: Text('Scan failed: $e')),
        );
      }
    }
  }

  List<Map<String, dynamic>> get _filteredSignals {
    if (_selectedFilter == 'all') return _signals;
    return _signals.where((s) => (s['market_type'] ?? '') == _selectedFilter).toList();
  }

  @override
  Widget build(BuildContext context) {
    final filtered = _filteredSignals;

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: const Text('Proactive SMC Scanner'),
        backgroundColor: AppColors.surface,
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh Signals',
            onPressed: _fetchSignals,
          ),
        ],
      ),
      body: Column(
        children: [
          // Filter Bar + Scan Trigger Button
          Container(
            color: AppColors.surface,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            child: Row(
              children: [
                _filterChip('ALL', 'all'),
                const SizedBox(width: 6),
                _filterChip('CRYPTO', 'crypto'),
                const SizedBox(width: 6),
                _filterChip('FOREX & GOLD', 'forex'),
                const SizedBox(width: 6),
                _filterChip('STOCKS', 'stock'),
                const Spacer(),
                ElevatedButton.icon(
                  onPressed: _isScanning ? null : _triggerScan,
                  icon: _isScanning
                      ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black))
                      : const Icon(Icons.radar, size: 16),
                  label: Text(_isScanning ? 'Scanning...' : 'Scan Now', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppColors.bullish,
                    foregroundColor: Colors.black,
                    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  ),
                ),
              ],
            ),
          ),
          const Divider(),
          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator(color: AppColors.bullish))
                : filtered.isEmpty
                    ? Center(
                        child: Column(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            const Icon(Icons.radar, size: 48, color: AppColors.textMuted),
                            const SizedBox(height: 12),
                            const Text('No SMC setups detected in current regime.', style: TextStyle(color: Colors.white70)),
                            const SizedBox(height: 8),
                            const Text('Click "Scan Now" to scan all markets proactively.', style: TextStyle(fontSize: 12, color: AppColors.textMuted)),
                            const SizedBox(height: 16),
                            ElevatedButton.icon(
                              onPressed: _triggerScan,
                              icon: const Icon(Icons.refresh),
                              label: const Text('Scan Markets'),
                            ),
                          ],
                        ),
                      )
                    : ListView.builder(
                        padding: const EdgeInsets.all(12),
                        itemCount: filtered.length,
                        itemBuilder: (ctx, i) {
                          final s = filtered[i];
                          final sym = s['symbol'] ?? 'BTC/USDT';
                          final dir = (s['direction'] ?? 'LONG').toString().toUpperCase();
                          final tf = s['timeframe'] ?? '1H';
                          final confluence = (s['confluence'] as num?)?.toInt() ?? 80;
                          final msg = s['message'] ?? '';
                          final entry = (s['entry'] as num?)?.toDouble();
                          final sl = (s['stop_loss'] as num?)?.toDouble();
                          final tp = (s['take_profit'] as num?)?.toDouble();
                          final rr = (s['rr'] as num?)?.toDouble() ?? 2.2;
                          final date = (s['timestamp'] ?? '').toString().split('T').first;

                          return _SignalCard(
                            symbol: sym,
                            direction: dir,
                            timeframe: tf,
                            confluence: confluence,
                            entry: entry,
                            sl: sl,
                            tp: tp,
                            rr: rr,
                            message: msg,
                            time: date,
                          );
                        },
                      ),
          ),
        ],
      ),
    );
  }

  Widget _filterChip(String title, String key) {
    final isSel = _selectedFilter == key;
    return GestureDetector(
      onTap: () => setState(() => _selectedFilter = key),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: isSel ? const Color(0xFF2E82FE).withOpacity(0.2) : const Color(0xFF1E2533),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: isSel ? const Color(0xFF2E82FE) : AppColors.border),
        ),
        child: Text(
          title,
          style: TextStyle(
            fontSize: 11,
            fontWeight: isSel ? FontWeight.bold : FontWeight.normal,
            color: isSel ? Colors.white : AppColors.textMuted,
          ),
        ),
      ),
    );
  }
}

class _SignalCard extends StatelessWidget {
  final String symbol, direction, timeframe, message, time;
  final int confluence;
  final double? entry, sl, tp, rr;

  const _SignalCard({
    required this.symbol,
    required this.direction,
    required this.timeframe,
    required this.confluence,
    required this.message,
    required this.time,
    this.entry,
    this.sl,
    this.tp,
    this.rr,
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
                Text(symbol, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: const Color(0xFF252540),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Text(timeframe, style: const TextStyle(fontSize: 11, color: Colors.white70, fontWeight: FontWeight.bold)),
                ),
                const SizedBox(width: 8),
                Text(time, style: const TextStyle(fontSize: 11, color: Colors.white38)),
              ],
            ),
            const SizedBox(height: 10),
            Text(message, style: const TextStyle(fontSize: 13, color: Colors.white70, height: 1.4)),
            if (entry != null && sl != null && tp != null) ...[
              const SizedBox(height: 10),
              Container(
                padding: const EdgeInsets.all(8),
                decoration: BoxDecoration(
                  color: const Color(0xFF141923),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceAround,
                  children: [
                    _levelInfo('Entry', '\$${entry!.toStringAsFixed(2)}', Colors.white),
                    _levelInfo('Stop Loss', '\$${sl!.toStringAsFixed(2)}', AppColors.bearish),
                    _levelInfo('Take Profit', '\$${tp!.toStringAsFixed(2)}', AppColors.bullish),
                    _levelInfo('R:R', '${rr ?? 2.2}R', AppColors.neutral),
                  ],
                ),
              ),
            ],
            const SizedBox(height: 10),
            Row(
              children: [
                const Text('Institutional Confluence: ', style: TextStyle(fontSize: 12, color: Colors.white38)),
                Text(
                  '$confluence/100',
                  style: TextStyle(fontSize: 12, color: color, fontWeight: FontWeight.bold),
                ),
                const Spacer(),
                const Text('Proactive Alert ✓', style: TextStyle(fontSize: 11, color: AppColors.bullish, fontWeight: FontWeight.bold)),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _levelInfo(String label, String val, Color col) {
    return Column(
      children: [
        Text(label, style: const TextStyle(fontSize: 9, color: AppColors.textMuted)),
        const SizedBox(height: 2),
        Text(val, style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: col, fontFamily: 'monospace')),
      ],
    );
  }
}
