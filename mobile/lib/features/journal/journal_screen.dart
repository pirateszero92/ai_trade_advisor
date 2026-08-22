import 'dart:async';
import 'package:flutter/material.dart';
import 'package:go_router/go_router.dart';
import '../../app/theme.dart';
import '../../core/api/api_client.dart';

class JournalScreen extends StatefulWidget {
  const JournalScreen({super.key});

  @override
  State<JournalScreen> createState() => _JournalScreenState();
}

class _JournalScreenState extends State<JournalScreen> {
  List<Map<String, dynamic>> _trades = [];
  Map<String, dynamic>? _accountInfo;
  bool _isLoading = true;
  String? _errorMessage;
  Timer? _liveTimer;
  int _selectedTab = 0; // 0: Open Positions, 1: Trade History
  String _historyFilter = 'all'; // 'all', 'win', 'loss'

  @override
  void initState() {
    super.initState();
    _fetchTrades();
    _fetchAccountInfo();
    _startLiveTicker();
  }

  @override
  void dispose() {
    _liveTimer?.cancel();
    super.dispose();
  }

  static String _normalizeSym(String s) =>
      s.replaceAll('/', '').replaceAll('-', '').replaceAll('_', '').toUpperCase();

  bool _isPriceFetching = false;

  void _startLiveTicker() {
    _liveTimer = Timer.periodic(const Duration(milliseconds: 1000), (timer) {
      if (!mounted) return;
      _fetchLivePrices();
      if (timer.tick % 5 == 0) {
        _fetchTradesSilently();
        _fetchAccountInfoSilently();
      }
    });
  }

