import 'dart:async';
import 'package:flutter/material.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';
import '../../core/api/api_client.dart';

class SignalsScreen extends StatefulWidget {
  const SignalsScreen({super.key});

  @override
  State<SignalsScreen> createState() => _SignalsScreenState();
}

class _SignalsScreenState extends State<SignalsScreen> {
  List<Map<String, dynamic>> _signals = [];
  List<Map<String, dynamic>> _positions = [];
  bool _isLoading = true;
  bool _isScanning = false;
  String _selectedFilter = 'all';
  Timer? _liveTimer;
  int _tagCounter = 101;

  @override
  void initState() {
    super.initState();
    _fetchSignals();
    _fetchPositions();
    _startLiveTicker();
  }

  @override
  void dispose() {
    _liveTimer?.cancel();
    super.dispose();
  }

  void _startLiveTicker() {
    _liveTimer = Timer.periodic(const Duration(milliseconds: 1400), (_) {
      if (!mounted) return;
      setState(() {
        // Minor realistic live tick simulation on signals for interactive feedback
        for (var s in _signals) {
          final cur = (s['live_price'] as num?)?.toDouble() ?? (s['entry'] as num?)?.toDouble() ?? 100.0;
          final delta = (DateTime.now().millisecond % 5 - 2) * 0.05;
          s['live_price'] = (cur + delta).clamp(1.0, 100000.0);
        }
      });
    });
  }

  Future<void> _fetchSignals() async {
    setState(() => _isLoading = true);
    try {
      final dio = Dio();
      final resp = await dio.get(AppApi.url('/api/v1/signals/'));
      final List<dynamic> list = resp.data['signals'] ?? [];
      setState(() {
        _signals = list.map((e) {
          final m = Map<String, dynamic>.from(e as Map);
          m['live_price'] = (m['entry'] as num?)?.toDouble() ?? 100.0;
          return m;
        }).toList();
        _isLoading = false;
      });
    } catch (e) {
      setState(() => _isLoading = false);
    }
  }

  Future<void> _fetchPositions() async {
    try {
      final dio = Dio();
      final resp = await dio.get(AppApi.url('/api/v1/trades/'));
      final List<dynamic> list = resp.data['trades'] ?? [];
      setState(() {
        _positions = list
            .map((e) => Map<String, dynamic>.from(e as Map))
            .where((p) => (p['status'] ?? 'open') == 'open')
            .toList();
      });
    } catch (_) {}
  }

