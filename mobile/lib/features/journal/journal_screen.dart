import 'dart:async';
import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../../app/theme.dart';
import '../../core/api/api_client.dart';
import '../../core/api/ws_client.dart';
import '../settings/settings_screen.dart';

class JournalScreen extends ConsumerStatefulWidget {
  const JournalScreen({super.key});

  @override
  ConsumerState<JournalScreen> createState() => _JournalScreenState();
}

class _JournalScreenState extends ConsumerState<JournalScreen> {
  List<Map<String, dynamic>> _trades = [];
  Map<String, dynamic>? _accountInfo;
  bool _isLoading = true;
  String? _errorMessage;
  Timer? _liveTimer;
  int _selectedTab = 0; // 0: Open Positions, 1: Pending, 2: Trade History
  String _historyFilter = 'all'; // 'all', 'win', 'loss'
  String _activeProfileMode = 'paper'; // 'live' or 'paper'
  String _selectedBroker =
      'all'; // 'all', 'innovestx', 'mt5', 'binance', 'alpaca'
  Map<String, dynamic>? _scorecardData;
  final Set<String> _closingTradeIds = {};

  StreamSubscription<Map<String, dynamic>>? _wsPriceSub;
  StreamSubscription<Map<String, dynamic>>? _wsTradeSub;
  StreamSubscription<WsConnectionState>? _wsStateSub;
  WsConnectionState _wsState = WsConnectionState.disconnected;

  @override
  void initState() {
    super.initState();
    final isPaper = ref.read(settingsProvider).isPaperMode;
    _activeProfileMode = isPaper ? 'paper' : 'live';

    _wsState = AppWebSocketClient.instance.currentState;
    _wsStateSub =
        AppWebSocketClient.instance.connectionStateStream.listen((state) {
      if (mounted) setState(() => _wsState = state);
    });
    _wsPriceSub =
        AppWebSocketClient.instance.priceStream.listen(_onWsPriceTick);
    _wsTradeSub =
        AppWebSocketClient.instance.tradeStream.listen(_onWsTradeUpdate);

    _fetchAccountInfo();
    _fetchTrades();
    _fetchScorecard();
    _startLiveTicker();
  }

  void _onWsPriceTick(Map<String, dynamic> ticks) {
    if (!mounted || ticks.isEmpty) return;
    bool hasChanges = false;
    for (var entry in ticks.entries) {
      final val = entry.value;
      if (val is Map<String, dynamic>) {
        final p = (val['price'] as num?)?.toDouble();
        if (p != null && p > 0) {
          final norm = _normalizeSym(entry.key);
          for (var t in _trades) {
            if (_normalizeSym(t['symbol']?.toString() ?? '') == norm) {
              if (t['live_price'] != p) {
                hasChanges = true;
                t['live_price'] = p;
                final entryPrice = (t['entry'] as num?)?.toDouble() ?? p;
                final isLong =
                    (t['direction'] ?? 'long').toString().toLowerCase() ==
                        'long';
                final size = (t['position_size'] ?? t['size'] ?? 1.0) as num;
                if (t['status'] == 'open') {
                  final livePnl = isLong
                      ? (p - entryPrice) * size.toDouble()
                      : (entryPrice - p) * size.toDouble();
                  final livePnlPct = entryPrice > 0
                      ? (isLong
                              ? (p - entryPrice) / entryPrice
                              : (entryPrice - p) / entryPrice) *
                          100
                      : 0.0;
                  t['live_pnl'] = livePnl;
                  t['live_pnl_pct'] = livePnlPct;
                }
              }
            }
          }
        }
      }
    }
    if (hasChanges && mounted) {
      setState(() {});
    }
  }

  void _onWsTradeUpdate(Map<String, dynamic> msg) {
    if (!mounted) return;
    _fetchTradesSilently();
    _fetchAccountInfoSilently();
    _fetchScorecard();
  }

  Future<void> _fetchScorecard() async {
    try {
      final resp =
          await AppApi.dio.get(AppApi.url('/api/v1/journal/scorecard'));
      if (resp.statusCode == 200 && mounted) {
        setState(() {
          _scorecardData = Map<String, dynamic>.from(resp.data);
        });
      }
    } catch (_) {}
  }

  @override
  void dispose() {
    _wsPriceSub?.cancel();
    _wsTradeSub?.cancel();
    _wsStateSub?.cancel();
    _liveTimer?.cancel();
    super.dispose();
  }

  static String _normalizeSym(String s) => s
      .replaceAll('/', '')
      .replaceAll('-', '')
      .replaceAll('_', '')
      .toUpperCase();

  bool _isPriceFetching = false;

  void _startLiveTicker() {
    _liveTimer = Timer.periodic(const Duration(seconds: 5), (timer) {
      if (!mounted) return;
      if (_wsState != WsConnectionState.connected) {
        _fetchLivePrices();
      }
      // Periodic reconciliation covers missed WS frames without continuous polling.
      if (_wsState != WsConnectionState.connected || timer.tick % 6 == 0) {
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

      final priceMap = <String, double>{};
      for (var entry in prices.entries) {
        final pData = entry.value as Map<String, dynamic>;
        final p = (pData['price'] as num?)?.toDouble();
        if (p != null && p > 0) {
          priceMap[_normalizeSym(entry.key)] = p;
        }
      }

      bool hasChanges = false;
      for (var t in _trades) {
        if (t['status'] == 'open' || t['status'] == 'pending') {
          final normSym = _normalizeSym(t['symbol']?.toString() ?? '');
          final p = priceMap[normSym];
          if (p != null && p > 0 && t['live_price'] != p) {
            hasChanges = true;
            t['live_price'] = p;
            final entryPrice = (t['entry'] as num?)?.toDouble() ?? p;
            final isLong =
                (t['direction'] ?? 'long').toString().toLowerCase() == 'long';
            final size = (t['position_size'] ?? t['size'] ?? 1.0) as num;
            if (t['status'] == 'open') {
              final livePnl = isLong
                  ? (p - entryPrice) * size.toDouble()
                  : (entryPrice - p) * size.toDouble();
              final livePnlPct = entryPrice > 0
                  ? (isLong
                          ? (p - entryPrice) / entryPrice
                          : (entryPrice - p) / entryPrice) *
                      100
                  : 0.0;
              t['live_pnl'] = livePnl;
              t['live_pnl_pct'] = livePnlPct;
            }
          }
        }
      }

      if (hasChanges && mounted) {
        setState(() {});
      }
    } catch (_) {
    } finally {
      _isPriceFetching = false;
    }
  }

  Future<void> _fetchAccountInfo({String? mode, String? broker}) async {
    try {
      final dio = AppApi.dio;
      final effMode = mode ?? _activeProfileMode;
      final effBroker = broker ?? _selectedBroker;
      final q = <String, dynamic>{};
      if (effMode == 'live' && effBroker != 'all') {
        q['broker'] = effBroker;
      }
      final accountPath =
          effMode == 'live' ? '/api/v1/live/account' : '/api/v1/paper/account';
      final resp = await dio.get(AppApi.url(accountPath), queryParameters: q);
      if (mounted) {
        setState(() {
          _accountInfo = Map<String, dynamic>.from(resp.data);
          if (mode != null) {
            _activeProfileMode = mode;
          }
        });
      }
    } catch (_) {}
  }

  Future<void> _fetchAccountInfoSilently() async {
    try {
      final dio = AppApi.dio;
      final q = <String, dynamic>{};
      if (_activeProfileMode == 'live' && _selectedBroker != 'all') {
        q['broker'] = _selectedBroker;
      }
      final accountPath = _activeProfileMode == 'live'
          ? '/api/v1/live/account'
          : '/api/v1/paper/account';
      final resp = await dio.get(AppApi.url(accountPath), queryParameters: q);
      if (mounted) {
        setState(() {
          _accountInfo = Map<String, dynamic>.from(resp.data);
        });
      }
    } catch (_) {}
  }

  Future<void> _fetchTrades({String? mode, String? broker}) async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });
    try {
      final dio = AppApi.dio;
      final effMode = mode ?? _activeProfileMode;
      final effBroker = broker ?? _selectedBroker;
      final q = <String, dynamic>{};
      if (effMode == 'live' && effBroker != 'all') {
        q['mode'] = 'live';
        q['broker'] = effBroker;
      }
      if (effMode == 'live' && effBroker == 'all') q['mode'] = 'live';
      final ordersPath =
          effMode == 'live' ? '/api/v1/trades/' : '/api/v1/paper/orders';
      final resp = await dio.get(AppApi.url(ordersPath), queryParameters: q);
      final List<dynamic> list = resp.data['trades'] ?? [];
      if (!mounted) return;

      final existingMap = <String, Map<String, dynamic>>{
        for (final t in _trades)
          if (t['id'] != null) t['id'].toString(): t
      };

      setState(() {
        _trades = list.map((e) {
          final m = Map<String, dynamic>.from(e as Map);
          final existing = existingMap[m['id']?.toString()] ?? {};
          final existingLive = (existing['live_price'] as num?)?.toDouble();
          final existingPnl = (existing['live_pnl'] as num?)?.toDouble();
          final existingPnlPct = (existing['live_pnl_pct'] as num?)?.toDouble();

          m['live_price'] =
              (m['live_price'] as num?)?.toDouble() ?? existingLive;
          m['live_pnl'] = (m['live_pnl'] as num?)?.toDouble() ??
              existingPnl ??
              (m['pnl'] as num?)?.toDouble() ??
              0.0;
          m['live_pnl_pct'] = (m['live_pnl_pct'] as num?)?.toDouble() ??
              existingPnlPct ??
              (m['pnl_pct'] as num?)?.toDouble() ??
              0.0;
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
          _errorMessage =
              'ไม่สามารถเชื่อมต่อ Backend API ได้ (${AppApi.baseUrl})';
        });
      }
    }
  }

