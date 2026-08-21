import 'dart:async';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
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
  String? _errorMessage;
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

  static String _normalizeSym(String s) =>
      s.replaceAll('/', '').replaceAll('-', '').replaceAll('_', '').toUpperCase();

  static String _formatPrice(double? price) {
    if (price == null) return '-';
    if (price < 5.0) {
      return '\$${price.toStringAsFixed(4)}';
    }
    return '\$${price.toStringAsFixed(2)}';
  }

  void _startLiveTicker() {
    _liveTimer = Timer.periodic(const Duration(milliseconds: 1000), (timer) {
      if (!mounted) return;
      _fetchLivePrices();
      if (timer.tick % 5 == 0) {
        _fetchPositions();
      }
    });
  }

  Future<void> _fetchLivePrices() async {
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/signals/live-prices'));
      final prices = resp.data['prices'] as Map<String, dynamic>? ?? {};
      if (prices.isEmpty || !mounted) return;

      setState(() {
        for (var s in _signals) {
          final rawSym = s['symbol']?.toString() ?? '';
          final normSym = _normalizeSym(rawSym);
          for (var entry in prices.entries) {
            if (entry.key == rawSym || _normalizeSym(entry.key) == normSym) {
              final pData = entry.value as Map<String, dynamic>;
              final p = (pData['price'] as num?)?.toDouble();
              if (p != null && p > 0) {
                s['live_price'] = p;
              }
              break;
            }
          }
        }

        for (var p in _positions) {
          final rawSym = p['symbol']?.toString() ?? '';
          final normSym = _normalizeSym(rawSym);
          for (var entry in prices.entries) {
            if (entry.key == rawSym || _normalizeSym(entry.key) == normSym) {
              final pData = entry.value as Map<String, dynamic>;
              final price = (pData['price'] as num?)?.toDouble();
              if (price != null && price > 0) {
                p['live_price'] = price;
                final entryPrice = (p['entry'] as num?)?.toDouble() ?? price;
                final isLong = (p['direction'] ?? 'long').toString().toLowerCase() == 'long';
                final size = (p['position_size'] ?? p['size'] ?? 1.0) as num;
                p['live_pnl'] = isLong ? (price - entryPrice) * size.toDouble() : (entryPrice - price) * size.toDouble();
                p['live_pnl_pct'] = entryPrice > 0 ? (isLong ? (price - entryPrice) / entryPrice : (entryPrice - price) / entryPrice) * 100 : 0.0;
              }
              break;
            }
          }
        }
      });
    } catch (_) {}
  }

  Future<void> _fetchSignals() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/signals/'));
      final List<dynamic> list = resp.data['signals'] ?? [];
      setState(() {
        _signals = list.map((e) {
          final m = Map<String, dynamic>.from(e as Map);
          final rawSym = m['symbol']?.toString() ?? '';
          final normSym = _normalizeSym(rawSym);
          final old = _signals.firstWhere((x) => _normalizeSym(x['symbol'] ?? '') == normSym, orElse: () => {});
          final oldLive = (old['live_price'] as num?)?.toDouble();
          m['live_price'] = (m['live_price'] as num?)?.toDouble() ?? oldLive ?? (m['entry'] as num?)?.toDouble() ?? 100.0;
          return m;
        }).toList();
        _isLoading = false;
        _errorMessage = null;
      });
      _fetchLivePrices();
    } catch (e) {
      if (mounted) {
        setState(() {
          _isLoading = false;
          _errorMessage = 'ไม่สามารถเชื่อมต่อ Scanner Backend ได้ (${AppApi.baseUrl})';
        });
      }
    }
  }

  Future<void> _fetchPositions() async {
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/trades/'));
      final List<dynamic> list = resp.data['trades'] ?? [];
      if (mounted) {
        setState(() {
          _positions = list
              .map((e) {
                final m = Map<String, dynamic>.from(e as Map);
                final old = _positions.firstWhere((x) => x['id'] == m['id'], orElse: () => {});
                final oldLive = (old['live_price'] as num?)?.toDouble();
                final oldPnl = (old['live_pnl'] as num?)?.toDouble();
                final oldPnlPct = (old['live_pnl_pct'] as num?)?.toDouble();
                m['live_price'] = (m['live_price'] as num?)?.toDouble() ?? oldLive ?? (m['entry'] as num?)?.toDouble() ?? 0.0;
                m['live_pnl'] = (m['live_pnl'] as num?)?.toDouble() ?? oldPnl ?? 0.0;
                m['live_pnl_pct'] = (m['live_pnl_pct'] as num?)?.toDouble() ?? oldPnlPct ?? 0.0;
                return m;
              })
              .where((p) => (p['status'] ?? 'open') == 'open')
              .toList();
        });
      }
    } catch (_) {}
  }

  Future<void> _triggerScan() async {
    setState(() => _isScanning = true);
    try {
      final dio = AppApi.dio;
      final resp = await dio.post(
        AppApi.url('/api/v1/signals/scan'),
        options: Options(receiveTimeout: const Duration(seconds: 30)),
      );
      final List<dynamic> list = resp.data['signals'] ?? [];
      setState(() {
        _signals = list.map((e) {
          final m = Map<String, dynamic>.from(e as Map);
          m['live_price'] = (m['entry'] as num?)?.toDouble() ?? 100.0;
          return m;
        }).toList();
        _isScanning = false;
        _errorMessage = null;
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
    final sl = (signal['stop_loss'] as num?)?.toDouble() ?? (dir == 'long' ? entry * 0.992 : entry * 1.008);
    final tp = (signal['take_profit'] as num?)?.toDouble() ?? (dir == 'long' ? entry * 1.02 : entry * 0.98);
    final mType = (signal['market_type'] ?? 'crypto').toString().toLowerCase();
    final isStock = mType == 'stock';
    final isForex = mType == 'forex';
    final unitLabel = isStock ? 'Shares' : (isForex ? 'Lots' : sym.split('/').first);

    double defaultQty = isStock ? 5.0 : (isForex ? 0.10 : 0.10);
    double qtyStep = isStock ? 1.0 : (isForex ? 0.01 : 0.05);
    final List<double> presetChips = isStock
        ? [1.0, 5.0, 10.0, 50.0, 100.0]
        : (isForex ? [0.01, 0.05, 0.10, 0.50, 1.00] : [0.05, 0.10, 0.25, 0.50, 1.00]);

    final tag = '#${sym.replaceAll('/', '')}-${dir.toUpperCase()}-$_tagCounter';
    _tagCounter++;

    final qtyController = TextEditingController(text: defaultQty.toStringAsFixed(isStock ? 0 : 2));
    double selectedQty = defaultQty;

    final isConfirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (context, setDlgState) {
          final posVal = entry * selectedQty;
          final riskAmount = (entry - sl).abs() * selectedQty;
          final gainAmount = (tp - entry).abs() * selectedQty;

          return AlertDialog(
            backgroundColor: AppColors.surface,
            shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(14)),
            titlePadding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
            contentPadding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
            title: Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                    color: (dir == 'long' ? AppColors.bullish : AppColors.bearish).withValues(alpha: 0.2),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: Icon(
                    dir == 'long' ? Icons.arrow_upward : Icons.arrow_downward,
                    color: dir == 'long' ? AppColors.bullish : AppColors.bearish,
                    size: 18,
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    'ยืนยันส่งคำสั่ง $sym (${dir.toUpperCase()})',
                    style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold),
                  ),
                ),
              ],
            ),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'ระบบจะส่งคำสั่ง Paper Trade ตามการวิเคราะห์ของ SMC Engine:',
                    style: TextStyle(fontSize: 11, color: Colors.white70),
                  ),
                  const SizedBox(height: 10),
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF141926),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: AppColors.border),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        _dialogItem('Entry', _formatPrice(entry), Colors.white),
                        _dialogItem('Stop Loss', _formatPrice(sl), AppColors.bearish),
                        _dialogItem('Take Profit', _formatPrice(tp), AppColors.bullish),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Order Quantity Header
                  Text(
                    'ORDER QUANTITY ($unitLabel)',
                    style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: AppColors.textMuted, letterSpacing: 0.8),
                  ),
                  const SizedBox(height: 6),

                  // Quantity Control Row (- input +)
                  Row(
                    children: [
                      IconButton.filledTonal(
                        onPressed: () {
                          if (selectedQty > qtyStep) {
                            setDlgState(() {
                              selectedQty = (selectedQty - qtyStep);
                              if (selectedQty < qtyStep) selectedQty = qtyStep;
                              qtyController.text = selectedQty.toStringAsFixed(isStock ? 0 : 2);
                            });
                          }
                        },
                        icon: const Icon(Icons.remove, size: 16),
                        style: IconButton.styleFrom(
                          backgroundColor: const Color(0xFF252D3F),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                          minimumSize: const Size(36, 36),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Expanded(
                        child: Container(
                          height: 40,
                          alignment: Alignment.center,
                          decoration: BoxDecoration(
                            color: const Color(0xFF141926),
                            borderRadius: BorderRadius.circular(8),
                            border: Border.all(color: const Color(0xFF2E82FE).withValues(alpha: 0.5)),
                          ),
                          child: TextField(
                            controller: qtyController,
                            textAlign: TextAlign.center,
                            keyboardType: const TextInputType.numberWithOptions(decimal: true),
                            style: const TextStyle(fontSize: 15, fontWeight: FontWeight.bold, color: Colors.white, fontFamily: 'monospace'),
                            decoration: InputDecoration(
                              isDense: true,
                              contentPadding: const EdgeInsets.symmetric(horizontal: 6, vertical: 8),
                              border: InputBorder.none,
                              suffixText: unitLabel,
                              suffixStyle: const TextStyle(fontSize: 11, color: AppColors.textMuted),
                            ),
                            onChanged: (val) {
                              final parsed = double.tryParse(val);
                              if (parsed != null && parsed > 0) {
                                setDlgState(() => selectedQty = parsed);
                              }
                            },
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      IconButton.filledTonal(
                        onPressed: () {
                          setDlgState(() {
                            selectedQty = (selectedQty + qtyStep);
                            qtyController.text = selectedQty.toStringAsFixed(isStock ? 0 : 2);
                          });
                        },
                        icon: const Icon(Icons.add, size: 16),
                        style: IconButton.styleFrom(
                          backgroundColor: const Color(0xFF252D3F),
                          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                          minimumSize: const Size(36, 36),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),

                  // Quick Preset Chips
                  Wrap(
                    spacing: 6,
                    runSpacing: 4,
                    children: presetChips.map((preset) {
                      final isSelected = (selectedQty - preset).abs() < 0.001;
                      return GestureDetector(
                        onTap: () {
                          setDlgState(() {
                            selectedQty = preset;
                            qtyController.text = preset.toStringAsFixed(isStock ? 0 : (preset < 0.1 ? 2 : 2));
                          });
                        },
                        child: Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            color: isSelected ? const Color(0xFF2E82FE) : const Color(0xFF1E2533),
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(color: isSelected ? const Color(0xFF2E82FE) : AppColors.border),
                          ),
                          child: Text(
                            isStock ? preset.toStringAsFixed(0) : preset.toStringAsFixed(2),
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
                              color: isSelected ? Colors.white : AppColors.textMuted,
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 10),

                  // Live Risk & Value Bar
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    decoration: BoxDecoration(
                      color: const Color(0xFF141926),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                          'Value: \$${posVal.toStringAsFixed(posVal > 1000 ? 1 : 2)}',
                          style: const TextStyle(fontSize: 11, color: Colors.white70, fontFamily: 'monospace'),
                        ),
                        Text(
                          'Risk: -\$${riskAmount.toStringAsFixed(2)}',
                          style: const TextStyle(fontSize: 11, color: AppColors.bearish, fontWeight: FontWeight.bold, fontFamily: 'monospace'),
                        ),
                        Text(
                          'TP: +\$${gainAmount.toStringAsFixed(2)}',
                          style: const TextStyle(fontSize: 11, color: AppColors.bullish, fontWeight: FontWeight.bold, fontFamily: 'monospace'),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(ctx, false),
                child: const Text('ยกเลิก', style: TextStyle(color: Colors.white60)),
              ),
              ElevatedButton(
                style: ElevatedButton.styleFrom(
                  backgroundColor: dir == 'long' ? AppColors.bullish : AppColors.bearish,
                  foregroundColor: Colors.black,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                  shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
                ),
                onPressed: () => Navigator.pop(ctx, true),
                child: Text(
                  'Confirm Execute (${selectedQty.toStringAsFixed(isStock ? 0 : 2)})',
                  style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13),
                ),
              ),
            ],
          );
        },
      ),
    );

    qtyController.dispose();

    if (isConfirmed == true) {
      try {
        final dio = AppApi.dio;
        final resp = await dio.post(
          AppApi.url('/api/v1/trades/place'),
          data: {
            'symbol': sym,
            'direction': dir,
            'entry': entry,
            'stop_loss': sl,
            'take_profit': tp,
            'position_size': selectedQty,
            'size': selectedQty,
            'tag': tag,
          },
        );

        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              backgroundColor: AppColors.bullish,
              content: Text(
                '✅ ${resp.data['message'] ?? 'Trade executed successfully: $tag'}',
                style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold),
              ),
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

  Future<void> _closePosition(dynamic tradeId, String tag) async {
    try {
      final dio = AppApi.dio;
      await dio.post(
        AppApi.url('/api/v1/trades/$tradeId/close'),
        data: {'reason': 'manual'},
      );
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
        title: const FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: Text('Proactive SMC Scanner', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 18)),
        ),
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
          // Filter Bar + Scan Trigger Button (Horizontally scrollable with fixed Scan button)
          Container(
            color: AppColors.surface,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            child: Row(
              children: [
                Expanded(
                  child: SingleChildScrollView(
                    scrollDirection: Axis.horizontal,
                    child: Row(
                      children: [
                        _filterChip('ALL', 'all'),
                        const SizedBox(width: 6),
                        _filterChip('CRYPTO', 'crypto'),
                        const SizedBox(width: 6),
                        _filterChip('FOREX & GOLD', 'forex'),
                        const SizedBox(width: 6),
                        _filterChip('STOCKS', 'stock'),
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                InkWell(
                  onTap: _isScanning ? null : _triggerScan,
                  borderRadius: BorderRadius.circular(18),
                  child: Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    decoration: BoxDecoration(
                      color: AppColors.bullish,
                      borderRadius: BorderRadius.circular(18),
                    ),
                    child: Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (_isScanning)
                          const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.black))
                        else
                          const Icon(Icons.radar, size: 16, color: Colors.black),
                        const SizedBox(width: 4),
                        Text(
                          _isScanning ? '...' : 'Scan',
                          style: const TextStyle(fontSize: 12, fontWeight: FontWeight.bold, color: Colors.black),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
          ),
          const Divider(),
          Expanded(
            child: _isLoading
                ? const Center(child: CircularProgressIndicator(color: AppColors.bullish))
                : _errorMessage != null && _signals.isEmpty
                    ? _buildErrorBanner()
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
                            padding: const EdgeInsets.fromLTRB(12, 10, 12, 90),
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

                              final matchingPositions = _positions.where((p) => (p['symbol'] ?? '').toString().toUpperCase() == sym.toUpperCase()).toList();
                              final entryType = (s['entry_type'] ?? 'limit').toString();
                              final squeezeStatus = (s['squeeze_status'] ?? 'no_squeeze').toString();
                              final volumeDelta = (s['volume_delta'] as num?)?.toDouble() ?? 0.0;
                              final deltaAbsorption = s['delta_absorption'] == true;
                              final deltaStatus = (s['delta_status'] ?? '').toString();

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
                                entryType: entryType,
                                squeezeStatus: squeezeStatus,
                                volumeDelta: volumeDelta,
                                deltaAbsorption: deltaAbsorption,
                                deltaStatus: deltaStatus,
                                message: msg,
                                advice: s['advice'] as String?,
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

  Widget _buildErrorBanner() {
    return Center(
      child: Container(
        margin: const EdgeInsets.all(16),
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: const Color(0xFF221518),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: AppColors.bearish.withOpacity(0.4)),
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.wifi_off, color: AppColors.bearish, size: 36),
            const SizedBox(height: 10),
            const Text(
              'เชื่อมต่อเซิร์ฟเวอร์ไม่ได้',
              style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15),
            ),
            const SizedBox(height: 6),
            Text(
              'แอปพยายามเชื่อมต่อไปที่: ${AppApi.baseUrl}\nกรุณาตั้งค่า IP เครื่องคอมพิวเตอร์ในหน้า Settings หรือตรวจสอบว่าเปิด Backend อยู่',
              textAlign: TextAlign.center,
              style: const TextStyle(color: Colors.white70, fontSize: 12),
            ),
            const SizedBox(height: 14),
            Row(
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                ElevatedButton.icon(
                  onPressed: () => context.go('/settings'),
                  icon: const Icon(Icons.settings, size: 16),
                  label: const Text('ตั้งค่า IP'),
                  style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF2E82FE)),
                ),
                const SizedBox(width: 10),
                OutlinedButton.icon(
                  onPressed: () {
                    _fetchSignals();
                    _fetchPositions();
                  },
                  icon: const Icon(Icons.refresh, size: 16),
                  label: const Text('ลองใหม่'),
                  style: OutlinedButton.styleFrom(foregroundColor: Colors.white70),
                ),
              ],
            ),
          ],
        ),
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
  final String? advice;
  final int confluence;
  final double? entry, livePrice, sl, tp, rr;
  final String entryType;
  final String squeezeStatus;
  final double volumeDelta;
  final bool deltaAbsorption;
  final String deltaStatus;
  final List<Map<String, dynamic>> openPositions;
  final VoidCallback onExecuteTrade;
  final Function(dynamic id, String tag) onClosePosition;

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
    this.entryType = 'limit',
    this.squeezeStatus = 'no_squeeze',
    this.volumeDelta = 0.0,
    this.deltaAbsorption = false,
    this.deltaStatus = '',
    this.advice,
    this.entry,
    this.livePrice,
    this.sl,
    this.tp,
    this.rr,
  });

  String _getAdviceText(String? customAdvice, String direction, int confluence, bool isGradeA, bool isGradeB) {
    if (customAdvice != null && customAdvice.trim().isNotEmpty && customAdvice.contains('คำแนะนำ:')) {
      return customAdvice;
    }
    if (isGradeA) {
      return 'คำแนะนำ: โครงสร้างแข็งแกร่ง (Grade A+) สอดคล้องเทรนด์ใหญ่ แนะนำพิจารณาเข้าตามแผน Entry / SL ได้ทันที (ความเสี่ยง 1.0%)';
    } else if (isGradeB) {
      final isLong = direction == 'LONG';
      final zone = isLong ? 'Discount' : 'Premium';
      return 'คำแนะนำ: โครงสร้าง $direction (Grade B) แตะโซน $zone ควรรอแท่งยืนยัน Rejection ใน TF ย่อยก่อนเข้า หรือจำกัดความเสี่ยงที่ 0.5%';
    } else {
      return 'คำแนะนำ: รอยืนยันการเคลื่อนไหวของราคา แนะนำ "รอ (WAIT)" สัญญาณ CHoCH ยืนยันใน TF ย่อยก่อน';
    }
  }

  String _formatPrice(double? price) {
    if (price == null) return '-';
    if (price < 5.0) {
      return '\$${price.toStringAsFixed(4)}';
    }
    return '\$${price.toStringAsFixed(2)}';
  }

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
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    // Interactive Buy / Sell Button on the Direction Badge
                    ElevatedButton.icon(
                      onPressed: onExecuteTrade,
                      icon: Icon(
                        isLong ? Icons.arrow_upward : Icons.arrow_downward,
                        size: 13,
                        color: Colors.black,
                      ),
                      label: Text(
                        isLong ? 'BUY / LONG' : 'SELL / SHORT',
                        style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold, fontSize: 11),
                      ),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: color,
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                        minimumSize: Size.zero,
                        tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                      ),
                    ),
                    const SizedBox(width: 8),
                    Text(symbol, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16)),
                  ],
                ),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        if (squeezeStatus == 'squeeze_fire') ...[
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                            decoration: BoxDecoration(
                              color: AppColors.bullish.withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: AppColors.bullish.withValues(alpha: 0.8), width: 0.8),
                            ),
                            child: const Text('⚡ SQUEEZE FIRE', style: TextStyle(fontSize: 8, fontWeight: FontWeight.bold, color: AppColors.bullish)),
                          ),
                          const SizedBox(width: 4),
                        ] else if (squeezeStatus == 'squeeze_on') ...[
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                            decoration: BoxDecoration(
                              color: const Color(0xFFFF9900).withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: const Color(0xFFFF9900).withValues(alpha: 0.8), width: 0.8),
                            ),
                            child: const Text('⚫ SQUEEZING', style: TextStyle(fontSize: 8, fontWeight: FontWeight.bold, color: Color(0xFFFF9900))),
                          ),
                          const SizedBox(width: 4),
                        ],
                        if (deltaAbsorption) ...[
                          Container(
                            padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                            decoration: BoxDecoration(
                              color: const Color(0xFF00E5FF).withValues(alpha: 0.15),
                              borderRadius: BorderRadius.circular(4),
                              border: Border.all(color: const Color(0xFF00E5FF).withValues(alpha: 0.8), width: 0.8),
                            ),
                            child: const Text('🌊 ABSORPTION', style: TextStyle(fontSize: 8, fontWeight: FontWeight.bold, color: Color(0xFF00E5FF))),
                          ),
                          const SizedBox(width: 4),
                        ],
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: gradeColor.withValues(alpha: 0.15),
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(color: gradeColor.withValues(alpha: 0.6), width: 0.8),
                          ),
                          child: Text(
                            gradeText,
                            style: TextStyle(fontSize: 9, fontWeight: FontWeight.bold, color: gradeColor),
                          ),
                        ),
                        const SizedBox(width: 4),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: const Color(0xFF252540),
                            borderRadius: BorderRadius.circular(4),
                          ),
                          child: Text(timeframe, style: const TextStyle(fontSize: 10, color: Colors.white70, fontWeight: FontWeight.bold)),
                        ),
                      ],
                    ),
                    const SizedBox(height: 3),
                    Text(
                      time,
                      style: const TextStyle(fontSize: 10, color: Colors.white38, fontWeight: FontWeight.w500),
                    ),
                  ],
                ),
              ],
            ),
            const SizedBox(height: 8),
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
              decoration: BoxDecoration(
                color: const Color(0xFF332200),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: const Color(0xFFFF9900).withValues(alpha: 0.6)),
              ),
              child: Row(
                children: [
                  const Icon(Icons.info_outline, color: Color(0xFFFF9900), size: 15),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      _getAdviceText(advice, direction, confluence, isGradeA, isGradeB),
                      style: const TextStyle(
                        fontSize: 11,
                        color: Color(0xFFFFB84D),
                        fontWeight: FontWeight.w500,
                        height: 1.3,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            if (entry != null && sl != null && tp != null) ...[
              const SizedBox(height: 10),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 8),
                decoration: BoxDecoration(
                  color: const Color(0xFF141923),
                  borderRadius: BorderRadius.circular(6),
                ),
                child: Row(
                  children: [
                    // Entry Price + Real-time Live Price Tag
                    Expanded(
                      flex: 36,
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        children: [
                          Text(
                            entryType == 'limit' ? 'OB Zone / Live' : 'Market / Live',
                            style: TextStyle(
                              fontSize: 9,
                              color: entryType == 'limit' ? const Color(0xFF5CA3FF) : AppColors.textMuted,
                              fontWeight: entryType == 'limit' ? FontWeight.bold : FontWeight.normal,
                            ),
                          ),
                          const SizedBox(height: 3),
                          SizedBox(
                            height: 22,
                            child: FittedBox(
                              fit: BoxFit.scaleDown,
                              alignment: Alignment.center,
                              child: Row(
                                mainAxisSize: MainAxisSize.min,
                                crossAxisAlignment: CrossAxisAlignment.center,
                                children: [
                                  Text(
                                    _formatPrice(entry),
                                    style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white, fontFamily: 'monospace'),
                                  ),
                                  const SizedBox(width: 4),
                                  Container(
                                    padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                                    decoration: BoxDecoration(
                                      color: (isLong
                                              ? ((livePrice ?? entry!) >= entry! ? AppColors.bullish : AppColors.bearish)
                                              : ((livePrice ?? entry!) <= entry! ? AppColors.bullish : AppColors.bearish))
                                          .withValues(alpha: 0.2),
                                      borderRadius: BorderRadius.circular(4),
                                      border: Border.all(
                                        color: isLong
                                            ? ((livePrice ?? entry!) >= entry! ? AppColors.bullish : AppColors.bearish)
                                            : ((livePrice ?? entry!) <= entry! ? AppColors.bullish : AppColors.bearish),
                                        width: 0.8,
                                      ),
                                    ),
                                    child: Text(
                                      '● ${_formatPrice(livePrice ?? entry)}',
                                      style: TextStyle(
                                        fontSize: 11,
                                        fontWeight: FontWeight.bold,
                                        color: isLong
                                            ? ((livePrice ?? entry!) >= entry! ? AppColors.bullish : AppColors.bearish)
                                            : ((livePrice ?? entry!) <= entry! ? AppColors.bullish : AppColors.bearish),
                                        fontFamily: 'monospace',
                                      ),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    Expanded(
                      flex: 22,
                      child: _levelInfo('Stop Loss', _formatPrice(sl), AppColors.bearish),
                    ),
                    Expanded(
                      flex: 24,
                      child: _levelInfo('Take Profit', _formatPrice(tp), AppColors.bullish),
                    ),
                    Expanded(
                      flex: 18,
                      child: _levelInfo('R:R', '${rr ?? 2.2}R', AppColors.neutral),
                    ),
                  ],
                ),
              ),
            ],
            // Active Open Positions for this symbol
            if (openPositions.isNotEmpty) ...[
              const SizedBox(height: 8),
              ...openPositions.map((pos) {
                final rawTag = pos['tag']?.toString();
                final rawId = pos['id']?.toString() ?? '';
                final pTag = (rawTag != null && rawTag.isNotEmpty && !rawTag.startsWith('POS-'))
                    ? (rawTag.startsWith('#') ? rawTag : '#$rawTag')
                    : '#POS-${rawId.length > 8 ? rawId.substring(0, 8) : rawId}';
                final pEntry = (pos['entry'] as num?)?.toDouble() ?? 0.0;
                final pDir = (pos['direction'] ?? 'long').toString().toUpperCase();
                final size = (pos['position_size'] as num?)?.toDouble() ?? (pos['size'] as num?)?.toDouble() ?? 1.0;
                final cur = livePrice ?? pEntry;
                final pnl = (pDir == 'LONG' ? (cur - pEntry) : (pEntry - cur)) * size;
                final pnlPct = pEntry > 0 ? ((pDir == 'LONG' ? (cur - pEntry) : (pEntry - cur)) / pEntry) * 100 : 0.0;
                final isWin = pnl >= 0;
                final pCol = isWin ? AppColors.bullish : AppColors.bearish;

                return Container(
                  margin: const EdgeInsets.only(bottom: 4),
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 6),
                  decoration: BoxDecoration(
                    color: const Color(0xFF1B2333),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: const Color(0xFF00E5FF).withOpacity(0.4)),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Icon(Icons.bookmark_added, size: 13, color: Color(0xFF00E5FF)),
                          const SizedBox(width: 4),
                          Expanded(
                            child: Text(
                              '$pTag ($pDir) • Size: ${size.toStringAsFixed(size.truncateToDouble() == size ? 0 : 2)}',
                              style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Color(0xFF00E5FF)),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                          const SizedBox(width: 6),
                          FittedBox(
                            fit: BoxFit.scaleDown,
                            child: Text(
                              '${isWin ? '+' : ''}\$${pnl.toStringAsFixed(2)} (${isWin ? '+' : ''}${pnlPct.toStringAsFixed(2)}%)',
                              style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: pCol, fontFamily: 'monospace'),
                            ),
                          ),
                          const SizedBox(width: 6),
                          GestureDetector(
                            onTap: () => onClosePosition(pos['id'], pTag),
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
                      const SizedBox(height: 2),
                      Text(
                        'Entry: ${_formatPrice(pEntry)}  ➜  Live: ${_formatPrice(cur)}',
                        style: const TextStyle(fontSize: 10, color: Colors.white60, fontFamily: 'monospace'),
                      ),
                    ],
                  ),
                );
              }),
            ],
            const SizedBox(height: 10),
            Row(
              children: [
                const Text('Institutional Confluence: ', style: TextStyle(fontSize: 11, color: Colors.white38)),
                Text(
                  '$confluence/100',
                  style: TextStyle(fontSize: 11, color: gradeColor, fontWeight: FontWeight.bold),
                ),
                const Spacer(),
                const Text('Proactive Alert ✓', style: TextStyle(fontSize: 10, color: AppColors.bullish, fontWeight: FontWeight.bold)),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _levelInfo(String label, String val, Color col) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: const TextStyle(fontSize: 9, color: AppColors.textMuted)),
        const SizedBox(height: 3),
        SizedBox(
          height: 22,
          child: FittedBox(
            fit: BoxFit.scaleDown,
            alignment: Alignment.center,
            child: Text(
              val,
              style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: col, fontFamily: 'monospace'),
            ),
          ),
        ),
      ],
    );
  }
}