  Future<void> _fetchLivePrices() async {
    if (_isPriceFetching) return;
    _isPriceFetching = true;
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/signals/live-prices'));
      final prices = resp.data['prices'] as Map<String, dynamic>? ?? {};
      if (prices.isEmpty || !mounted) return;

      setState(() {
        for (var t in _trades) {
          if (t['status'] == 'open') {
            final rawSym = t['symbol']?.toString() ?? '';
            final normSym = _normalizeSym(rawSym);
            for (var entry in prices.entries) {
              if (entry.key == rawSym || _normalizeSym(entry.key) == normSym) {
                final pData = entry.value as Map<String, dynamic>;
                final p = (pData['price'] as num?)?.toDouble();
                if (p != null && p > 0) {
                  t['live_price'] = p;
                  final entryPrice = (t['entry'] as num?)?.toDouble() ?? p;
                  final isLong = (t['direction'] ?? 'long').toString().toLowerCase() == 'long';
                  final size = (t['position_size'] ?? t['size'] ?? 1.0) as num;
                  final livePnl = isLong ? (p - entryPrice) * size.toDouble() : (entryPrice - p) * size.toDouble();
                  final livePnlPct = entryPrice > 0 ? (isLong ? (p - entryPrice) / entryPrice : (entryPrice - p) / entryPrice) * 100 : 0.0;
                  t['live_pnl'] = livePnl;
                  t['live_pnl_pct'] = livePnlPct;
                }
                break;
              }
            }
          }
        }
      });
    } catch (_) {
    } finally {
      _isPriceFetching = false;
    }
  }

  Future<void> _fetchAccountInfo() async {
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/trades/account'));
      if (mounted) {
        setState(() {
          _accountInfo = Map<String, dynamic>.from(resp.data);
        });
      }
    } catch (_) {}
  }

  Future<void> _fetchAccountInfoSilently() async {
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/trades/account'));
      if (mounted) {
        setState(() {
          _accountInfo = Map<String, dynamic>.from(resp.data);
        });
      }
    } catch (_) {}
  }

  Future<void> _fetchTrades() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/trades/'));
      final List<dynamic> list = resp.data['trades'] ?? [];
      if (!mounted) return;
      setState(() {
        _trades = list.map((e) {
          final m = Map<String, dynamic>.from(e as Map);
          final entry = (m['entry'] as num?)?.toDouble() ?? 100.0;
          final existing = _trades.firstWhere((x) => x['id'] == m['id'], orElse: () => {});
          final existingLive = (existing['live_price'] as num?)?.toDouble();
          final existingPnl = (existing['live_pnl'] as num?)?.toDouble();
          final existingPnlPct = (existing['live_pnl_pct'] as num?)?.toDouble();

          m['live_price'] = (m['live_price'] as num?)?.toDouble() ?? existingLive ?? entry;
          m['live_pnl'] = (m['live_pnl'] as num?)?.toDouble() ?? existingPnl ?? (m['pnl'] as num?)?.toDouble() ?? 0.0;
          m['live_pnl_pct'] = (m['live_pnl_pct'] as num?)?.toDouble() ?? existingPnlPct ?? (m['pnl_pct'] as num?)?.toDouble() ?? 0.0;
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
          _errorMessage = 'ไม่สามารถเชื่อมต่อ Backend API ได้ (${AppApi.baseUrl})';
        });
      }
    }
  }

  Future<void> _fetchTradesSilently() async {
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/trades/'));
      final List<dynamic> list = resp.data['trades'] ?? [];
      if (mounted) {
        setState(() {
          _trades = list.map((e) {
            final m = Map<String, dynamic>.from(e as Map);
            final entry = (m['entry'] as num?)?.toDouble() ?? 100.0;
            final existing = _trades.firstWhere((x) => x['id'] == m['id'], orElse: () => {});
            final existingLive = (existing['live_price'] as num?)?.toDouble();
            final existingPnl = (existing['live_pnl'] as num?)?.toDouble();
            final existingPnlPct = (existing['live_pnl_pct'] as num?)?.toDouble();

            m['live_price'] = (m['live_price'] as num?)?.toDouble() ?? existingLive ?? entry;
            m['live_pnl'] = (m['live_pnl'] as num?)?.toDouble() ?? existingPnl ?? (m['pnl'] as num?)?.toDouble() ?? 0.0;
            m['live_pnl_pct'] = (m['live_pnl_pct'] as num?)?.toDouble() ?? existingPnlPct ?? (m['pnl_pct'] as num?)?.toDouble() ?? 0.0;
            return m;
          }).toList();
        });
      }
    } catch (_) {}
  }

  Future<void> _closeTrade(String tradeId) async {
    try {
      final dio = AppApi.dio;
      final t = _trades.firstWhere((e) => e['id']?.toString() == tradeId, orElse: () => {});
      final closePrice = (t['live_price'] as num?)?.toDouble() ?? (t['entry'] as num?)?.toDouble() ?? 100.0;

      await dio.post(
        AppApi.url('/api/v1/trades/$tradeId/close'),
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
      _fetchAccountInfo();
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(backgroundColor: AppColors.bearish, content: Text('Failed to close: $e')),
        );
      }
    }
  }

  Future<void> _showResetPaperDialog(double currentCapital) async {
    final capCtrl = TextEditingController(text: currentCapital.toStringAsFixed(0));
    bool clearTrades = true;
    final presets = [10000.0, 50000.0, 100000.0, 250000.0, 500000.0, 1000000.0];

    try {
      await showDialog(
        context: context,
        builder: (dialogCtx) => StatefulBuilder(
        builder: (dialogCtx, setDlgState) => AlertDialog(
          backgroundColor: AppColors.surface,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
          title: const Row(
            children: [
              Icon(Icons.restart_alt, color: Color(0xFF00E5FF), size: 22),
              SizedBox(width: 8),
              Text('ตั้งค่าเงินต้น / Reset Portfolio', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
            ],
          ),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  'กำหนดจำนวนเงินต้นจำลอง (Initial Capital) สำหรับพอร์ต Paper Trading:',
                  style: TextStyle(color: Colors.white70, fontSize: 12, height: 1.4),
                ),
                const SizedBox(height: 12),
                TextField(
                  controller: capCtrl,
                  keyboardType: TextInputType.number,
                  style: const TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold),
                  decoration: const InputDecoration(
                    labelText: 'จำนวนเงินต้น (USD)',
                    prefixText: '\$ ',
                    prefixStyle: TextStyle(color: AppColors.bullish, fontWeight: FontWeight.bold),
                    hintText: '100000',
                  ),
                ),
                const SizedBox(height: 12),
                const Text('เลือกจำนวนเงินด่วน (Quick Presets):', style: TextStyle(color: Colors.white38, fontSize: 11)),
                const SizedBox(height: 6),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: presets.map((p) {
                    final isSel = (double.tryParse(capCtrl.text) ?? 0.0) == p;
                    return InkWell(
                      onTap: () {
                        setDlgState(() {
                          capCtrl.text = p.toStringAsFixed(0);
                        });
                      },
                      child: Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                        decoration: BoxDecoration(
                          color: isSel ? const Color(0xFF00E5FF).withOpacity(0.2) : const Color(0xFF252540),
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(color: isSel ? const Color(0xFF00E5FF) : Colors.white12),
                        ),
                        child: Text(
                          '\$${p >= 1000000 ? '${(p / 1000000).toStringAsFixed(0)}M' : '${(p / 1000).toStringAsFixed(0)}k'}',
                          style: TextStyle(fontSize: 11, color: isSel ? const Color(0xFF00E5FF) : Colors.white70, fontWeight: FontWeight.bold),
                        ),
                      ),
                    );
                  }).toList(),
                ),
                const SizedBox(height: 14),
                const Divider(color: Color(0xFF222B3D), height: 1),
                const SizedBox(height: 4),
                CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  value: clearTrades,
                  onChanged: (v) => setDlgState(() => clearTrades = v ?? true),
                  title: const Text('ล้างประวัติการเทรดทั้งหมด (Clear All Trades)', style: TextStyle(fontSize: 12, color: Colors.white70)),
                  controlAffinity: ListTileControlAffinity.leading,
                  activeColor: AppColors.bullish,
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(dialogCtx).pop(),
              child: const Text('ยกเลิก', style: TextStyle(color: Colors.white54)),
            ),
            ElevatedButton.icon(
              onPressed: () async {
                final amount = double.tryParse(capCtrl.text.replaceAll(',', '').trim()) ?? 100000.0;
                Navigator.of(dialogCtx).pop();
                try {
                  final dio = AppApi.dio;
                  await dio.post(
                    AppApi.url('/api/v1/trades/account/reset'),
                    data: {
                      'initial_capital': amount,
                      'clear_trades': clearTrades,
                      'currency': 'USD',
                    },
                  );
                  _fetchTrades();
                  _fetchAccountInfo();
                  if (mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        backgroundColor: AppColors.bullish,
                        content: Text('✅ Reset Paper Capital เป็น \$${amount.toStringAsFixed(2)} สำเร็จ!', style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
                      ),
                    );
                  }
                } catch (e) {
                  if (mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(backgroundColor: AppColors.bearish, content: Text('Reset failed: $e')),
                    );
                  }
                }
              },
              style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF00E5FF)),
              icon: const Icon(Icons.check, color: Colors.black, size: 16),
              label: const Text('ยืนยัน Reset', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
            ),
          ],
        ),
      ),
    );
    } finally {
      capCtrl.dispose();
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
    final isLandscape = MediaQuery.of(context).orientation == Orientation.landscape;

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        toolbarHeight: isLandscape ? 44 : 56,
        title: const FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: Text('Trade Journal & Performance', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 17)),
        ),
        backgroundColor: AppColors.surface,
        actions: [
          IconButton(
            icon: const Icon(Icons.refresh),
            tooltip: 'Refresh Journal',
            onPressed: () {
              _fetchTrades();
              _fetchAccountInfo();
            },
          ),
        ],
      ),
      body: _isLoading
          ? const Center(child: CircularProgressIndicator(color: AppColors.bullish))
          : _errorMessage != null && _trades.isEmpty
              ? _buildErrorBanner()
              : RefreshIndicator(
                  onRefresh: () async {
                    _fetchTrades();
                    _fetchAccountInfo();
                  },
                  color: AppColors.bullish,
                  child: ListView(
                    padding: EdgeInsets.fromLTRB(12, 6, 12, isLandscape ? 30 : 90),
                    children: [
                      // Header: In Landscape, 2 columns side-by-side! In Portrait, stacked vertically.
                      if (isLandscape)
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Expanded(
                              flex: 55,
                              child: _buildAccountPortfolioCard(realizedPnl, unrealizedPnl, isLandscape: true),
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              flex: 45,
                              child: _buildSummaryBar(winRate, realizedPnl, unrealizedPnl, openList.length, closed.length, isLandscape: true),
                            ),
                          ],
                        )
                      else ...[
                        _buildAccountPortfolioCard(realizedPnl, unrealizedPnl, isLandscape: false),
                        const SizedBox(height: 6),
                        _buildSummaryBar(winRate, realizedPnl, unrealizedPnl, openList.length, closed.length, isLandscape: false),
                      ],

                      const SizedBox(height: 10),

                      // Two Distinct Tabs: Open Positions vs Trade History
                      _buildTabsHeader(openList.length, closed.length),

                      const SizedBox(height: 8),

                      if (_selectedTab == 0) ...[
                        // Tab 0: Open Positions
                        if (openList.isEmpty)
                          Container(
                            padding: const EdgeInsets.symmetric(vertical: 36, horizontal: 20),
                            decoration: BoxDecoration(
                              color: const Color(0xFF141926),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(color: const Color(0xFF2E384D).withValues(alpha: 0.6)),
                            ),
                            child: Center(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  const Icon(Icons.check_circle_outline, size: 44, color: AppColors.bullish),
                                  const SizedBox(height: 10),
                                  const Text(
                                    'ไม่มีสถานะที่เปิดอยู่ขณะนี้ (No Open Positions)',
                                    style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 15),
                                  ),
                                  const SizedBox(height: 6),
                                  const Text(
                                    'พอร์ตของคุณกำลังถือเงินสด 100% รอสัญญาณ SMC Confluence คุณภาพสูง',
                                    textAlign: TextAlign.center,
                                    style: TextStyle(color: AppColors.textMuted, fontSize: 12),
                                  ),
                                  const SizedBox(height: 14),
                                  ElevatedButton.icon(
                                    onPressed: () => context.go('/signals'),
                                    icon: const Icon(Icons.bolt, size: 16, color: Colors.black),
                                    label: const Text('ดูสัญญาณเทรด SMC Signals →', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold, fontSize: 12)),
                                    style: ElevatedButton.styleFrom(
                                      backgroundColor: const Color(0xFF00E5FF),
                                      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          )
                        else
                          ...openList.map((t) => _buildTradeItem(t)),
                      ] else ...[
                        // Tab 1: Trade History (Closed Trades)
                        if (closed.isNotEmpty)
                          _buildHistoryFilterBar(closed.length, wins.length, closed.length - wins.length),

                        if (_getFilteredClosedTrades(closed).isEmpty)
                          Container(
                            padding: const EdgeInsets.symmetric(vertical: 36, horizontal: 20),
                            decoration: BoxDecoration(
                              color: const Color(0xFF141926),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(color: const Color(0xFF2E384D).withValues(alpha: 0.6)),
                            ),
                            child: const Center(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Icon(Icons.history_toggle_off, size: 44, color: Colors.white24),
                                  SizedBox(height: 10),
                                  Text(
                                    'ไม่พบประวัติการเทรดตามเงื่อนไขที่เลือก',
                                    style: TextStyle(color: Colors.white70, fontWeight: FontWeight.bold, fontSize: 14),
                                  ),
                                ],
                              ),
                            ),
                          )
                        else
                          ..._getFilteredClosedTrades(closed).map((t) => _buildTradeItem(t)),
                      ],
                    ],
                  ),
                ),
    );
  }

  List<Map<String, dynamic>> _getFilteredClosedTrades(List<Map<String, dynamic>> closed) {
    if (_historyFilter == 'win') {
      return closed.where((t) => ((t['pnl'] ?? 0) as num) > 0).toList();
    } else if (_historyFilter == 'loss') {
      return closed.where((t) => ((t['pnl'] ?? 0) as num) <= 0).toList();
    }
    return closed;
  }

  Widget _buildTradeItem(Map<String, dynamic> t) {
    final id = t['id']?.toString() ?? '';
    final tag = t['tag'] ?? '#POS-$id';
    final sym = t['symbol'] ?? 'BTC/USDT';
    final dir = (t['direction'] ?? 'long').toString().toUpperCase();
    final entry = (t['entry'] as num?)?.toDouble() ?? 0.0;
    final livePrice = (t['live_price'] as num?)?.toDouble() ?? entry;
    final size = (t['position_size'] ?? t['size'] ?? 1.0) as num;

    final status = t['status'] ?? 'open';
    final isOpen = status == 'open';
    final closePrice = (t['close_price'] as num?)?.toDouble() ?? 0.0;
    final closeReason = t['close_reason']?.toString() ?? '';

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
      closePrice: closePrice,
      closeReason: closeReason,
      size: size.toDouble(),
      pnl: pnlPct,
      pnlUsd: pnl,
      status: status,
      rr: 2.3,
      date: date,
      onClose: () => _closeTrade(id),
    );
  }

  Widget _buildTabsHeader(int openCount, int closedCount) {
    return Container(
      margin: const EdgeInsets.symmetric(vertical: 6),
      padding: const EdgeInsets.all(4),
      decoration: BoxDecoration(
        color: const Color(0xFF141926),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFF2E384D), width: 1),
      ),
      child: Row(
        children: [
          // Tab 0: Open Positions
          Expanded(
            child: InkWell(
              borderRadius: BorderRadius.circular(8),
              onTap: () => setState(() => _selectedTab = 0),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                padding: const EdgeInsets.symmetric(vertical: 9),
                decoration: BoxDecoration(
                  color: _selectedTab == 0 ? const Color(0xFF00E5FF).withValues(alpha: 0.15) : Colors.transparent,
                  borderRadius: BorderRadius.circular(8),
                  border: _selectedTab == 0 ? Border.all(color: const Color(0xFF00E5FF), width: 1.2) : null,
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      Icons.bolt,
                      size: 16,
                      color: _selectedTab == 0 ? const Color(0xFF00E5FF) : Colors.white54,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      'Open Positions',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.bold,
                        color: _selectedTab == 0 ? const Color(0xFF00E5FF) : Colors.white70,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                      decoration: BoxDecoration(
                        color: openCount > 0 ? AppColors.bullish.withValues(alpha: 0.25) : const Color(0xFF252540),
                        borderRadius: BorderRadius.circular(10),
                        border: openCount > 0 ? Border.all(color: AppColors.bullish.withValues(alpha: 0.6), width: 0.8) : null,
                      ),
                      child: Text(
                        '$openCount Active',
                        style: TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                          color: openCount > 0 ? AppColors.bullish : Colors.white54,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(width: 6),
          // Tab 1: Trade History
          Expanded(
            child: InkWell(
              borderRadius: BorderRadius.circular(8),
              onTap: () => setState(() => _selectedTab = 1),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                padding: const EdgeInsets.symmetric(vertical: 9),
                decoration: BoxDecoration(
                  color: _selectedTab == 1 ? const Color(0xFF5CA3FF).withValues(alpha: 0.15) : Colors.transparent,
                  borderRadius: BorderRadius.circular(8),
                  border: _selectedTab == 1 ? Border.all(color: const Color(0xFF5CA3FF), width: 1.2) : null,
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      Icons.history,
                      size: 16,
                      color: _selectedTab == 1 ? const Color(0xFF5CA3FF) : Colors.white54,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      'Trade History',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.bold,
                        color: _selectedTab == 1 ? const Color(0xFF5CA3FF) : Colors.white70,
                      ),
                    ),
                    const SizedBox(width: 6),
                    Container(
                      padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                      decoration: BoxDecoration(
                        color: const Color(0xFF252540),
                        borderRadius: BorderRadius.circular(10),
                      ),
                      child: Text(
                        '$closedCount Closed',
                        style: const TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                          color: Colors.white70,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildHistoryFilterBar(int totalClosed, int winCount, int lossCount) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8, top: 2),
      child: Row(
        children: [
          _buildFilterChip('ทั้งหมด ($totalClosed)', 'all'),
          const SizedBox(width: 6),
          _buildFilterChip('กำไร ($winCount) 🎯', 'win', activeColor: AppColors.bullish),
          const SizedBox(width: 6),
          _buildFilterChip('ขาดทุน/Invalid ($lossCount) 🛑', 'loss', activeColor: AppColors.bearish),
        ],
      ),
    );
  }

  Widget _buildFilterChip(String label, String key, {Color? activeColor}) {
    final isSelected = _historyFilter == key;
    final color = activeColor ?? const Color(0xFF5CA3FF);
    return InkWell(
      borderRadius: BorderRadius.circular(6),
      onTap: () => setState(() => _historyFilter = key),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: isSelected ? color.withValues(alpha: 0.15) : const Color(0xFF1C2333),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
            color: isSelected ? color : const Color(0xFF2E384D),
            width: isSelected ? 1.0 : 0.8,
          ),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 11,
            fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
            color: isSelected ? color : Colors.white60,
          ),
        ),
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
          border: Border.all(color: AppColors.bearish.withValues(alpha: 0.4)),
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
                    _fetchAccountInfo();
                    _fetchTrades();
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

  Widget _buildAccountPortfolioCard(double realizedPnl, double unrealizedPnl, {bool isLandscape = false}) {
    final acc = _accountInfo ?? {
      'broker': 'Paper Trading Portfolio',
      'account_id': 'PAPER-01',
      'status': 'ACTIVE',
      'initial_capital': 100000.0,
      'buying_power': 200000.0,
      'cash': 100000.0,
      'mode': 'paper',
    };

    final brokerName = acc['broker']?.toString() ?? 'Paper Trading';
    final accId = acc['account_id']?.toString() ?? 'PAPER-01';
    final initialCap = (acc['initial_capital'] as num?)?.toDouble() ?? 100000.0;
    final buyingPower = (acc['buying_power'] as num?)?.toDouble() ?? (initialCap * 2);
    final cash = (acc['cash'] as num?)?.toDouble() ?? initialCap;
    final mode = acc['mode']?.toString() ?? 'paper';
    final isLive = mode == 'live';

    final netWorth = initialCap + realizedPnl + unrealizedPnl;
    final totalPnl = realizedPnl + unrealizedPnl;
    final totalPnlPct = initialCap > 0 ? (totalPnl / initialCap) * 100 : 0.0;
    final isPnlPositive = totalPnl >= 0;

    return Container(
      margin: isLandscape ? EdgeInsets.zero : const EdgeInsets.fromLTRB(0, 4, 0, 0),
      padding: EdgeInsets.all(isLandscape ? 10 : 12),
      decoration: BoxDecoration(
        color: const Color(0xFF141926),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: isLive ? AppColors.bearish.withValues(alpha: 0.4) : const Color(0xFF2E384D),
          width: 1,
        ),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          // Header: Broker Icon + Name + Mode Badge
          Row(
            children: [
              Container(
                padding: const EdgeInsets.all(6),
                decoration: BoxDecoration(
                  color: isLive ? AppColors.bearish.withValues(alpha: 0.15) : AppColors.bullish.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Icon(
                  isLive ? Icons.bolt : Icons.account_balance_wallet_outlined,
                  color: isLive ? AppColors.bearish : AppColors.bullish,
                  size: 16,
                ),
              ),
              const SizedBox(width: 8),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      brokerName,
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: Colors.white),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(
                      'Account #$accId',
                      style: const TextStyle(fontSize: 10, color: Colors.white38, fontFamily: 'monospace'),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 6),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: BoxDecoration(
                  color: isLive ? AppColors.bearish.withValues(alpha: 0.2) : const Color(0xFF252540),
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(
                    color: isLive ? AppColors.bearish : const Color(0xFF00E5FF),
                    width: 0.8,
                  ),
                ),
                child: Text(
                  isLive ? '⚡ LIVE' : '🧪 PAPER',
                  style: TextStyle(
                    fontSize: 9,
                    fontWeight: FontWeight.bold,
                    color: isLive ? AppColors.bearish : const Color(0xFF00E5FF),
                  ),
                ),
              ),
            ],
          ),

          const SizedBox(height: 6),

          // Row 2: Reset & Configure Button (if paper)
          if (!isLive) ...[
            InkWell(
              onTap: () => _showResetPaperDialog(initialCap),
              borderRadius: BorderRadius.circular(6),
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: const Color(0xFF00E5FF).withValues(alpha: 0.08),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: const Color(0xFF00E5FF).withValues(alpha: 0.4), width: 0.8),
                ),
                child: const Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(Icons.tune, color: Color(0xFF00E5FF), size: 12),
                    SizedBox(width: 4),
                    Text('ตั้งค่าเงินต้น / Reset Portfolio', style: TextStyle(fontSize: 10, color: Color(0xFF00E5FF), fontWeight: FontWeight.bold)),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 6),
          ],

          // Row 3: 3 Column Financial Metrics (Initial Cap, Balance, Total PnL) with Expanded
          Row(
            children: [
              Expanded(
                child: _accountStat('Initial Cap', '\$${_formatCurrency(initialCap)}', Colors.white70),
              ),
              Expanded(
                child: _accountStat('Balance', '\$${_formatCurrency(netWorth)}', Colors.white),
              ),
              Expanded(
                child: _accountStat(
                  'Total PnL',
                  '${isPnlPositive ? '+' : ''}\$${_formatCurrency(totalPnl)} (${isPnlPositive ? '+' : ''}${totalPnlPct.toStringAsFixed(1)}%)',
                  isPnlPositive ? AppColors.bullish : AppColors.bearish,
                ),
              ),
            ],
          ),

          const SizedBox(height: 6),
          const Divider(color: Color(0xFF222B3D), height: 1),
          const SizedBox(height: 4),

          // Row 4: Buying Power & Cash
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  const Icon(Icons.flash_on, size: 11, color: Color(0xFFFFD700)),
                  const SizedBox(width: 2),
                  Text(
                    'Buying Power: \$${_formatCurrency(buyingPower)}',
                    style: const TextStyle(fontSize: 9, color: Colors.white54, fontFamily: 'monospace'),
                  ),
                ],
              ),
              Text(
                'Cash: \$${_formatCurrency(cash)}',
                style: const TextStyle(fontSize: 9, color: Colors.white38, fontFamily: 'monospace'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _accountStat(String label, String value, Color valueColor) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: const TextStyle(fontSize: 9, color: Colors.white38),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        const SizedBox(height: 1),
        Text(
          value,
          style: TextStyle(
            fontSize: 10,
            fontWeight: FontWeight.bold,
            color: valueColor,
            fontFamily: 'monospace',
          ),
          maxLines: 2,
          overflow: TextOverflow.ellipsis,
        ),
      ],
    );
  }

  String _formatCurrency(double val) {
    if (val.abs() >= 1000000) {
      return '${(val / 1000000).toStringAsFixed(2)}M';
    }
    return val.toStringAsFixed(2).replaceAllMapped(
          RegExp(r'(\d{1,3})(?=(\d{3})+(?!\d))'),
          (Match m) => '${m[1]},',
        );
  }

  Widget _buildSummaryBar(String winRate, double realizedPnl, double unrealizedPnl, int openCount, int closedCount, {bool isLandscape = false}) {
    final isRealPos = realizedPnl >= 0;
    final isUnrealPos = unrealizedPnl >= 0;

    if (isLandscape) {
      return Container(
        padding: const EdgeInsets.all(10),
        decoration: BoxDecoration(
          color: const Color(0xFF141926),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: const Color(0xFF2E384D), width: 1),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Row(
              children: [
                Icon(Icons.analytics_outlined, color: AppColors.bullish, size: 15),
                SizedBox(width: 6),
                Text('Performance Overview', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 11, color: Colors.white)),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(child: _stat('Win Rate', '$winRate%', AppColors.bullish)),
                Expanded(child: _stat('Open Orders', '$openCount Active', const Color(0xFF00E5FF))),
              ],
            ),
            const SizedBox(height: 6),
            const Divider(color: Color(0xFF222B3D), height: 1),
            const SizedBox(height: 6),
            Row(
              children: [
                Expanded(
                  child: _stat(
                    'Unrealized PnL',
                    '${isUnrealPos ? '+' : ''}\$${unrealizedPnl.toStringAsFixed(2)}',
                    isUnrealPos ? AppColors.bullish : AppColors.bearish,
                  ),
                ),
                Expanded(
                  child: _stat(
                    'Realized PnL',
                    '${isRealPos ? '+' : ''}\$${realizedPnl.toStringAsFixed(2)}',
                    isRealPos ? AppColors.bullish : AppColors.bearish,
                  ),
                ),
              ],
            ),
          ],
        ),
      );
    }

    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
      decoration: BoxDecoration(
        color: const Color(0xFF141926),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFF2E384D).withValues(alpha: 0.6)),
      ),
      child: Row(
        children: [
          Expanded(child: _stat('Win Rate', '$winRate%', AppColors.bullish)),
          Expanded(child: _stat('Open Orders', '$openCount Active', const Color(0xFF00E5FF))),
          Expanded(
            child: _stat(
              'Unrealized',
              '${isUnrealPos ? '+' : ''}\$${unrealizedPnl.toStringAsFixed(2)}',
              isUnrealPos ? AppColors.bullish : AppColors.bearish,
            ),
          ),
          Expanded(
            child: _stat(
              'Realized',
              '${isRealPos ? '+' : ''}\$${realizedPnl.toStringAsFixed(2)}',
              isRealPos ? AppColors.bullish : AppColors.bearish,
            ),
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
        Text(value, style: TextStyle(fontSize: 12, color: color, fontWeight: FontWeight.bold, fontFamily: 'monospace')),
      ],
    );
  }
}