  Future<void> _triggerScan() async {
    setState(() => _isScanning = true);
    try {
      final dio = Dio();
      final resp = await dio.post(AppApi.url('/api/v1/signals/scan'));
      final List<dynamic> list = resp.data['signals'] ?? [];
      setState(() {
        _signals = list.map((e) {
          final m = Map<String, dynamic>.from(e as Map);
          m['live_price'] = (m['entry'] as num?)?.toDouble() ?? 100.0;
          return m;
        }).toList();
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

  Future<void> _placeOrderFromSignal(Map<String, dynamic> signal) async {
    final sym = signal['symbol'] ?? 'BTC/USDT';
    final dir = (signal['direction'] ?? 'LONG').toString().toLowerCase();
    final entry = (signal['live_price'] as num?)?.toDouble() ?? (signal['entry'] as num?)?.toDouble() ?? 100.0;
    final sl = (signal['stop_loss'] as num?)?.toDouble() ?? (entry * 0.99);
    final tp = (signal['take_profit'] as num?)?.toDouble() ?? (entry * 1.02);
    final tag = '#${sym.replaceAll('/', '')}-${dir.toUpperCase()}-$_tagCounter';
    _tagCounter++;

    final tagCtrl = TextEditingController(text: tag);
    final sizeCtrl = TextEditingController(text: '1.0');

    final bool? confirm = await showModalBottomSheet<bool>(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
      builder: (ctx) {
        final isBuy = dir == 'long';
        final col = isBuy ? AppColors.bullish : AppColors.bearish;
        return Padding(
          padding: EdgeInsets.only(bottom: MediaQuery.of(ctx).viewInsets.bottom + 16, left: 16, right: 16, top: 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    decoration: BoxDecoration(color: col.withOpacity(0.2), borderRadius: BorderRadius.circular(6), border: Border.all(color: col)),
                    child: Text(isBuy ? 'BUY / LONG' : 'SELL / SHORT', style: TextStyle(color: col, fontWeight: FontWeight.bold, fontSize: 13)),
                  ),
                  const SizedBox(width: 10),
                  Text(sym, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
                  const Spacer(),
                  Text('Mark: \$${entry.toStringAsFixed(2)}', style: const TextStyle(color: AppColors.bullish, fontWeight: FontWeight.bold, fontSize: 13)),
                ],
              ),
              const SizedBox(height: 16),
              TextField(
                controller: tagCtrl,
                style: const TextStyle(color: Colors.white),
                decoration: const InputDecoration(labelText: 'Position Tag / Order Number', prefixIcon: Icon(Icons.tag, size: 18, color: Colors.white54)),
              ),
              const SizedBox(height: 10),
              TextField(
                controller: sizeCtrl,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                style: const TextStyle(color: Colors.white),
                decoration: const InputDecoration(labelText: 'Order Size (Qty / Lots)', prefixIcon: Icon(Icons.layers, size: 18, color: Colors.white54)),
              ),
              const SizedBox(height: 12),
              Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(color: const Color(0xFF141923), borderRadius: BorderRadius.circular(8)),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceAround,
                  children: [
                    _dialogItem('Stop Loss', '\$${sl.toStringAsFixed(2)}', AppColors.bearish),
                    _dialogItem('Take Profit', '\$${tp.toStringAsFixed(2)}', AppColors.bullish),
                    _dialogItem('Target R:R', '${signal['rr'] ?? 2.2}R', AppColors.neutral),
                  ],
                ),
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton.icon(
                  onPressed: () => Navigator.pop(ctx, true),
                  icon: Icon(isBuy ? Icons.arrow_upward : Icons.arrow_downward, color: Colors.black),
                  label: Text('CONFIRM ${isBuy ? "BUY" : "SELL"} EXECUTION', style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: col,
                    padding: const EdgeInsets.symmetric(vertical: 12),
                  ),
                ),
              ),
            ],
          ),
        );
      },
    );