  Future<void> _fetchTradesSilently() async {
    try {
      final dio = AppApi.dio;
      final q = <String, dynamic>{};
      if (_activeProfileMode == 'live' && _selectedBroker != 'all') {
        q['mode'] = 'live';
        q['broker'] = _selectedBroker;
      }
      if (_activeProfileMode == 'live' && _selectedBroker == 'all') {
        q['mode'] = 'live';
      }
      final ordersPath = _activeProfileMode == 'live'
          ? '/api/v1/trades/'
          : '/api/v1/paper/orders';
      final resp = await dio.get(AppApi.url(ordersPath), queryParameters: q);
      final List<dynamic> list = resp.data['trades'] ?? [];
      if (mounted) {
        final existingMap = <String, Map<String, dynamic>>{
          for (final t in _trades)
            if (t['id'] != null) t['id'].toString(): t
        };

        setState(() {
          _trades = list.map((e) {
            final m = Map<String, dynamic>.from(e as Map);
            final existing = existingMap[m['id']?.toString()] ?? {};
            final existingLive = (existing['live_price'] as num?)?.toDouble();
            final existingPnl = (existing['live_pnl'] as num?)?.toDouble();
            final existingPnlPct =
                (existing['live_pnl_pct'] as num?)?.toDouble();

            m['live_price'] =
                (m['live_price'] as num?)?.toDouble() ?? existingLive;
            m['live_pnl'] = (m['live_pnl'] as num?)?.toDouble() ??
                existingPnl ??
                (m['pnl'] as num?)?.toDouble() ??
                0.0;
            m['live_pnl_pct'] = (m['live_pnl_pct'] as num?)?.toDouble() ??
                existingPnlPct ??
                (m['pnl_pct'] as num?)?.toDouble() ??
                0.0;
            return m;
          }).toList();
        });
      }
    } catch (_) {}
  }