class _TradeCard extends StatelessWidget {
  final String id, tag, symbol, direction, date, status, closeReason;
  final double entry, livePrice, closePrice, size, pnl, pnlUsd, rr;
  final VoidCallback onClose;

  const _TradeCard({
    required this.id,
    required this.tag,
    required this.symbol,
    required this.direction,
    required this.entry,
    required this.livePrice,
    required this.closePrice,
    required this.closeReason,
    required this.size,
    required this.pnl,
    required this.pnlUsd,
    required this.status,
    required this.rr,
    required this.date,
    required this.onClose,
  });

  String _formatPrice(double price, String sym) {
    final s = sym.toUpperCase();
    final isForex = s.contains('EURUSD') || s.contains('GBPUSD') || s.contains('USDJPY') || s.contains('AUDUSD') || s.contains('USDCAD');
    if (isForex) {
      return price.toStringAsFixed(4);
    }
    if (price.abs() >= 1000) {
      return price.toStringAsFixed(2).replaceAllMapped(
            RegExp(r'(\d{1,3})(?=(\d{3})+(?!\d))'),
            (Match m) => '${m[1]},',
          );
    }
    return price.toStringAsFixed(2);
  }

  @override
  Widget build(BuildContext context) {
    final isWin = pnlUsd >= 0;
    final isOpen = status == 'open';
    final isLong = direction == 'LONG';
    final sideColor = isLong ? AppColors.bullish : AppColors.bearish;
    final pnlColor = isWin ? AppColors.bullish : AppColors.bearish;

    // Determine status badge text and color
    String statusLabel = '● OPEN';
    Color statusColor = const Color(0xFF00E5FF);
    if (!isOpen) {
      if (closeReason.contains('TP')) {
        statusLabel = '🎯 TP HIT';
        statusColor = AppColors.bullish;
      } else if (closeReason.contains('SL')) {
        statusLabel = '🛑 SL HIT';
        statusColor = AppColors.bearish;
      } else if (closeReason.toLowerCase().contains('invalidation') || closeReason.toLowerCase().contains('invalid')) {
        statusLabel = '⚠️ INVALIDATED';
        statusColor = const Color(0xFFFF9900);
      } else {
        statusLabel = 'CLOSED';
        statusColor = Colors.white54;
      }
    }

    final exitP = closePrice > 0 ? closePrice : livePrice;

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          children: [
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Expanded(
                  child: Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      // Direction Badge
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                        decoration: BoxDecoration(
                          color: sideColor.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(color: sideColor, width: 0.8),
                        ),
                        child: Text(direction, style: TextStyle(color: sideColor, fontWeight: FontWeight.bold, fontSize: 11)),
                      ),
                      const SizedBox(width: 8),
                      Flexible(
                        child: Text(
                          symbol,
                          style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 15),
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                      const SizedBox(width: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: statusColor.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(color: statusColor.withValues(alpha: 0.5), width: 0.8),
                        ),
                        child: Text(
                          statusLabel,
                          style: TextStyle(
                            fontSize: 10,
                            fontWeight: FontWeight.bold,
                            color: statusColor,
                          ),
                        ),
                      ),
                    ],
                  ),
                ),
                const SizedBox(width: 8),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: pnlColor.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: pnlColor, width: 0.8),
                  ),
                  child: Text(
                    '${isWin ? '+' : ''}\$${pnlUsd.toStringAsFixed(2)} (${isWin ? '+' : ''}${pnl.toStringAsFixed(2)}%)',
                    style: TextStyle(color: pnlColor, fontWeight: FontWeight.bold, fontSize: 11, fontFamily: 'monospace'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: Text(
                    '$tag  •  Size: $size',
                    style: const TextStyle(fontSize: 11, color: Color(0xFF93C5FD), fontWeight: FontWeight.w600),
                    maxLines: 1,
                    overflow: TextOverflow.ellipsis,
                  ),
                ),
                const SizedBox(width: 8),
                Text(date, style: const TextStyle(fontSize: 10, color: Colors.white38)),
              ],
            ),
            const SizedBox(height: 4),
            Row(
              children: [
                Expanded(
                  child: Text(
                    isOpen
                        ? 'Entry: \$${_formatPrice(entry, symbol)}  ➜  Live: \$${_formatPrice(livePrice, symbol)}'
                        : 'Entry: \$${_formatPrice(entry, symbol)}  ➜  Exit: \$${_formatPrice(exitP, symbol)}${closeReason.isNotEmpty ? ' ($closeReason)' : ''}',
                    style: const TextStyle(fontSize: 11, color: Colors.white70, fontFamily: 'monospace'),
                  ),
                ),
                if (isOpen) ...[
                  const SizedBox(width: 8),
                  InkWell(
                    onTap: onClose,
                    child: Container(
                      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                      decoration: BoxDecoration(
                        color: AppColors.bearish.withOpacity(0.2),
                        borderRadius: BorderRadius.circular(4),
                        border: Border.all(color: AppColors.bearish, width: 0.8),
                      ),
                      child: const Text('Close ✕', style: TextStyle(color: AppColors.bearish, fontSize: 11, fontWeight: FontWeight.bold)),
                    ),
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