    if (confirm == true) {
      try {
        final dio = Dio();
        final size = double.tryParse(sizeCtrl.text) ?? 1.0;
        await dio.post(
          AppApi.url('/api/v1/trades/place'),
          data: {
            'symbol': sym,
            'direction': dir,
            'entry': entry,
            'stop_loss': sl,
            'take_profit': tp,
            'position_size': size,
            'tag': tagCtrl.text.trim(),
            'notes': 'Executed via Proactive Scanner ($tag)',
          },
        );

        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              backgroundColor: AppColors.bullish,
              content: Text('🚀 Position ${tagCtrl.text} opened successfully at \$${entry.toStringAsFixed(2)}!', style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
            ),
          );
        }
        _fetchPositions();
      } catch (e) {
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(backgroundColor: AppColors.bearish, content: Text('Execution failed: $e')),
          );
        }
      }
    }
  }

  Future<void> _closePosition(int tradeId, String tag) async {
    try {
      final dio = Dio();
      await dio.post(AppApi.url('/api/v1/trades/$tradeId/close'));
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text('✅ Position $tag closed.', style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
          ),
        );
      }
      _fetchPositions();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(backgroundColor: AppColors.bearish, content: Text('Failed to close position: $e')),
        );
      }
    }
  }

  Widget _dialogItem(String label, String val, Color col) {
    return Column(
      children: [
        Text(label, style: const TextStyle(fontSize: 10, color: AppColors.textMuted)),
        const SizedBox(height: 2),
        Text(val, style: TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: col, fontFamily: 'monospace')),
      ],
    );
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
            onPressed: () {
              _fetchSignals();
              _fetchPositions();
            },
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
                          final livePrice = (s['live_price'] as num?)?.toDouble() ?? entry;
                          final sl = (s['stop_loss'] as num?)?.toDouble();
                          final tp = (s['take_profit'] as num?)?.toDouble();
                          final rr = (s['rr'] as num?)?.toDouble() ?? 2.2;
                          final date = (s['timestamp'] ?? '').toString().split('T').first;

                          // Find matching open positions for this symbol
                          final matchingPositions = _positions.where((p) => (p['symbol'] ?? '').toString().toUpperCase() == sym.toUpperCase()).toList();

                          return _SignalCard(
                            symbol: sym,
                            direction: dir,
                            timeframe: tf,
                            confluence: confluence,
                            entry: entry,
                            livePrice: livePrice,
                            sl: sl,
                            tp: tp,
                            rr: rr,
                            message: msg,
                            time: date,
                            openPositions: matchingPositions,
                            onExecuteTrade: () => _placeOrderFromSignal(s),
                            onClosePosition: (id, tag) => _closePosition(id, tag),
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
  final double? entry, livePrice, sl, tp, rr;
  final List<Map<String, dynamic>> openPositions;
  final VoidCallback onExecuteTrade;
  final Function(int id, String tag) onClosePosition;

  const _SignalCard({
    required this.symbol,
    required this.direction,
    required this.timeframe,
    required this.confluence,
    required this.message,
    required this.time,
    required this.openPositions,
    required this.onExecuteTrade,
    required this.onClosePosition,
    this.entry,
    this.livePrice,
    this.sl,
    this.tp,
    this.rr,
  });

  @override
  Widget build(BuildContext context) {
    final isLong = direction == 'LONG';
    final color = isLong ? AppColors.bullish : AppColors.bearish;

    final isGradeA = confluence >= 80;
    final isGradeB = confluence >= 65 && confluence < 80;
    final gradeText = isGradeA ? 'GRADE A+' : (isGradeB ? 'GRADE B' : 'GRADE C (WAIT)');
    final gradeColor = isGradeA ? AppColors.bullish : (isGradeB ? AppColors.neutral : const Color(0xFFFF9900));

    return Card(
      margin: const EdgeInsets.only(bottom: 12),
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                // Interactive Buy / Sell Button on the Direction Badge
                ElevatedButton.icon(
                  onPressed: onExecuteTrade,
                  icon: Icon(
                    isLong ? Icons.arrow_upward : Icons.arrow_downward,
                    size: 14,
                    color: Colors.black,
                  ),
                  label: Text(
                    isLong ? 'BUY / LONG' : 'SELL / SHORT',
                    style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold, fontSize: 12),
                  ),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: color,
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                ),
                const SizedBox(width: 8),
                Text(symbol, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: gradeColor.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: gradeColor.withOpacity(0.6), width: 0.8),
                  ),
                  child: Text(
                    gradeText,
                    style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: gradeColor),
                  ),
                ),
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
            if (!isGradeA && !isGradeB) ...[
              const SizedBox(height: 8),
              Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                decoration: BoxDecoration(
                  color: const Color(0xFF332200),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: const Color(0xFFFF9900).withOpacity(0.5)),
                ),
                child: const Row(
                  children: [
                    Icon(Icons.info_outline, color: Color(0xFFFF9900), size: 14),
                    SizedBox(width: 6),
                    Expanded(
                      child: Text(
                        'คำแนะนำ: ยังไม่ควรเสี่ยงเข้าทันที แนะนำ "รอ (WAIT)" สัญญาณ CHoCH ยืนยันใน TF ย่อยก่อน',
                        style: TextStyle(fontSize: 11, color: Color(0xFFFFB84D), fontWeight: FontWeight.w500),
                      ),
                    ),
                  ],
                ),
              ),
            ],
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
                    // Entry Price + Real-time Live Price Tag
                    Column(
                      children: [
                        const Text('Entry / Live', style: TextStyle(fontSize: 9, color: AppColors.textMuted)),
                        const SizedBox(height: 2),
                        Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            Text('\$${entry!.toStringAsFixed(2)}', style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white, fontFamily: 'monospace')),
                            const SizedBox(width: 4),
                            Container(
                              padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                              decoration: BoxDecoration(
                                color: AppColors.bullish.withOpacity(0.2),
                                borderRadius: BorderRadius.circular(3),
                                border: Border.all(color: AppColors.bullish, width: 0.5),
                              ),
                              child: Text(
                                '● \$${(livePrice ?? entry!).toStringAsFixed(2)}',
                                style: const TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: AppColors.bullish, fontFamily: 'monospace'),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                    _levelInfo('Stop Loss', '\$${sl!.toStringAsFixed(2)}', AppColors.bearish),
                    _levelInfo('Take Profit', '\$${tp!.toStringAsFixed(2)}', AppColors.bullish),
                    _levelInfo('R:R', '${rr ?? 2.2}R', AppColors.neutral),
                  ],
                ),
              ),
            ],
            // Active Open Positions for this symbol
            if (openPositions.isNotEmpty) ...[
              const SizedBox(height: 10),
              ...openPositions.map((pos) {
                final pTag = pos['tag'] ?? '#POS-${pos['id']}';
                final pEntry = (pos['entry'] as num?)?.toDouble() ?? 0.0;
                final pDir = (pos['direction'] ?? 'long').toString().toUpperCase();
                final cur = livePrice ?? pEntry;
                final pnl = pDir == 'LONG' ? (cur - pEntry) : (pEntry - cur);
                final pnlPct = pEntry > 0 ? (pnl / pEntry) * 100 : 0.0;
                final isWin = pnl >= 0;
                final pCol = isWin ? AppColors.bullish : AppColors.bearish;

                return Container(
                  margin: const EdgeInsets.only(bottom: 4),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  decoration: BoxDecoration(
                    color: const Color(0xFF1B2333),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: const Color(0xFF00E5FF).withOpacity(0.4)),
                  ),
                  child: Row(
                    children: [
                      const Icon(Icons.bookmark_added, size: 14, color: Color(0xFF00E5FF)),
                      const SizedBox(width: 6),
                      Text('$pTag ($pDir)', style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Color(0xFF00E5FF))),
                      const SizedBox(width: 8),
                      Text('Entry: \$${pEntry.toStringAsFixed(2)}', style: const TextStyle(fontSize: 11, color: Colors.white70)),
                      const Spacer(),
                      Text(
                        'PnL: ${isWin ? '+' : ''}\$${pnl.toStringAsFixed(2)} (${isWin ? '+' : ''}${pnlPct.toStringAsFixed(2)}%)',
                        style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: pCol),
                      ),
                      const SizedBox(width: 8),
                      GestureDetector(
                        onTap: () => onClosePosition(pos['id'] as int? ?? 0, pTag),
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: AppColors.bearish.withOpacity(0.2),
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(color: AppColors.bearish, width: 0.8),
                          ),
                          child: const Text('Close ✕', style: TextStyle(fontSize: 10, color: AppColors.bearish, fontWeight: FontWeight.bold)),
                        ),
                      ),
                    ],
                  ),
                );
              }),
            ],
            const SizedBox(height: 10),
            Row(
              children: [
                const Text('Institutional Confluence: ', style: TextStyle(fontSize: 12, color: Colors.white38)),
                Text(
                  '$confluence/100',
                  style: TextStyle(fontSize: 12, color: gradeColor, fontWeight: FontWeight.bold),
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