  Future<void> _closeTrade(String tradeId) async {
    if (_closingTradeIds.contains(tradeId)) return;
    if (_activeProfileMode == 'live') {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          backgroundColor: AppColors.bearish,
          content: Text(
              'Live position ต้องปิดผ่าน Broker OMS; Journal จะไม่แก้สถานะ Live ในเครื่อง'),
        ),
      );
      return;
    }
    if (mounted) setState(() => _closingTradeIds.add(tradeId));
    try {
      final dio = AppApi.dio;
      final t = _trades.firstWhere((e) => e['id']?.toString() == tradeId,
          orElse: () => {});
      final isPending = t['status'] == 'pending';
      final sym = t['symbol']?.toString() ?? '';
      final closePrice = (t['live_price'] as num?)?.toDouble();
      if (!isPending && (closePrice == null || closePrice <= 0)) {
        throw StateError(
            'Current market price is unavailable. Refresh quotes before closing.');
      }

      await dio.post(
        AppApi.url('/api/v1/paper/orders/$tradeId/close'),
        data: isPending
            ? {'reason': 'Order Cancelled'}
            : {
                'close_price': closePrice,
                'reason': 'Manual Close from Journal'
              },
      );

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor:
                isPending ? const Color(0xFFFF9900) : AppColors.bullish,
            content: Text(
              isPending
                  ? '🗑️ Cancelled Pending Order $sym'
                  : '✅ Closed position @ \$${closePrice!.toStringAsFixed(2)}',
              style: const TextStyle(
                  color: Colors.black, fontWeight: FontWeight.bold),
            ),
          ),
        );
      }
      await Future.wait([_fetchTrades(), _fetchAccountInfo()]);
    } catch (e) {
      String msg = e.toString();
      if (e is DioException && e.response?.data != null) {
        final data = e.response!.data;
        if (data is Map && data.containsKey('detail')) {
          msg = data['detail'].toString();
        }
      }
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              backgroundColor: AppColors.bearish,
              content: Text('Failed: $msg')),
        );
      }
    } finally {
      if (mounted) setState(() => _closingTradeIds.remove(tradeId));
    }
  }

  Future<void> _showResetPaperDialog(double currentCapital) async {
    final capCtrl =
        TextEditingController(text: currentCapital.toStringAsFixed(0));
    bool clearTrades = true;
    final presets = [10000.0, 50000.0, 100000.0, 250000.0, 500000.0, 1000000.0];

    try {
      await showDialog(
        context: context,
        builder: (dialogCtx) => StatefulBuilder(
          builder: (dialogCtx, setDlgState) => AlertDialog(
            backgroundColor: AppColors.surface,
            shape:
                RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
            title: const Row(
              children: [
                Icon(Icons.restart_alt, color: Color(0xFF00E5FF), size: 22),
                SizedBox(width: 8),
                Text('ตั้งค่าเงินต้น / Reset Portfolio',
                    style: TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.bold)),
              ],
            ),
            content: SingleChildScrollView(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    'กำหนดจำนวนเงินต้นจำลอง (Initial Capital) สำหรับพอร์ต Paper Trading:',
                    style: TextStyle(
                        color: Colors.white70, fontSize: 12, height: 1.4),
                  ),
                  const SizedBox(height: 12),
                  TextField(
                    controller: capCtrl,
                    keyboardType: TextInputType.number,
                    style: const TextStyle(
                        color: Colors.white,
                        fontSize: 16,
                        fontWeight: FontWeight.bold),
                    decoration: const InputDecoration(
                      labelText: 'จำนวนเงินต้น (USD)',
                      prefixText: '\$ ',
                      prefixStyle: TextStyle(
                          color: AppColors.bullish,
                          fontWeight: FontWeight.bold),
                      hintText: '100000',
                    ),
                  ),
                  const SizedBox(height: 12),
                  const Text('เลือกจำนวนเงินด่วน (Quick Presets):',
                      style: TextStyle(color: Colors.white38, fontSize: 11)),
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
                          padding: const EdgeInsets.symmetric(
                              horizontal: 8, vertical: 4),
                          decoration: BoxDecoration(
                            color: isSel
                                ? const Color(0xFF00E5FF).withValues(alpha: 0.2)
                                : const Color(0xFF252540),
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(
                                color: isSel
                                    ? const Color(0xFF00E5FF)
                                    : Colors.white12),
                          ),
                          child: Text(
                            '\$${p >= 1000000 ? '${(p / 1000000).toStringAsFixed(0)}M' : '${(p / 1000).toStringAsFixed(0)}k'}',
                            style: TextStyle(
                                fontSize: 11,
                                color: isSel
                                    ? const Color(0xFF00E5FF)
                                    : Colors.white70,
                                fontWeight: FontWeight.bold),
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
                    onChanged: (v) =>
                        setDlgState(() => clearTrades = v ?? true),
                    title: const Text(
                        'ล้างประวัติการเทรดทั้งหมด (Clear All Trades)',
                        style: TextStyle(fontSize: 12, color: Colors.white70)),
                    controlAffinity: ListTileControlAffinity.leading,
                    activeColor: AppColors.bullish,
                  ),
                ],
              ),
            ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialogCtx).pop(),
                child: const Text('ยกเลิก',
                    style: TextStyle(color: Colors.white54)),
              ),
              ElevatedButton.icon(
                onPressed: () async {
                  final amount = double.tryParse(
                          capCtrl.text.replaceAll(',', '').trim()) ??
                      100000.0;
                  Navigator.of(dialogCtx).pop();
                  try {
                    final dio = AppApi.dio;
                    await dio.post(
                      AppApi.url('/api/v1/paper/account/reset'),
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
                          content: Text(
                              '✅ Reset Paper Capital เป็น \$${amount.toStringAsFixed(2)} สำเร็จ!',
                              style: const TextStyle(
                                  color: Colors.black,
                                  fontWeight: FontWeight.bold)),
                        ),
                      );
                    }
                  } catch (e) {
                    if (mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                            backgroundColor: AppColors.bearish,
                            content: Text('Reset failed: $e')),
                      );
                    }
                  }
                },
                style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF00E5FF)),
                icon: const Icon(Icons.check, color: Colors.black, size: 16),
                label: const Text('ยืนยัน Reset',
                    style: TextStyle(
                        color: Colors.black, fontWeight: FontWeight.bold)),
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
    final pendingList = _trades.where((t) => t['status'] == 'pending').toList();

    final wins = closed
        .where((t) => ((t['pnl'] as num?)?.toDouble() ?? 0.0) > 0)
        .toList();
    final winRate = closed.isNotEmpty
        ? ((wins.length / closed.length) * 100).toStringAsFixed(0)
        : '0';
    final realizedPnl = closed.fold(
        0.0, (acc, t) => acc + ((t['pnl'] as num?)?.toDouble() ?? 0.0));
    final unrealizedPnl = openList.fold(
        0.0, (acc, t) => acc + ((t['live_pnl'] as num?)?.toDouble() ?? 0.0));
    final isLandscape =
        MediaQuery.of(context).orientation == Orientation.landscape;

    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        toolbarHeight: isLandscape ? 44 : 56,
        title: const FittedBox(
          fit: BoxFit.scaleDown,
          alignment: Alignment.centerLeft,
          child: Text('Trade Journal & Performance',
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 17)),
        ),
        backgroundColor: AppColors.surface,
        actions: [
          // Live WebSocket Status Badge
          Container(
            margin: const EdgeInsets.symmetric(vertical: 12),
            padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
            decoration: BoxDecoration(
              color: _wsState == WsConnectionState.connected
                  ? AppColors.bullish.withValues(alpha: 0.15)
                  : const Color(0xFFFFD700).withValues(alpha: 0.15),
              borderRadius: BorderRadius.circular(6),
              border: Border.all(
                color: _wsState == WsConnectionState.connected
                    ? AppColors.bullish.withValues(alpha: 0.6)
                    : const Color(0xFFFFD700).withValues(alpha: 0.6),
                width: 0.8,
              ),
            ),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 6,
                  height: 6,
                  decoration: BoxDecoration(
                    color: _wsState == WsConnectionState.connected
                        ? AppColors.bullish
                        : const Color(0xFFFFD700),
                    shape: BoxShape.circle,
                  ),
                ),
                const SizedBox(width: 4),
                Text(
                  _wsState == WsConnectionState.connected ? '⚡ WS' : '🟡 Poll',
                  style: TextStyle(
                    fontSize: 9.5,
                    fontWeight: FontWeight.bold,
                    color: _wsState == WsConnectionState.connected
                        ? AppColors.bullish
                        : const Color(0xFFFFD700),
                  ),
                ),
              ],
            ),
          ),
          const SizedBox(width: 6),
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
          ? const Center(
              child: CircularProgressIndicator(color: AppColors.bullish))
          : _errorMessage != null && _trades.isEmpty
              ? _buildErrorBanner()
              : RefreshIndicator(
                  onRefresh: () async {
                    _fetchTrades();
                    _fetchAccountInfo();
                  },
                  color: AppColors.bullish,
                  child: ListView(
                    padding:
                        EdgeInsets.fromLTRB(12, 6, 12, isLandscape ? 30 : 90),
                    children: [
                      // Profile Mode Selector: Live vs Paper Trading
                      _buildAccountProfileSelector(),
                      if (_activeProfileMode == 'live') ...[
                        const SizedBox(height: 6),
                        _buildBrokerSelector(),
                      ],
                      const SizedBox(height: 6),

                      // Header: In Landscape, 2 columns side-by-side! In Portrait, stacked vertically.
                      if (isLandscape)
                        Row(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Expanded(
                              flex: 55,
                              child: _buildAccountPortfolioCard(
                                  realizedPnl, unrealizedPnl,
                                  isLandscape: true),
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              flex: 45,
                              child: _buildSummaryBar(winRate, realizedPnl,
                                  unrealizedPnl, openList.length, closed.length,
                                  isLandscape: true),
                            ),
                          ],
                        )
                      else ...[
                        _buildAccountPortfolioCard(realizedPnl, unrealizedPnl,
                            isLandscape: false),
                        const SizedBox(height: 6),
                        _buildSummaryBar(winRate, realizedPnl, unrealizedPnl,
                            openList.length, closed.length,
                            isLandscape: false),
                      ],

                      const SizedBox(height: 10),

                      // Three Distinct Tabs: Open Positions vs Pending Orders vs Trade History
                      _buildTabsHeader(
                          openList.length, pendingList.length, closed.length),

                      const SizedBox(height: 8),

                      if (_selectedTab == 0) ...[
                        // Tab 0: Open Positions
                        if (pendingList.isNotEmpty) ...[
                          InkWell(
                            onTap: () => setState(() => _selectedTab = 1),
                            borderRadius: BorderRadius.circular(8),
                            child: Container(
                              margin: const EdgeInsets.only(bottom: 10),
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 12, vertical: 9),
                              decoration: BoxDecoration(
                                color: const Color(0xFFFFD700)
                                    .withValues(alpha: 0.12),
                                borderRadius: BorderRadius.circular(8),
                                border: Border.all(
                                    color: const Color(0xFFFFD700)
                                        .withValues(alpha: 0.6),
                                    width: 1),
                              ),
                              child: Row(
                                children: [
                                  const Icon(Icons.hourglass_top,
                                      color: Color(0xFFFFD700), size: 16),
                                  const SizedBox(width: 8),
                                  Expanded(
                                    child: Text(
                                      'มี ${pendingList.length} คำสั่งที่กำลังรอดักราคา (Pending Orders)',
                                      style: const TextStyle(
                                          color: Color(0xFFFFD700),
                                          fontSize: 12,
                                          fontWeight: FontWeight.bold),
                                    ),
                                  ),
                                  const Text('แตะดูคำสั่ง →',
                                      style: TextStyle(
                                          color: Color(0xFFFFD700),
                                          fontSize: 11,
                                          fontWeight: FontWeight.bold)),
                                ],
                              ),
                            ),
                          ),
                        ],
                        if (openList.isEmpty)
                          Container(
                            padding: const EdgeInsets.symmetric(
                                vertical: 36, horizontal: 20),
                            decoration: BoxDecoration(
                              color: const Color(0xFF141926),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(
                                  color: const Color(0xFF2E384D)
                                      .withValues(alpha: 0.6)),
                            ),
                            child: Center(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Icon(
                                    (_activeProfileMode == 'live' &&
                                            (_accountInfo?['status'] ==
                                                    'DISCONNECTED' ||
                                                _accountInfo?['broker_id'] ==
                                                    'none'))
                                        ? Icons.link_off
                                        : Icons.check_circle_outline,
                                    size: 44,
                                    color: (_activeProfileMode == 'live' &&
                                            (_accountInfo?['status'] ==
                                                    'DISCONNECTED' ||
                                                _accountInfo?['broker_id'] ==
                                                    'none'))
                                        ? Colors.white54
                                        : AppColors.bullish,
                                  ),
                                  const SizedBox(height: 10),
                                  Text(
                                    _activeProfileMode == 'live'
                                        ? ((_accountInfo?['status'] ==
                                                    'DISCONNECTED' ||
                                                _accountInfo?['broker_id'] ==
                                                    'none')
                                            ? 'ยังไม่ได้เชื่อมต่อบัญชีจริง (No Live Broker)'
                                            : 'ไม่มีสถานะที่เปิดอยู่ในบัญชีจริง (No Live Positions)')
                                        : 'ไม่มีสถานะที่เปิดอยู่ขณะนี้ (No Open Positions)',
                                    style: const TextStyle(
                                        color: Colors.white,
                                        fontWeight: FontWeight.bold,
                                        fontSize: 15),
                                  ),
                                  const SizedBox(height: 6),
                                  Text(
                                    _activeProfileMode == 'live'
                                        ? ((_accountInfo?['status'] ==
                                                    'DISCONNECTED' ||
                                                _accountInfo?['broker_id'] ==
                                                    'none')
                                            ? 'กรุณากรอก API Key ในหน้า Settings เพื่อเชื่อมต่อบัญชีจริงของคุณ'
                                            : 'บัญชีจริงของคุณกำลังถือเงินสด 100% รอสัญญาณ SMC Confluence ที่ปลอดภัย')
                                        : 'พอร์ตจำลองกำลังถือเงินสด 100% รอสัญญาณ SMC Confluence คุณภาพสูง',
                                    textAlign: TextAlign.center,
                                    style: const TextStyle(
                                        color: AppColors.textMuted,
                                        fontSize: 12),
                                  ),
                                  const SizedBox(height: 14),
                                  if (_activeProfileMode == 'live' &&
                                      (_accountInfo?['status'] ==
                                              'DISCONNECTED' ||
                                          _accountInfo?['broker_id'] == 'none'))
                                    ElevatedButton.icon(
                                      onPressed: () => context.go('/settings'),
                                      icon:
                                          const Icon(Icons.settings, size: 16),
                                      label: const Text(
                                          'ไปยังหน้าตั้งค่า (Settings) →',
                                          style: TextStyle(
                                              fontWeight: FontWeight.bold,
                                              fontSize: 12)),
                                      style: ElevatedButton.styleFrom(
                                        backgroundColor:
                                            const Color(0xFF9B59B6),
                                        foregroundColor: Colors.white,
                                        padding: const EdgeInsets.symmetric(
                                            horizontal: 16, vertical: 8),
                                      ),
                                    )
                                  else
                                    ElevatedButton.icon(
                                      onPressed: () => context.go('/signals'),
                                      icon: const Icon(Icons.bolt,
                                          size: 16, color: Colors.black),
                                      label: const Text(
                                          'ดูสัญญาณเทรด SMC Signals →',
                                          style: TextStyle(
                                              color: Colors.black,
                                              fontWeight: FontWeight.bold,
                                              fontSize: 12)),
                                      style: ElevatedButton.styleFrom(
                                        backgroundColor:
                                            const Color(0xFF00E5FF),
                                        padding: const EdgeInsets.symmetric(
                                            horizontal: 16, vertical: 8),
                                      ),
                                    ),
                                ],
                              ),
                            ),
                          )
                        else
                          ...openList.map((t) => _buildTradeItem(t)),
                      ] else if (_selectedTab == 1) ...[
                        // Tab 1: Pending Orders
                        if (pendingList.isEmpty)
                          Container(
                            padding: const EdgeInsets.symmetric(
                                vertical: 36, horizontal: 20),
                            decoration: BoxDecoration(
                              color: const Color(0xFF141926),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(
                                  color: const Color(0xFF2E384D)
                                      .withValues(alpha: 0.6)),
                            ),
                            child: const Center(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Icon(Icons.hourglass_empty,
                                      size: 44, color: Colors.white24),
                                  SizedBox(height: 10),
                                  Text(
                                    'ไม่มีคำสั่งรอดักราคา (No Pending Orders)',
                                    style: TextStyle(
                                        color: Colors.white70,
                                        fontWeight: FontWeight.bold,
                                        fontSize: 14),
                                  ),
                                  SizedBox(height: 4),
                                  Text(
                                    'คำสั่ง Limit ที่ตั้งรอดักโซน Order Block ในอนาคตจะแสดงที่นี่และรอราคาลงมาแตะ',
                                    textAlign: TextAlign.center,
                                    style: TextStyle(
                                        color: AppColors.textMuted,
                                        fontSize: 11),
                                  ),
                                ],
                              ),
                            ),
                          )
                        else
                          ...pendingList.map((t) => _buildTradeItem(t)),
                      ] else ...[
                        // Tab 2: Trade History (Closed Trades)
                        _buildDisciplineScorecard(),
                        const SizedBox(height: 8),
                        if (closed.isNotEmpty)
                          _buildHistoryFilterBar(closed.length, wins.length,
                              closed.length - wins.length),

                        if (_getFilteredClosedTrades(closed).isEmpty)
                          Container(
                            padding: const EdgeInsets.symmetric(
                                vertical: 36, horizontal: 20),
                            decoration: BoxDecoration(
                              color: const Color(0xFF141926),
                              borderRadius: BorderRadius.circular(12),
                              border: Border.all(
                                  color: const Color(0xFF2E384D)
                                      .withValues(alpha: 0.6)),
                            ),
                            child: Center(
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  const Icon(Icons.history_toggle_off,
                                      size: 44, color: Colors.white24),
                                  const SizedBox(height: 10),
                                  Text(
                                    _activeProfileMode == 'live'
                                        ? 'ยังไม่มีประวัติการเทรดสดในบัญชีจริง'
                                        : 'ยังไม่มีประวัติการเทรดจำลองตามเงื่อนไขที่เลือก',
                                    style: const TextStyle(
                                        color: Colors.white70,
                                        fontWeight: FontWeight.bold,
                                        fontSize: 14),
                                  ),
                                  const SizedBox(height: 4),
                                  Text(
                                    _activeProfileMode == 'live'
                                        ? 'รายการซื้อขายที่ส่งผ่าน InnovestX จริงจะถูกบันทึกและซิงค์สถิติที่นี่แบบ Real-time'
                                        : 'ประวัติการเทรด Paper Trading ที่ปิดแล้วจะแสดงที่นี่',
                                    textAlign: TextAlign.center,
                                    style: const TextStyle(
                                        color: AppColors.textMuted,
                                        fontSize: 11),
                                  ),
                                ],
                              ),
                            ),
                          )
                        else
                          ..._getFilteredClosedTrades(closed)
                              .map((t) => _buildTradeItem(t)),
                      ],
                    ],
                  ),
                ),
    );
  }

  void _showLiveDisabledDialog() {
    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.surface,
        title: const Row(
          children: [
            Icon(Icons.lock_outline, color: Color(0xFF9B59B6), size: 20),
            SizedBox(width: 8),
            Text('โหมด Live Trading ปิดอยู่',
                style: TextStyle(
                    color: Colors.white,
                    fontSize: 15,
                    fontWeight: FontWeight.bold)),
          ],
        ),
        content: const Text(
          'ขณะนี้ระบบทำงานในโหมดพอร์ตจำลอง (Paper Trading)\n\nหากต้องการดูยอดเงินและประวัติการเทรดในบัญชีจริง กรุณาไปที่เมนู Settings และเปิดสวิตช์ "Live Trading"',
          style: TextStyle(color: Colors.white70, fontSize: 13, height: 1.4),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('ปิด', style: TextStyle(color: Colors.white60)),
          ),
          ElevatedButton.icon(
            onPressed: () {
              Navigator.pop(ctx);
              context.go('/settings');
            },
            icon: const Icon(Icons.settings, size: 16),
            label: const Text('ไปยังหน้า Settings'),
            style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF9B59B6),
                foregroundColor: Colors.white),
          ),
        ],
      ),
    );
  }

  Widget _buildBrokerSelector() {
    final brokers = [
      {'id': 'all', 'name': '🌐 ทั้งหมด', 'color': const Color(0xFF00E5FF)},
      {
        'id': 'innovestx',
        'name': '🟣 InnovestX (฿)',
        'color': const Color(0xFF9B59B6)
      },
      {
        'id': 'mt5',
        'name': '💱 MetaTrader 5 (\$)',
        'color': const Color(0xFF00E5FF)
      },
      {
        'id': 'binance',
        'name': '🪙 Binance (USDT)',
        'color': const Color(0xFFF0B90B)
      },
      {
        'id': 'alpaca',
        'name': '📈 Alpaca (\$)',
        'color': const Color(0xFFFFD700)
      },
    ];

    return SizedBox(
      height: 32,
      child: ListView(
        scrollDirection: Axis.horizontal,
        children: brokers.map((b) {
          final isSel = _selectedBroker == b['id'];
          final color = b['color'] as Color;
          return Padding(
            padding: const EdgeInsets.only(right: 6),
            child: ChoiceChip(
              label: Text(
                b['name'] as String,
                style: TextStyle(
                  fontSize: 11,
                  fontWeight: isSel ? FontWeight.bold : FontWeight.normal,
                  color: isSel ? Colors.white : Colors.white60,
                ),
              ),
              selected: isSel,
              selectedColor: color.withValues(alpha: 0.25),
              backgroundColor: const Color(0xFF141926),
              side: BorderSide(color: isSel ? color : const Color(0xFF2E384D)),
              onSelected: (_) {
                setState(() {
                  _selectedBroker = b['id'] as String;
                  _isLoading = true;
                });
                _fetchAccountInfo(mode: 'live', broker: _selectedBroker);
                _fetchTrades(mode: 'live', broker: _selectedBroker);
              },
              padding: const EdgeInsets.symmetric(horizontal: 4),
              materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
            ),
          );
        }).toList(),
      ),
    );
  }

  Widget _buildAccountProfileSelector() {
    final settings = ref.watch(settingsProvider);
    final isLiveAllowed = !settings.isPaperMode;

    return Container(
      padding: const EdgeInsets.all(3),
      decoration: BoxDecoration(
        color: const Color(0xFF141926),
        borderRadius: BorderRadius.circular(10),
        border: Border.all(color: const Color(0xFF2E384D), width: 1),
      ),
      child: Row(
        children: [
          Expanded(
            child: InkWell(
              onTap: () {
                if (!isLiveAllowed) {
                  _showLiveDisabledDialog();
                  return;
                }
                if (_activeProfileMode != 'live') {
                  setState(() {
                    _activeProfileMode = 'live';
                    _isLoading = true;
                  });
                  _fetchAccountInfo(mode: 'live');
                  _fetchTrades(mode: 'live');
                }
              },
              borderRadius: BorderRadius.circular(8),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: BoxDecoration(
                  color: _activeProfileMode == 'live'
                      ? const Color(0xFF9B59B6).withValues(alpha: 0.25)
                      : Colors.transparent,
                  borderRadius: BorderRadius.circular(8),
                  border: _activeProfileMode == 'live'
                      ? Border.all(color: const Color(0xFF9B59B6), width: 1.2)
                      : null,
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      isLiveAllowed ? Icons.verified : Icons.lock_outline,
                      size: 14,
                      color: _activeProfileMode == 'live'
                          ? const Color(0xFFD4AC0D)
                          : (isLiveAllowed ? Colors.white38 : Colors.white24),
                    ),
                    const SizedBox(width: 6),
                    Text(
                      isLiveAllowed
                          ? '🟣 บัญชีจริง Live'
                          : '🟣 บัญชีจริง (ปิดใน Settings)',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                        color: _activeProfileMode == 'live'
                            ? Colors.white
                            : (isLiveAllowed ? Colors.white60 : Colors.white38),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(width: 4),
          Expanded(
            child: InkWell(
              onTap: () {
                if (_activeProfileMode != 'paper') {
                  setState(() {
                    _activeProfileMode = 'paper';
                    _isLoading = true;
                  });
                  _fetchAccountInfo(mode: 'paper');
                  _fetchTrades(mode: 'paper');
                }
              },
              borderRadius: BorderRadius.circular(8),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: BoxDecoration(
                  color: _activeProfileMode == 'paper'
                      ? const Color(0xFF00E5FF).withValues(alpha: 0.18)
                      : Colors.transparent,
                  borderRadius: BorderRadius.circular(8),
                  border: _activeProfileMode == 'paper'
                      ? Border.all(color: const Color(0xFF00E5FF), width: 1.2)
                      : null,
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(Icons.science,
                        size: 14,
                        color: _activeProfileMode == 'paper'
                            ? const Color(0xFF00E5FF)
                            : Colors.white38),
                    const SizedBox(width: 6),
                    Text(
                      '🧪 พอร์ตจำลอง Paper (\$)',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                        color: _activeProfileMode == 'paper'
                            ? Colors.white
                            : Colors.white60,
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

  List<Map<String, dynamic>> _getFilteredClosedTrades(
      List<Map<String, dynamic>> closed) {
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

    final pnl = isOpen
        ? ((t['live_pnl'] ?? 0.0) as num).toDouble()
        : ((t['pnl'] ?? 0.0) as num).toDouble();
    final pnlPct = isOpen
        ? ((t['live_pnl_pct'] ?? 0.0) as num).toDouble()
        : ((t['pnl_pct'] ?? 0.0) as num).toDouble();
    final date = (t['opened_at'] ?? '').toString().split('T').first;
    final tradeCurrency =
        (t['currency'] ?? (_activeProfileMode == 'live' ? 'THB' : 'USD'))
            .toString()
            .toUpperCase();
    final currSym = tradeCurrency == 'THB' ? '฿' : '\$';
    final aiReview = t['ai_review']?.toString() ?? '';
    final executionRating = (t['execution_rating'] as num?)?.toInt() ?? 0;
    final lessons = t['lessons']?.toString() ?? '';
    final tags =
        (t['tags'] as List<dynamic>?)?.map((e) => e.toString()).toList() ??
            <String>[];
    final stopRaw = t['initial_stop_loss'] ?? t['stop_loss'];
    final stopLoss = (stopRaw as num?)?.toDouble() ?? 0.0;
    final takeProfit = (t['take_profit'] as num?)?.toDouble() ?? 0.0;
    final riskDistance = (entry - stopLoss).abs();
    final plannedRr =
        riskDistance > 0 ? (takeProfit - entry).abs() / riskDistance : 0.0;

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
      rr: plannedRr,
      date: date,
      currSym: currSym,
      aiReview: aiReview,
      executionRating: executionRating,
      lessons: lessons,
      tags: tags,
      isClosing: _closingTradeIds.contains(id),
      onClose: () => _closeTrade(id),
      onAudit: () => _showTradeAuditModal(context, t),
    );
  }

  Widget _buildTabsHeader(int openCount, int pendingCount, int closedCount) {
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
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: BoxDecoration(
                  color: _selectedTab == 0
                      ? const Color(0xFF00E5FF).withValues(alpha: 0.15)
                      : Colors.transparent,
                  borderRadius: BorderRadius.circular(8),
                  border: _selectedTab == 0
                      ? Border.all(color: const Color(0xFF00E5FF), width: 1.2)
                      : null,
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      Icons.bolt,
                      size: 14,
                      color: _selectedTab == 0
                          ? const Color(0xFF00E5FF)
                          : Colors.white54,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      'Open',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                        color: _selectedTab == 0
                            ? const Color(0xFF00E5FF)
                            : Colors.white70,
                      ),
                    ),
                    const SizedBox(width: 4),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 5, vertical: 1),
                      decoration: BoxDecoration(
                        color: openCount > 0
                            ? AppColors.bullish.withValues(alpha: 0.25)
                            : const Color(0xFF252540),
                        borderRadius: BorderRadius.circular(8),
                        border: openCount > 0
                            ? Border.all(
                                color: AppColors.bullish.withValues(alpha: 0.6),
                                width: 0.8)
                            : null,
                      ),
                      child: Text(
                        '$openCount',
                        style: TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                          color: openCount > 0
                              ? AppColors.bullish
                              : Colors.white54,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(width: 4),
          // Tab 1: Pending Orders
          Expanded(
            child: InkWell(
              borderRadius: BorderRadius.circular(8),
              onTap: () => setState(() => _selectedTab = 1),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: BoxDecoration(
                  color: _selectedTab == 1
                      ? const Color(0xFFFFD700).withValues(alpha: 0.15)
                      : Colors.transparent,
                  borderRadius: BorderRadius.circular(8),
                  border: _selectedTab == 1
                      ? Border.all(color: const Color(0xFFFFD700), width: 1.2)
                      : null,
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      Icons.hourglass_top,
                      size: 14,
                      color: _selectedTab == 1
                          ? const Color(0xFFFFD700)
                          : Colors.white54,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      'Pending',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                        color: _selectedTab == 1
                            ? const Color(0xFFFFD700)
                            : Colors.white70,
                      ),
                    ),
                    const SizedBox(width: 4),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 5, vertical: 1),
                      decoration: BoxDecoration(
                        color: pendingCount > 0
                            ? const Color(0xFFFFD700).withValues(alpha: 0.25)
                            : const Color(0xFF252540),
                        borderRadius: BorderRadius.circular(8),
                        border: pendingCount > 0
                            ? Border.all(
                                color: const Color(0xFFFFD700)
                                    .withValues(alpha: 0.8),
                                width: 0.8)
                            : null,
                      ),
                      child: Text(
                        '$pendingCount',
                        style: TextStyle(
                          fontSize: 10,
                          fontWeight: FontWeight.bold,
                          color: pendingCount > 0
                              ? const Color(0xFFFFD700)
                              : Colors.white54,
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ),
          const SizedBox(width: 4),
          // Tab 2: Trade History
          Expanded(
            child: InkWell(
              borderRadius: BorderRadius.circular(8),
              onTap: () => setState(() => _selectedTab = 2),
              child: AnimatedContainer(
                duration: const Duration(milliseconds: 150),
                padding: const EdgeInsets.symmetric(vertical: 8),
                decoration: BoxDecoration(
                  color: _selectedTab == 2
                      ? const Color(0xFF5CA3FF).withValues(alpha: 0.15)
                      : Colors.transparent,
                  borderRadius: BorderRadius.circular(8),
                  border: _selectedTab == 2
                      ? Border.all(color: const Color(0xFF5CA3FF), width: 1.2)
                      : null,
                ),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(
                      Icons.history,
                      size: 14,
                      color: _selectedTab == 2
                          ? const Color(0xFF5CA3FF)
                          : Colors.white54,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      'History',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                        color: _selectedTab == 2
                            ? const Color(0xFF5CA3FF)
                            : Colors.white70,
                      ),
                    ),
                    const SizedBox(width: 4),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 5, vertical: 1),
                      decoration: BoxDecoration(
                        color: const Color(0xFF252540),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      child: Text(
                        '$closedCount',
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
          _buildFilterChip('กำไร ($winCount) 🎯', 'win',
              activeColor: AppColors.bullish),
          const SizedBox(width: 6),
          _buildFilterChip('ขาดทุน/Invalid ($lossCount) 🛑', 'loss',
              activeColor: AppColors.bearish),
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
          color: isSelected
              ? color.withValues(alpha: 0.15)
              : const Color(0xFF1C2333),
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

  Widget _buildDisciplineScorecard() {
    final sc = _scorecardData ?? {};
    final score = (sc['discipline_score'] as num?)?.toInt() ?? 0;
    final planPct = (sc['plan_adherence_pct'] as num?)?.toDouble() ?? 0.0;
    final avgRating = (sc['avg_execution_rating'] as num?)?.toDouble() ?? 0.0;
    final winRate = (sc['win_rate'] as num?)?.toDouble() ?? 0.0;
    final avgRr = (sc['avg_rr'] as num?)?.toDouble() ?? 0.0;
    final reviewedTrades = (sc['reviewed_trades'] as num?)?.toInt() ?? 0;
    final bestSetups = (sc['best_setups'] as List<dynamic>?)
            ?.map((e) => e.toString())
            .toList() ??
        <String>[];

    final isGreat = score >= 85;
    final isGood = score >= 70 && score < 85;
    final scoreColor = isGreat
        ? AppColors.bullish
        : (isGood ? const Color(0xFFFFD700) : AppColors.bearish);
    final scoreBadge = reviewedTrades == 0
        ? 'No reviewed trades'
        : (isGreat
            ? '🛡️ Strict Institutional Compliance'
            : (isGood ? '⚖️ Moderate Discipline' : '⚠️ Rule Leakage Warning'));

    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: const Color(0xFF141926),
        borderRadius: BorderRadius.circular(12),
        border:
            Border.all(color: scoreColor.withValues(alpha: 0.4), width: 1.2),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  Icon(Icons.psychology, size: 16, color: scoreColor),
                  const SizedBox(width: 6),
                  const Text(
                    'DISCIPLINE & EXECUTION SCORECARD',
                    style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        color: Colors.white70,
                        letterSpacing: 0.5),
                  ),
                ],
              ),
              InkWell(
                onTap: _fetchScorecard,
                child: const Row(
                  children: [
                    Icon(Icons.refresh, size: 12, color: Color(0xFF93C5FD)),
                    SizedBox(width: 2),
                    Text('Sync',
                        style: TextStyle(
                            fontSize: 10,
                            color: Color(0xFF93C5FD),
                            fontWeight: FontWeight.w600)),
                  ],
                ),
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Text(
                '$score',
                style: TextStyle(
                    fontSize: 26,
                    fontWeight: FontWeight.bold,
                    color: scoreColor,
                    fontFamily: 'monospace'),
              ),
              const Text(' / 100',
                  style: TextStyle(fontSize: 12, color: Colors.white38)),
              const SizedBox(width: 10),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                decoration: BoxDecoration(
                  color: scoreColor.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(
                      color: scoreColor.withValues(alpha: 0.6), width: 0.8),
                ),
                child: Text(
                  scoreBadge,
                  style: TextStyle(
                      fontSize: 9.5,
                      fontWeight: FontWeight.bold,
                      color: scoreColor),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          ClipRRect(
            borderRadius: BorderRadius.circular(4),
            child: LinearProgressIndicator(
              value: (score / 100.0).clamp(0.0, 1.0),
              backgroundColor: const Color(0xFF232A38),
              valueColor: AlwaysStoppedAnimation<Color>(scoreColor),
              minHeight: 5,
            ),
          ),
          const SizedBox(height: 10),
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              _scorecardMiniStat('Plan Followed',
                  '${planPct.toStringAsFixed(0)}%', AppColors.bullish),
              _scorecardMiniStat('Win Rate', '${winRate.toStringAsFixed(0)}%',
                  AppColors.bullish),
              _scorecardMiniStat('Avg R:R', '${avgRr.toStringAsFixed(2)}R',
                  const Color(0xFFFFD700)),
              _scorecardMiniStat('AI Rating',
                  '${avgRating.toStringAsFixed(1)} ⭐', const Color(0xFF00E5FF)),
            ],
          ),
          if (bestSetups.isNotEmpty) ...[
            const SizedBox(height: 8),
            const Divider(height: 1, color: Color(0xFF222938)),
            const SizedBox(height: 6),
            Row(
              children: [
                const Text('Top Edge: ',
                    style: TextStyle(fontSize: 10, color: Colors.white38)),
                Expanded(
                  child: Wrap(
                    spacing: 4,
                    runSpacing: 2,
                    children: bestSetups.map((s) {
                      return Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 5, vertical: 1.5),
                        decoration: BoxDecoration(
                          color: const Color(0xFF1E283D),
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(
                              color: const Color(0xFF334466), width: 0.6),
                        ),
                        child: Text(s,
                            style: const TextStyle(
                                fontSize: 8.5,
                                color: Color(0xFF93C5FD),
                                fontWeight: FontWeight.w600)),
                      );
                    }).toList(),
                  ),
                ),
              ],
            ),
          ],
        ],
      ),
    );
  }

  Widget _scorecardMiniStat(String label, String value, Color color) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(fontSize: 9, color: Colors.white38)),
        const SizedBox(height: 2),
        Text(value,
            style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.bold,
                color: color,
                fontFamily: 'monospace')),
      ],
    );
  }

  void _showTradeAuditModal(BuildContext context, Map<String, dynamic> trade) {
    final sym = trade['symbol'] ?? 'BTC/USDT';
    final dir = (trade['direction'] ?? 'long').toString().toUpperCase();
    final pnl = ((trade['pnl'] ?? 0.0) as num).toDouble();
    final pnlPct = ((trade['pnl_pct'] ?? 0.0) as num).toDouble();
    final isWin = pnl >= 0;
    final color = isWin ? AppColors.bullish : AppColors.bearish;
    final rating = (trade['execution_rating'] as num?)?.toInt() ?? 0;
    final aiReview =
        trade['ai_review']?.toString() ?? 'ยังไม่ได้ตรวจสอบรายการนี้';
    final lessons = trade['lessons']?.toString() ??
        'กด Review เพื่อสร้าง audit จากข้อมูลที่บันทึกจริง';
    final tags =
        (trade['tags'] as List<dynamic>?)?.map((e) => e.toString()).toList() ??
            <String>[];
    final entry = (trade['entry'] as num?)?.toDouble() ?? 0.0;
    final closeP = (trade['close_price'] as num?)?.toDouble() ?? entry;
    final sl = (trade['stop_loss'] as num?)?.toDouble() ?? 0.0;
    final tp = (trade['take_profit'] as num?)?.toDouble() ?? 0.0;
    final tradeId = trade['id']?.toString() ?? '';

    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF10141E),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) {
        return StatefulBuilder(
          builder: (ctx, setModalState) {
            return Padding(
              padding: EdgeInsets.only(
                left: 16,
                right: 16,
                top: 16,
                bottom: MediaQuery.of(ctx).viewInsets.bottom + 24,
              ),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Center(
                    child: Container(
                      width: 40,
                      height: 4,
                      decoration: BoxDecoration(
                        color: Colors.white24,
                        borderRadius: BorderRadius.circular(2),
                      ),
                    ),
                  ),
                  const SizedBox(height: 14),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Row(
                        children: [
                          const Icon(Icons.psychology,
                              size: 20, color: Color(0xFF00E5FF)),
                          const SizedBox(width: 8),
                          Text(
                            'AI Post-Trade Audit ($sym)',
                            style: const TextStyle(
                                color: Colors.white,
                                fontSize: 16,
                                fontWeight: FontWeight.bold),
                          ),
                        ],
                      ),
                      IconButton(
                        icon: const Icon(Icons.close,
                            size: 18, color: Colors.white54),
                        onPressed: () => Navigator.pop(ctx),
                      ),
                    ],
                  ),
                  const Divider(color: Color(0xFF222938)),
                  const SizedBox(height: 6),

                  // Execution Rating & Realized Result
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: const Color(0xFF161C2A),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                          color: color.withValues(alpha: 0.4), width: 1),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('EXECUTION RATING',
                                style: TextStyle(
                                    fontSize: 9.5,
                                    color: Colors.white38,
                                    fontWeight: FontWeight.bold)),
                            const SizedBox(height: 4),
                            Row(
                              children: [
                                Row(
                                  children: List.generate(
                                    5,
                                    (i) => Icon(
                                      i < rating
                                          ? Icons.star
                                          : Icons.star_border,
                                      size: 16,
                                      color: const Color(0xFFFFD700),
                                    ),
                                  ),
                                ),
                                const SizedBox(width: 6),
                                Text('$rating / 5',
                                    style: const TextStyle(
                                        fontSize: 12,
                                        fontWeight: FontWeight.bold,
                                        color: Color(0xFFFFD700))),
                              ],
                            ),
                          ],
                        ),
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.end,
                          children: [
                            const Text('REALIZED PNL',
                                style: TextStyle(
                                    fontSize: 9.5,
                                    color: Colors.white38,
                                    fontWeight: FontWeight.bold)),
                            const SizedBox(height: 4),
                            Text(
                              '${isWin ? '+' : ''}\$${pnl.toStringAsFixed(2)} (${isWin ? '+' : ''}${pnlPct.toStringAsFixed(2)}%)',
                              style: TextStyle(
                                  fontSize: 13,
                                  fontWeight: FontWeight.bold,
                                  color: color,
                                  fontFamily: 'monospace'),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 10),

                  // Tags
                  if (tags.isNotEmpty)
                    Wrap(
                      spacing: 6,
                      runSpacing: 4,
                      children: tags.map((t) {
                        return Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 7, vertical: 3),
                          decoration: BoxDecoration(
                            color: const Color(0xFF232E45),
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(
                                color: const Color(0xFF3B4D72), width: 0.8),
                          ),
                          child: Text(t,
                              style: const TextStyle(
                                  fontSize: 10.5,
                                  color: Color(0xFF93C5FD),
                                  fontWeight: FontWeight.bold)),
                        );
                      }).toList(),
                    ),
                  const SizedBox(height: 10),

                  // Cognitive AI Review
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: const Color(0xFF141926),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: const Color(0xFF2E384D)),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.smart_toy_outlined,
                                size: 14, color: Color(0xFF00E5FF)),
                            SizedBox(width: 6),
                            Text('Institutional AI Critique',
                                style: TextStyle(
                                    fontSize: 11,
                                    fontWeight: FontWeight.bold,
                                    color: Color(0xFF00E5FF))),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Text(
                          aiReview,
                          style: const TextStyle(
                              fontSize: 12, color: Colors.white, height: 1.4),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 10),

                  // Lessons Learned
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: BoxDecoration(
                      color: const Color(0xFF1A1A28),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(
                          color:
                              const Color(0xFFFFD700).withValues(alpha: 0.3)),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.lightbulb_outline,
                                size: 14, color: Color(0xFFFFD700)),
                            SizedBox(width: 6),
                            Text('Key Lesson Learned',
                                style: TextStyle(
                                    fontSize: 11,
                                    fontWeight: FontWeight.bold,
                                    color: Color(0xFFFFD700))),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Text(
                          lessons,
                          style: const TextStyle(
                              fontSize: 12, color: Colors.white70, height: 1.4),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 14),

                  // Price Parameters Summary
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      _auditParamItem('Side', dir, color),
                      _auditParamItem('Entry', '\$${entry.toStringAsFixed(2)}',
                          Colors.white70),
                      _auditParamItem('Exit', '\$${closeP.toStringAsFixed(2)}',
                          Colors.white),
                      _auditParamItem('SL', '\$${sl.toStringAsFixed(2)}',
                          AppColors.bearish),
                      _auditParamItem('TP', '\$${tp.toStringAsFixed(2)}',
                          AppColors.bullish),
                    ],
                  ),
                  const SizedBox(height: 14),

                  // Regenerate Review Button
                  SizedBox(
                    width: double.infinity,
                    child: OutlinedButton.icon(
                      onPressed: () async {
                        try {
                          await AppApi.dio.post(AppApi.url(
                              '/api/v1/journal/entries/$tradeId/rule-review'));
                          await _fetchTrades();
                          await _fetchScorecard();
                          if (ctx.mounted) Navigator.pop(ctx);
                        } catch (_) {}
                      },
                      icon: const Icon(Icons.refresh,
                          size: 14, color: Color(0xFF93C5FD)),
                      label: const Text('🔄 Re-Audit Trade with Rules',
                          style: TextStyle(
                              color: Color(0xFF93C5FD),
                              fontSize: 12,
                              fontWeight: FontWeight.bold)),
                      style: OutlinedButton.styleFrom(
                        side: const BorderSide(color: Color(0xFF3B4D72)),
                        padding: const EdgeInsets.symmetric(vertical: 10),
                      ),
                    ),
                  ),
                ],
              ),
            );
          },
        );
      },
    );
  }

  Widget _auditParamItem(String label, String value, Color color) {
    return Column(
      children: [
        Text(label,
            style: const TextStyle(fontSize: 9.5, color: Colors.white38)),
        const SizedBox(height: 2),
        Text(value,
            style: TextStyle(
                fontSize: 11,
                fontWeight: FontWeight.bold,
                color: color,
                fontFamily: 'monospace')),
      ],
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
              style: TextStyle(
                  color: Colors.white,
                  fontWeight: FontWeight.bold,
                  fontSize: 15),
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
                  style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF2E82FE)),
                ),
                const SizedBox(width: 10),
                OutlinedButton.icon(
                  onPressed: () {
                    _fetchAccountInfo();
                    _fetchTrades();
                  },
                  icon: const Icon(Icons.refresh, size: 16),
                  label: const Text('ลองใหม่'),
                  style:
                      OutlinedButton.styleFrom(foregroundColor: Colors.white70),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildAccountPortfolioCard(double realizedPnl, double unrealizedPnl,
      {bool isLandscape = false}) {
    final acc = _accountInfo ??
        {
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
    final buyingPower =
        (acc['buying_power'] as num?)?.toDouble() ?? (initialCap * 2);
    final cash = (acc['cash'] as num?)?.toDouble() ?? initialCap;
    final mode = acc['mode']?.toString() ?? 'paper';
    final isLive = mode == 'live';

    final currency = (acc['currency'] ?? 'USD').toString().toUpperCase();
    final currSym = currency == 'THB' ? '฿' : '\$';
    final holdCash = (acc['hold_cash'] as num?)?.toDouble() ?? 0.0;
    final isInnovestX = brokerName.toLowerCase().contains('innovestx');

    final netWorth = initialCap + realizedPnl + unrealizedPnl;
    final totalPnl = realizedPnl + unrealizedPnl;
    final totalPnlPct = initialCap > 0 ? (totalPnl / initialCap) * 100 : 0.0;
    final isPnlPositive = totalPnl >= 0;

    final isDisconnected = isLive &&
        (acc['status'] == 'DISCONNECTED' || acc['broker_id'] == 'none');

    if (isDisconnected) {
      return Container(
        margin: isLandscape
            ? EdgeInsets.zero
            : const EdgeInsets.fromLTRB(0, 4, 0, 0),
        padding: EdgeInsets.all(isLandscape ? 12 : 14),
        decoration: BoxDecoration(
          color: const Color(0xFF141926),
          borderRadius: BorderRadius.circular(12),
          border: Border.all(color: const Color(0xFF2E384D)),
        ),
        child: Column(
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: const Color(0xFF252540),
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: const Icon(Icons.link_off,
                      color: Colors.white54, size: 20),
                ),
                const SizedBox(width: 10),
                const Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        'ยังไม่ได้เชื่อมต่อบัญชีจริง (No Live Broker)',
                        style: TextStyle(
                            fontWeight: FontWeight.bold,
                            fontSize: 13,
                            color: Colors.white),
                      ),
                      SizedBox(height: 2),
                      Text(
                        'กรุณาระบุ API Key ในหน้า Settings เพื่อเชื่อมต่อบัญชีจริง',
                        style: TextStyle(fontSize: 11, color: Colors.white54),
                      ),
                    ],
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                onPressed: () => context.go('/settings'),
                icon: const Icon(Icons.key, size: 15),
                label: const Text('ไปยังหน้าตั้งค่าเชื่อมต่อ API (Settings)',
                    style:
                        TextStyle(fontSize: 12, fontWeight: FontWeight.bold)),
                style: ElevatedButton.styleFrom(
                  backgroundColor: const Color(0xFF9B59B6),
                  foregroundColor: Colors.white,
                  padding: const EdgeInsets.symmetric(vertical: 8),
                  shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(8)),
                ),
              ),
            ),
          ],
        ),
      );
    }

    return Container(
      margin:
          isLandscape ? EdgeInsets.zero : const EdgeInsets.fromLTRB(0, 4, 0, 0),
      padding: EdgeInsets.all(isLandscape ? 10 : 12),
      decoration: BoxDecoration(
        color: const Color(0xFF141926),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(
          color: isLive
              ? (isInnovestX ? const Color(0xFF9B59B6) : AppColors.bearish)
                  .withValues(alpha: 0.6)
              : const Color(0xFF2E384D),
          width: isLive ? 1.2 : 1,
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
                  color: isLive
                      ? (isInnovestX
                          ? const Color(0xFF9B59B6).withValues(alpha: 0.2)
                          : AppColors.bearish.withValues(alpha: 0.15))
                      : AppColors.bullish.withValues(alpha: 0.15),
                  borderRadius: BorderRadius.circular(8),
                ),
                child: Icon(
                  isLive
                      ? (isInnovestX
                          ? Icons.account_balance_wallet
                          : Icons.bolt)
                      : Icons.account_balance_wallet_outlined,
                  color: isLive
                      ? (isInnovestX
                          ? const Color(0xFF9B59B6)
                          : AppColors.bearish)
                      : AppColors.bullish,
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
                      style: const TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                          color: Colors.white),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                    Text(
                      'Account #$accId',
                      style: const TextStyle(
                          fontSize: 10,
                          color: Colors.white38,
                          fontFamily: 'monospace'),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 6),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                decoration: BoxDecoration(
                  color: isLive
                      ? (isInnovestX
                          ? const Color(0xFF9B59B6).withValues(alpha: 0.25)
                          : AppColors.bearish.withValues(alpha: 0.2))
                      : const Color(0xFF252540),
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(
                    color: isLive
                        ? (isInnovestX
                            ? const Color(0xFF9B59B6)
                            : AppColors.bearish)
                        : const Color(0xFF00E5FF),
                    width: 0.8,
                  ),
                ),
                child: Text(
                  isLive
                      ? (isInnovestX ? '🟣 LIVE (INNOVESTX)' : '⚡ LIVE')
                      : '🧪 PAPER',
                  style: TextStyle(
                    fontSize: 9,
                    fontWeight: FontWeight.bold,
                    color: isLive
                        ? (isInnovestX
                            ? const Color(0xFFD4AC0D)
                            : AppColors.bearish)
                        : const Color(0xFF00E5FF),
                  ),
                ),
              ),
            ],
          ),

          const SizedBox(height: 6),

          // Row 2: Reset & Configure Button (if paper) or Live Broker Sync (if live)
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
                  border: Border.all(
                      color: const Color(0xFF00E5FF).withValues(alpha: 0.4),
                      width: 0.8),
                ),
                child: const Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(Icons.tune, color: Color(0xFF00E5FF), size: 12),
                    SizedBox(width: 4),
                    Text('ตั้งค่าเงินต้น / Reset Portfolio',
                        style: TextStyle(
                            fontSize: 10,
                            color: Color(0xFF00E5FF),
                            fontWeight: FontWeight.bold)),
                  ],
                ),
              ),
            ),
            const SizedBox(height: 6),
          ] else if (isInnovestX) ...[
            InkWell(
              onTap: () => context.go('/settings'),
              borderRadius: BorderRadius.circular(6),
              child: Container(
                width: double.infinity,
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: const Color(0xFF9B59B6).withValues(alpha: 0.12),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(
                      color: const Color(0xFF9B59B6).withValues(alpha: 0.4),
                      width: 0.8),
                ),
                child: const Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    Icon(Icons.verified, color: Color(0xFF9B59B6), size: 12),
                    SizedBox(width: 4),
                    Text('เชื่อมต่อ InnovestX Exchange สำเร็จ (แตะเพื่อจัดการ)',
                        style: TextStyle(
                            fontSize: 10,
                            color: Color(0xFFD4AC0D),
                            fontWeight: FontWeight.bold)),
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
                child: _accountStat('Total Equity',
                    '$currSym${_formatCurrency(initialCap)}', Colors.white70),
              ),
              Expanded(
                child: _accountStat('Net Balance',
                    '$currSym${_formatCurrency(netWorth)}', Colors.white),
              ),
              Expanded(
                child: _accountStat(
                  'Total PnL',
                  '${isPnlPositive ? '+' : ''}$currSym${_formatCurrency(totalPnl)} (${isPnlPositive ? '+' : ''}${totalPnlPct.toStringAsFixed(1)}%)',
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
                  const Icon(Icons.flash_on,
                      size: 11, color: Color(0xFFFFD700)),
                  const SizedBox(width: 2),
                  Text(
                    'Buying Power: $currSym${_formatCurrency(buyingPower)}',
                    style: const TextStyle(
                        fontSize: 9,
                        color: Colors.white54,
                        fontFamily: 'monospace'),
                  ),
                ],
              ),
              Text(
                'Available Cash: $currSym${_formatCurrency(cash)}${holdCash > 0 ? ' (Hold: $currSym${_formatCurrency(holdCash)})' : ''}',
                style: const TextStyle(
                    fontSize: 9,
                    color: Colors.white38,
                    fontFamily: 'monospace'),
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

  Widget _buildSummaryBar(String winRate, double realizedPnl,
      double unrealizedPnl, int openCount, int closedCount,
      {bool isLandscape = false}) {
    final isRealPos = realizedPnl >= 0;
    final isUnrealPos = unrealizedPnl >= 0;
    final currency =
        (_accountInfo?['currency'] ?? 'USD').toString().toUpperCase();
    final currSym = currency == 'THB' ? '฿' : '\$';

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
                Icon(Icons.analytics_outlined,
                    color: AppColors.bullish, size: 15),
                SizedBox(width: 6),
                Text('Performance Overview',
                    style: TextStyle(
                        fontWeight: FontWeight.bold,
                        fontSize: 11,
                        color: Colors.white)),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                    child: _stat('Win Rate', '$winRate%', AppColors.bullish)),
                Expanded(
                    child: _stat('Open Orders', '$openCount Active',
                        const Color(0xFF00E5FF))),
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
                    '${isUnrealPos ? '+' : ''}$currSym${unrealizedPnl.toStringAsFixed(2)}',
                    isUnrealPos ? AppColors.bullish : AppColors.bearish,
                  ),
                ),
                Expanded(
                  child: _stat(
                    'Realized PnL',
                    '${isRealPos ? '+' : ''}$currSym${realizedPnl.toStringAsFixed(2)}',
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
        border:
            Border.all(color: const Color(0xFF2E384D).withValues(alpha: 0.6)),
      ),
      child: Row(
        children: [
          Expanded(child: _stat('Win Rate', '$winRate%', AppColors.bullish)),
          Expanded(
              child: _stat(
                  'Open Orders', '$openCount Active', const Color(0xFF00E5FF))),
          Expanded(
            child: _stat(
              'Unrealized',
              '${isUnrealPos ? '+' : ''}$currSym${unrealizedPnl.toStringAsFixed(2)}',
              isUnrealPos ? AppColors.bullish : AppColors.bearish,
            ),
          ),
          Expanded(
            child: _stat(
              'Realized',
              '${isRealPos ? '+' : ''}$currSym${realizedPnl.toStringAsFixed(2)}',
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
        Text(label,
            style: const TextStyle(fontSize: 10, color: Colors.white38)),
        const SizedBox(height: 2),
        Text(value,
            style: TextStyle(
                fontSize: 12,
                color: color,
                fontWeight: FontWeight.bold,
                fontFamily: 'monospace')),
      ],
    );
  }
}

class _TradeCard extends StatelessWidget {
  final String id,
      tag,
      symbol,
      direction,
      date,
      status,
      closeReason,
      aiReview,
      lessons;
  final int executionRating;
  final List<String> tags;
  final bool isClosing;
  final double entry, livePrice, closePrice, size, pnl, pnlUsd, rr;
  final String currSym;
  final VoidCallback onClose;
  final VoidCallback? onAudit;

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
    this.currSym = '\$',
    this.aiReview = '',
    this.executionRating = 0,
    this.lessons = '',
    this.tags = const [],
    this.isClosing = false,
    required this.onClose,
    this.onAudit,
  });

  String _formatPrice(double price, String sym) {
    final s = sym.toUpperCase();
    final isForex = s.contains('EURUSD') ||
        s.contains('GBPUSD') ||
        s.contains('USDJPY') ||
        s.contains('AUDUSD') ||
        s.contains('USDCAD');
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
    final isPending = status == 'pending';
    final isOpen = status == 'open';
    final isWin = pnlUsd >= 0;
    final isLong = direction == 'LONG';
    final sideColor = isLong ? AppColors.bullish : AppColors.bearish;
    final pnlColor = isPending
        ? const Color(0xFFFFD700)
        : (isWin ? AppColors.bullish : AppColors.bearish);

    // Determine status badge text and color
    String statusLabel = '● OPEN';
    Color statusColor = const Color(0xFF00E5FF);
    if (isPending) {
      statusLabel = '⏳ PENDING';
      statusColor = const Color(0xFFFFD700);
    } else if (!isOpen) {
      if (closeReason.contains('TP')) {
        statusLabel = '🎯 TP HIT';
        statusColor = AppColors.bullish;
      } else if (closeReason.contains('SL')) {
        statusLabel = '🛑 SL HIT';
        statusColor = AppColors.bearish;
      } else if (closeReason.toLowerCase().contains('invalidation') ||
          closeReason.toLowerCase().contains('invalid')) {
        statusLabel = '⚠️ INVALIDATED';
        statusColor = const Color(0xFFFF9900);
      } else {
        statusLabel = 'CLOSED';
        statusColor = Colors.white54;
      }
    }

    final exitP = closePrice > 0 ? closePrice : livePrice;
    final distPct = entry > 0 ? ((livePrice - entry) / entry * 100).abs() : 0.0;

    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        borderRadius: BorderRadius.circular(12),
        onTap: (!isOpen && !isPending) ? onAudit : null,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            children: [
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      // Direction Badge
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 7, vertical: 3),
                        decoration: BoxDecoration(
                          color: sideColor.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(color: sideColor, width: 0.8),
                        ),
                        child: Text(direction,
                            style: TextStyle(
                                color: sideColor,
                                fontWeight: FontWeight.bold,
                                fontSize: 11)),
                      ),
                      const SizedBox(width: 6),
                      Text(
                        symbol,
                        style: const TextStyle(
                            fontWeight: FontWeight.bold, fontSize: 14),
                      ),
                      const SizedBox(width: 6),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 5, vertical: 2),
                        decoration: BoxDecoration(
                          color: statusColor.withValues(alpha: 0.15),
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(
                              color: statusColor.withValues(alpha: 0.5),
                              width: 0.8),
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
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                    decoration: BoxDecoration(
                      color: pnlColor.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(color: pnlColor, width: 0.8),
                    ),
                    child: Text(
                      isPending
                          ? 'รอ Match (ห่าง ${distPct.toStringAsFixed(2)}%)'
                          : '${isWin ? '+' : ''}$currSym${pnlUsd.toStringAsFixed(2)} (${isWin ? '+' : ''}${pnl.toStringAsFixed(2)}%)',
                      style: TextStyle(
                          color: pnlColor,
                          fontWeight: FontWeight.bold,
                          fontSize: 11,
                          fontFamily: 'monospace'),
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
                      style: const TextStyle(
                          fontSize: 11,
                          color: Color(0xFF93C5FD),
                          fontWeight: FontWeight.w600),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  const SizedBox(width: 8),
                  Text(date,
                      style:
                          const TextStyle(fontSize: 10, color: Colors.white38)),
                ],
              ),
              const SizedBox(height: 4),
              Row(
                children: [
                  Expanded(
                    child: Text(
                      isPending
                          ? 'เป้าเข้า (Entry): \$${_formatPrice(entry, symbol)}  ➜  ตลาดสด: \$${_formatPrice(livePrice, symbol)}'
                          : (isOpen
                              ? 'Entry: \$${_formatPrice(entry, symbol)}  ➜  Live: \$${_formatPrice(livePrice, symbol)}'
                              : 'Entry: \$${_formatPrice(entry, symbol)}  ➜  Exit: \$${_formatPrice(exitP, symbol)}${closeReason.isNotEmpty ? ' ($closeReason)' : ''}'),
                      style: const TextStyle(
                          fontSize: 11,
                          color: Colors.white70,
                          fontFamily: 'monospace'),
                    ),
                  ),
                  if (isOpen || isPending) ...[
                    const SizedBox(width: 8),
                    InkWell(
                      onTap: isClosing ? null : onClose,
                      child: Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 10, vertical: 4),
                        decoration: BoxDecoration(
                          color: AppColors.bearish.withValues(alpha: 0.2),
                          borderRadius: BorderRadius.circular(4),
                          border:
                              Border.all(color: AppColors.bearish, width: 0.8),
                        ),
                        child: Text(
                          isClosing
                              ? 'Working…'
                              : (isPending ? 'Cancel ✕' : 'Close ✕'),
                          style: const TextStyle(
                              color: AppColors.bearish,
                              fontSize: 11,
                              fontWeight: FontWeight.bold),
                        ),
                      ),
                    ),
                  ],
                ],
              ),
              // AI Review & Execution Rating for Closed Trades
              if (!isOpen && !isPending) ...[
                const SizedBox(height: 8),
                const Divider(height: 1, color: Color(0xFF222938)),
                const SizedBox(height: 6),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: const Color(0xFF141926),
                    borderRadius: BorderRadius.circular(6),
                    border:
                        Border.all(color: const Color(0xFF2E384D), width: 0.8),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Icon(Icons.smart_toy_outlined,
                              size: 13, color: Color(0xFF00E5FF)),
                          const SizedBox(width: 4),
                          const Text('AI Cognitive Review:',
                              style: TextStyle(
                                  fontSize: 10.5,
                                  fontWeight: FontWeight.bold,
                                  color: Color(0xFF00E5FF))),
                          const Spacer(),
                          Row(
                            mainAxisSize: MainAxisSize.min,
                            children: List.generate(
                              5,
                              (i) => Icon(
                                i < executionRating
                                    ? Icons.star
                                    : Icons.star_border,
                                size: 12,
                                color: const Color(0xFFFFD700),
                              ),
                            ),
                          ),
                          const SizedBox(width: 4),
                          Text('$executionRating/5',
                              style: const TextStyle(
                                  fontSize: 10,
                                  fontWeight: FontWeight.bold,
                                  color: Color(0xFFFFD700))),
                        ],
                      ),
                      if (aiReview.isNotEmpty) ...[
                        const SizedBox(height: 4),
                        Text(
                          aiReview,
                          style: const TextStyle(
                              fontSize: 11, color: Colors.white70, height: 1.3),
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ],
                      if (tags.isNotEmpty) ...[
                        const SizedBox(height: 6),
                        Wrap(
                          spacing: 4,
                          runSpacing: 4,
                          children: tags.map((tg) {
                            return Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 5, vertical: 1.5),
                              decoration: BoxDecoration(
                                color: const Color(0xFF252540),
                                borderRadius: BorderRadius.circular(4),
                                border: Border.all(
                                    color: const Color(0xFF3E4C6D), width: 0.6),
                              ),
                              child: Text(tg,
                                  style: const TextStyle(
                                      fontSize: 9,
                                      color: Color(0xFF93C5FD),
                                      fontWeight: FontWeight.w600)),
                            );
                          }).toList(),
                        ),
                      ],
                    ],
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
    );
  }
}
