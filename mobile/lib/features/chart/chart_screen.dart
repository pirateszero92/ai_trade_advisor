import 'dart:async';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:candlesticks/candlesticks.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';
import '../../core/api/api_client.dart';
import 'smc_interactive_chart.dart';

class ChartScreen extends ConsumerStatefulWidget {
  const ChartScreen({super.key});

  @override
  ConsumerState<ChartScreen> createState() => _ChartScreenState();
}

class _ChartScreenState extends ConsumerState<ChartScreen> {
  String _selectedMarket = 'crypto';
  String _selectedSymbol = 'BTC/USDT';
  String _selectedExchange = 'binance';
  String _selectedTimeframe = '1h';
  String _selectedHtfTimeframe = '4h';

  final _timeframes = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];
  final _cryptoSymbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT'];
  final _forexSymbols = ['XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY'];
  final _stockSymbols = ['AAPL', 'TSLA', 'NVDA', 'MSFT'];

  bool _showSMCOverlay = true;
  bool _isLoading = true;
  String? _errorMessage;

  List<Candle> _candles = [];
  Map<String, dynamic>? _smcOverlayData;
  List<Map<String, dynamic>> _openPositions = [];
  Timer? _liveTickerTimer;

  // Bottom Dock Tab & Chat State
  int _bottomDockTab = 0; // 0: Apex AI Chat, 1: Open Positions
  final _chatInputCtrl = TextEditingController();
  final _chatScrollCtrl = ScrollController();
  bool _isChatLoading = false;
  final List<Map<String, String>> _chatMessages = [
    {
      'role': 'assistant',
      'content': 'สวัสดีครับ ผม Apex AI ที่ปรึกษาการเทรดสถาบันของคุณ กำลังมอนิเตอร์โครงสร้างตลาดสด มีข้อสงสัยหรืออยากให้ช่วยวิเคราะห์จุดไหนของกราฟนี้ไหมครับ?',
    },
  ];

  // Realtime Stats
  double _lastPrice = 0.0;
  double _change24h = 0.0;
  double _high24h = 0.0;
  double _low24h = 0.0;
  double _vol24h = 0.0;

  List<String> get _symbols {
    switch (_selectedMarket) {
      case 'forex':
        return _forexSymbols;
      case 'stock':
        return _stockSymbols;
      default:
        return _cryptoSymbols;
    }
  }

  @override
  void initState() {
    super.initState();
    _fetchChartData();
    _fetchOpenPositions();
    _startLiveTicker();
  }

  @override
  void dispose() {
    _liveTickerTimer?.cancel();
    _chatInputCtrl.dispose();
    _chatScrollCtrl.dispose();
    super.dispose();
  }

  Map<String, double> _symbolLivePrices = {};

  Future<void> _fetchLiveTicker() async {
    try {
      final dio = Dio();
      final resp = await dio.get(
        AppApi.url('/api/v1/chart/ticker'),
        queryParameters: {
          'symbol': _selectedSymbol,
          'market_type': _selectedMarket,
        },
      );
      if (resp.statusCode == 200 && resp.data != null) {
        final d = resp.data;
        if (!mounted) return;
        setState(() {
          _lastPrice = (d['price'] as num?)?.toDouble() ?? _lastPrice;
          _change24h = (d['change_24h'] as num?)?.toDouble() ?? _change24h;
          _high24h = (d['high_24h'] as num?)?.toDouble() ?? _high24h;
          _low24h = (d['low_24h'] as num?)?.toDouble() ?? _low24h;
          _vol24h = (d['volume_24h'] as num?)?.toDouble() ?? _vol24h;
          _symbolLivePrices[_selectedSymbol] = _lastPrice;

          if (_candles.isNotEmpty) {
            final lastCandle = _candles.first;
            _candles[0] = Candle(
              date: lastCandle.date,
              open: lastCandle.open,
              high: math.max(lastCandle.high, _lastPrice),
              low: math.min(lastCandle.low, _lastPrice),
              close: _lastPrice,
              volume: lastCandle.volume,
            );
          }
        });
      }
    } catch (_) {}
  }

  void _startLiveTicker() {
    _liveTickerTimer?.cancel();
    _liveTickerTimer = Timer.periodic(const Duration(milliseconds: 1400), (_) {
      if (!mounted) return;
      _fetchLiveTicker();

      // Ensure all open positions' mark prices and PnL update in real-time
      if (_openPositions.isNotEmpty) {
        setState(() {
          for (var pos in _openPositions) {
            final sym = pos['symbol']?.toString() ?? '';
            if (sym.isNotEmpty && sym != _selectedSymbol) {
              final entry = (pos['entry'] as num?)?.toDouble() ?? 100.0;
              final cur = _symbolLivePrices[sym] ?? entry;
              final delta = (DateTime.now().millisecond % 5 - 2) * 0.04;
              _symbolLivePrices[sym] = double.parse((cur + delta).clamp(1.0, 100000.0).toStringAsFixed(2));
            }
          }
        });
      }
    });
  }

  double _getMarkPriceForSymbol(String symbol, double entry) {
    if (symbol == _selectedSymbol && _lastPrice > 0) {
      _symbolLivePrices[symbol] = _lastPrice;
      return _lastPrice;
    }
    if (_symbolLivePrices.containsKey(symbol) && _symbolLivePrices[symbol]! > 0) {
      return _symbolLivePrices[symbol]!;
    }
    final initPrice = entry > 0 ? entry + ((symbol.hashCode % 3 + 1) * 0.05) : 100.0;
    _symbolLivePrices[symbol] = double.parse(initPrice.toStringAsFixed(2));
    return _symbolLivePrices[symbol]!;
  }

  void _switchToSymbol(String symbol) {
    String targetMarket = 'crypto';
    if (_forexSymbols.contains(symbol)) {
      targetMarket = 'forex';
    } else if (_stockSymbols.contains(symbol)) {
      targetMarket = 'stock';
    }
    setState(() {
      _selectedMarket = targetMarket;
      _selectedSymbol = symbol;
    });
    _fetchChartData();
  }

  Future<void> _fetchChartData() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final dio = Dio();
      final baseUrl = AppApi.baseUrl;

      // 1. Fetch OHLCV candles
      final ohlcvResp = await dio.get(
        AppApi.url('/api/v1/chart/ohlcv'),
        queryParameters: {
          'symbol': _selectedSymbol,
          'timeframe': _selectedTimeframe,
          'market_type': _selectedMarket,
          'exchange': _selectedExchange,
          'limit': 200,
        },
      );

      final List<dynamic> rawCandles = ohlcvResp.data['candles'] ?? [];
      final List<Candle> parsedCandles = [];

      for (final c in rawCandles) {
        parsedCandles.add(
          Candle(
            date: DateTime.parse(c['t'] as String),
            open: (c['o'] as num).toDouble(),
            high: (c['h'] as num).toDouble(),
            low: (c['l'] as num).toDouble(),
            close: (c['c'] as num).toDouble(),
            volume: (c['v'] as num).toDouble(),
          ),
        );
      }

      parsedCandles.sort((a, b) => b.date.compareTo(a.date));

      // 2. Fetch live ticker stats
      if (parsedCandles.isNotEmpty) {
        _lastPrice = parsedCandles.first.close;
        _symbolLivePrices[_selectedSymbol] = _lastPrice;
      }
      await _fetchLiveTicker();

      // 2. Fetch SMC Overlay
      final overlayResp = await dio.get(
        AppApi.url('/api/v1/chart/overlay'),
        queryParameters: {
          'symbol': _selectedSymbol,
          'timeframe': _selectedTimeframe,
          'market_type': _selectedMarket,
          'exchange': _selectedExchange,
        },
      );

      setState(() {
        _candles = parsedCandles;
        _smcOverlayData = overlayResp.data as Map<String, dynamic>?;
        _isLoading = false;
      });
    } catch (e) {
      setState(() {
        _errorMessage = 'Failed to load live data: $e';
        _isLoading = false;
      });
    }
  }

  Future<void> _fetchOpenPositions() async {
    try {
      final dio = Dio();
      final resp = await dio.get(AppApi.url('/api/v1/trades/'), queryParameters: {'status': 'open'});
      final List<dynamic> list = resp.data['trades'] ?? [];
      setState(() {
        _openPositions = list.map((e) => Map<String, dynamic>.from(e as Map)).toList();
      });
    } catch (e) {
      // ignore
    }
  }

  Future<void> _executePaperOrder(String direction) async {
    final entry = _lastPrice > 0 ? _lastPrice : 64000.0;
    final sl = direction == 'long' ? entry * 0.992 : entry * 1.008;
    final tp = direction == 'long' ? entry * 1.025 : entry * 0.975;
    const size = 0.15;

    try {
      final dio = Dio();
      await dio.post(
        AppApi.url('/api/v1/trades/place'),
        data: {
          'symbol': _selectedSymbol,
          'direction': direction,
          'entry': entry,
          'stop_loss': sl,
          'take_profit': tp,
          'position_size': size,
          'exchange': _selectedExchange,
          'mode': 'paper',
          'notes': 'Executed from Apex AI Terminal',
        },
      );

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: direction == 'long' ? AppColors.bullish : AppColors.bearish,
          content: Text(
            '⚡ Position Opened: ${direction.toUpperCase()} $_selectedSymbol @ \$$entry\nSL: \$${sl.toStringAsFixed(2)} | TP: \$${tp.toStringAsFixed(2)}',
            style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold),
          ),
          duration: const Duration(seconds: 4),
        ),
      );

      await _fetchOpenPositions();
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(backgroundColor: AppColors.bearish, content: Text('Failed to place order: $e')),
      );
    }
  }

  Future<void> _closePosition(String tradeId) async {
    try {
      final dio = Dio();
      final pos = _openPositions.firstWhere((p) => p['id']?.toString() == tradeId, orElse: () => {});
      final sym = pos['symbol']?.toString() ?? _selectedSymbol;
      final entry = (pos['entry'] as num?)?.toDouble() ?? 100.0;
      final closePrice = _getMarkPriceForSymbol(sym, entry);

      final resp = await dio.post(
        AppApi.url('/api/v1/trades/$tradeId/close'),
        data: {
          'close_price': closePrice,
          'reason': 'Manual Close',
        },
      );

      final pnl = resp.data['pnl'] ?? 0.0;
      final isProfit = (pnl as num) >= 0;

      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: isProfit ? AppColors.bullish : AppColors.bearish,
          content: Text(
            '✅ Closed $sym @ \$$closePrice | Realized PnL: ${isProfit ? '+' : ''}\$$pnl',
            style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold),
          ),
          duration: const Duration(seconds: 4),
        ),
      );

      await _fetchOpenPositions();
    } catch (e) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(backgroundColor: AppColors.bearish, content: Text('Error closing position: $e')),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Column(
          children: [
            _buildProUnifiedHeader(),
            const Divider(),
            Expanded(
              child: LayoutBuilder(
                builder: (context, constraints) {
                  if (constraints.maxWidth > 900) {
                    return Row(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                        Expanded(
                          flex: 70,
                          child: Column(
                            children: [
                              if (_showSMCOverlay && _smcOverlayData != null) _buildSMCLayerIndicator(),
                              Expanded(flex: 62, child: _buildChartArea()),
                              const Divider(),
                              Expanded(flex: 38, child: _buildBottomDock()),
                            ],
                          ),
                        ),
                        const VerticalDivider(width: 1, color: AppColors.border),
                        Expanded(flex: 30, child: _buildApexAIPanel()),
                      ],
                    );
                  } else {
                    return Column(
                      children: [
                        if (_showSMCOverlay && _smcOverlayData != null) _buildSMCLayerIndicator(),
                        Expanded(flex: 48, child: _buildChartArea()),
                        const Divider(),
                        Expanded(flex: 30, child: _buildBottomDock()),
                        const Divider(),
                        Expanded(flex: 22, child: _buildApexAIPanel()),
                      ],
                    );
                  }
                },
              ),
            ),
            const Divider(),
            _buildBottomStatusBar(),
          ],
        ),
      ),
    );
  }

  // --------------------------------------------------------------------------
  // Unified Header: Market Switcher, Symbol Dropdown, 24h Ticker & Timeframe
  // --------------------------------------------------------------------------
  Widget _buildProUnifiedHeader() {
    final isPos = _change24h >= 0;
    final changeColor = isPos ? AppColors.bullish : AppColors.bearish;

    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
      child: Row(
        children: [
          // Market Switcher Chips
          _marketTypeBadge('CRYPTO', 'crypto'),
          const SizedBox(width: 4),
          _marketTypeBadge('FOREX & GOLD', 'forex'),
          const SizedBox(width: 4),
          _marketTypeBadge('STOCKS', 'stock'),

          const SizedBox(width: 12),
          Container(width: 1, height: 20, color: AppColors.border),
          const SizedBox(width: 12),

          // Symbol Selector Dropdown
          PopupMenuButton<String>(
            initialValue: _selectedSymbol,
            tooltip: 'Select Pair / Symbol',
            color: const Color(0xFF1E2533),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(8),
              side: const BorderSide(color: AppColors.border),
            ),
            onSelected: (s) {
              setState(() => _selectedSymbol = s);
              _fetchChartData();
            },
            itemBuilder: (context) => _symbols.map((s) {
              final isSel = s == _selectedSymbol;
              return PopupMenuItem<String>(
                value: s,
                height: 38,
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      s,
                      style: TextStyle(
                        fontWeight: isSel ? FontWeight.bold : FontWeight.w600,
                        color: isSel ? AppColors.bullish : Colors.white,
                        fontSize: 13,
                      ),
                    ),
                    if (isSel)
                      const Icon(Icons.check, size: 16, color: AppColors.bullish),
                  ],
                ),
              );
            }).toList(),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
              decoration: BoxDecoration(
                color: const Color(0xFF1E2533),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: AppColors.border),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    _selectedSymbol,
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 14, color: Colors.white),
                  ),
                  const SizedBox(width: 4),
                  const Icon(Icons.arrow_drop_down, color: Colors.white70, size: 18),
                ],
              ),
            ),
          ),

          const SizedBox(width: 12),

          // Live Price
          Text(
            _lastPrice > 0
                ? (_lastPrice < 10 ? _lastPrice.toStringAsFixed(4) : _lastPrice.toStringAsFixed(2))
                : '---',
            style: TextStyle(
              fontSize: 16,
              fontWeight: FontWeight.bold,
              color: changeColor,
              fontFamily: 'monospace',
            ),
          ),
          const SizedBox(width: 6),

          // 24h Change Badge
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
            decoration: BoxDecoration(
              color: changeColor.withOpacity(0.15),
              borderRadius: BorderRadius.circular(4),
            ),
            child: Text(
              '${isPos ? '+' : ''}${_change24h.toStringAsFixed(2)}%',
              style: TextStyle(color: changeColor, fontSize: 11, fontWeight: FontWeight.bold),
            ),
          ),

          const SizedBox(width: 14),

          // 24h High / Low / Volume Stats
          _tickerStat('24h High', _high24h.toStringAsFixed(2)),
          const SizedBox(width: 12),
          _tickerStat('24h Low', _low24h.toStringAsFixed(2)),
          const SizedBox(width: 12),
          _tickerStat('24h Vol', _formatVolume(_vol24h)),

          const SizedBox(width: 14),
          Container(width: 1, height: 20, color: AppColors.border),
          const SizedBox(width: 14),

          // Timeframe Dropdown
          PopupMenuButton<String>(
            initialValue: _selectedTimeframe,
            tooltip: 'Select Timeframe',
            color: const Color(0xFF1E2533),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(8),
              side: const BorderSide(color: AppColors.border),
            ),
            onSelected: (tf) {
              setState(() => _selectedTimeframe = tf);
              _fetchChartData();
            },
            itemBuilder: (context) => _timeframes.map((tf) {
              final isSel = tf == _selectedTimeframe;
              return PopupMenuItem<String>(
                value: tf,
                height: 36,
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      tf.toUpperCase(),
                      style: TextStyle(
                        fontWeight: isSel ? FontWeight.bold : FontWeight.normal,
                        color: isSel ? AppColors.bullish : Colors.white,
                        fontSize: 13,
                      ),
                    ),
                    if (isSel)
                      const Icon(Icons.check, size: 16, color: AppColors.bullish),
                  ],
                ),
              );
            }).toList(),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
              decoration: BoxDecoration(
                color: const Color(0xFF1E2533),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: AppColors.border),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  const Icon(Icons.schedule, size: 14, color: Colors.white70),
                  const SizedBox(width: 4),
                  Text(
                    _selectedTimeframe.toUpperCase(),
                    style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 12, color: Colors.white),
                  ),
                  const SizedBox(width: 2),
                  const Icon(Icons.arrow_drop_down, color: Colors.white70, size: 16),
                ],
              ),
            ),
          ),

          const SizedBox(width: 8),

          // LuxAlgo SMC Toggle
          GestureDetector(
            onTap: () => setState(() => _showSMCOverlay = !_showSMCOverlay),
            child: Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
              decoration: BoxDecoration(
                color: _showSMCOverlay ? const Color(0xFF2E82FE).withOpacity(0.15) : const Color(0xFF1E2533),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(color: _showSMCOverlay ? const Color(0xFF2E82FE) : AppColors.border),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Icon(
                    _showSMCOverlay ? Icons.visibility : Icons.visibility_off,
                    size: 14,
                    color: _showSMCOverlay ? const Color(0xFF2E82FE) : AppColors.textMuted,
                  ),
                  const SizedBox(width: 4),
                  Text(
                    'LuxAlgo SMC',
                    style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                      color: _showSMCOverlay ? Colors.white : AppColors.textMuted,
                    ),
                  ),
                ],
              ),
            ),
          ),

          const Spacer(),

          // Live Pulse Dot
          Container(
            width: 7,
            height: 7,
            decoration: const BoxDecoration(
              color: AppColors.bullish,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 5),
          const Text('LIVE', style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: AppColors.bullish)),

          const SizedBox(width: 10),
          IconButton(
            icon: const Icon(Icons.refresh, size: 18, color: Colors.white70),
            tooltip: 'Reload Data',
            onPressed: _fetchChartData,
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(),
          ),
        ],
      ),
    );
  }

  Widget _marketTypeBadge(String title, String market) {
    final isSelected = _selectedMarket == market;
    return GestureDetector(
      onTap: () {
        setState(() {
          _selectedMarket = market;
          _selectedSymbol = _symbols.first;
        });
        _fetchChartData();
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: isSelected ? const Color(0xFF2E82FE).withOpacity(0.2) : Colors.transparent,
          borderRadius: BorderRadius.circular(4),
          border: Border.all(
            color: isSelected ? const Color(0xFF2E82FE) : const Color(0xFF232A38),
          ),
        ),
        child: Text(
          title,
          style: TextStyle(
            fontSize: 10,
            fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
            color: isSelected ? Colors.white : AppColors.textMuted,
          ),
        ),
      ),
    );
  }

  Widget _tickerStat(String label, String value) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: const TextStyle(fontSize: 10, color: AppColors.textMuted)),
        const SizedBox(height: 2),
        Text(value, style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, color: Colors.white)),
      ],
    );
  }

  String _formatVolume(double vol) {
    if (vol >= 1000000000) return '${(vol / 1000000000).toStringAsFixed(2)}B';
    if (vol >= 1000000) return '${(vol / 1000000).toStringAsFixed(2)}M';
    if (vol >= 1000) return '${(vol / 1000).toStringAsFixed(1)}K';
    return vol.toStringAsFixed(0);
  }

  // --------------------------------------------------------------------------
  // SMC Active Zones Indicator Banner
  // --------------------------------------------------------------------------
  Widget _buildSMCLayerIndicator() {
    final ob = _smcOverlayData?['order_block'] as Map<String, dynamic>?;
    final fvg = _smcOverlayData?['fvg'] as Map<String, dynamic>?;
    final eq = _smcOverlayData?['equilibrium'] as num?;

    return Container(
      color: const Color(0xFF10141D),
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      child: Row(
        children: [
          const Text('SMC ZONES:', style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: AppColors.textMuted)),
          const SizedBox(width: 10),
          if (ob != null) ...[
            _smcZonePill(
              'OB: ${ob['bottom']?.toStringAsFixed(1)} - ${ob['top']?.toStringAsFixed(1)}',
              AppColors.orderBlock,
            ),
            const SizedBox(width: 8),
          ],
          if (fvg != null) ...[
            _smcZonePill(
              'FVG: ${fvg['bottom']?.toStringAsFixed(1)} - ${fvg['top']?.toStringAsFixed(1)}',
              AppColors.fvg,
            ),
            const SizedBox(width: 8),
          ],
          if (eq != null) ...[
            _smcZonePill('EQ: ${eq.toStringAsFixed(1)}', AppColors.eqLine),
          ],
          const Spacer(),
          const Text('HTF: ', style: TextStyle(fontSize: 10, color: AppColors.textMuted)),
          Text(_selectedHtfTimeframe.toUpperCase(), style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white)),
        ],
      ),
    );
  }

  Widget _smcZonePill(String text, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withOpacity(0.15),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withOpacity(0.4)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(width: 6, height: 6, decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
          const SizedBox(width: 5),
          Text(text, style: TextStyle(fontSize: 11, color: color, fontWeight: FontWeight.bold)),
        ],
      ),
    );
  }

  // --------------------------------------------------------------------------
  // Chart Area
  // --------------------------------------------------------------------------
  Widget _buildChartArea() {
    if (_isLoading) {
      return Container(
        color: AppColors.background,
        child: const Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              CircularProgressIndicator(color: AppColors.bullish, strokeWidth: 2),
              SizedBox(height: 16),
              Text('Connecting to market data feed...', style: TextStyle(color: AppColors.textMuted, fontSize: 13)),
            ],
          ),
        ),
      );
    }

    if (_errorMessage != null || _candles.isEmpty) {
      return Container(
        color: AppColors.background,
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.signal_cellular_connected_no_internet_4_bar, size: 48, color: AppColors.bearish),
              const SizedBox(height: 12),
              Text(_errorMessage ?? 'No candle data available.', style: const TextStyle(color: Colors.white70)),
              const SizedBox(height: 16),
              ElevatedButton.icon(
                onPressed: _fetchChartData,
                icon: const Icon(Icons.refresh),
                label: const Text('Retry Connection'),
                style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF1E2533)),
              ),
            ],
          ),
        ),
      );
    }

    return Container(
      color: AppColors.background,
      child: SMCInteractiveChart(
        candles: _candles,
        smcData: _smcOverlayData,
        openPositions: _openPositions,
        currentPrice: _lastPrice,
        showOverlay: _showSMCOverlay,
        symbol: _selectedSymbol,
      ),
    );
  }

  // --------------------------------------------------------------------------
  // Apex AI Chat Messaging & Context Injection
  // --------------------------------------------------------------------------
  Future<void> _sendChatMessage([String? customText, TextEditingController? sourceCtrl, ScrollController? scrollCtrl]) async {
    final ctrl = sourceCtrl ?? _chatInputCtrl;
    final text = (customText ?? ctrl.text).trim();
    if (text.isEmpty) return;

    if (customText == null) ctrl.clear();

    setState(() {
      _chatMessages.add({'role': 'user', 'content': text});
      _chatMessages.add({'role': 'assistant', 'content': 'Apex กำลังวิเคราะห์โครงสร้าง SMC ของ $_selectedSymbol...'});
      _isChatLoading = true;
    });

    try {
      final dio = Dio();
      final chatHistory = _chatMessages
          .where((m) => !m['content']!.startsWith('Apex กำลังวิเคราะห์'))
          .map((m) => {'role': m['role'], 'content': m['content']})
          .toList();

      final resp = await dio.post(
        AppApi.url('/api/v1/settings/llm/chat'),
        data: {
          'messages': chatHistory,
          'context': {
            'symbol': _selectedSymbol,
            'timeframe': _selectedTimeframe,
            'price': _lastPrice,
            'bias': _smcOverlayData?['bias'] ?? 'neutral',
            'confluence': _smcOverlayData?['confluence'] ?? 0,
            'open_positions': _openPositions.length,
          }
        },
      );

      final reply = resp.data['response'] as String? ?? 'ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล';

      setState(() {
        _chatMessages.removeLast();
        _chatMessages.add({'role': 'assistant', 'content': reply});
        _isChatLoading = false;
      });
    } catch (e) {
      setState(() {
        _chatMessages.removeLast();
        _chatMessages.add({
          'role': 'assistant',
          'content': '⚠️ ไม่สามารถเชื่อมต่อกับ AI Advisor ได้: $e\nกรุณาตรวจสอบการตั้งค่า Provider ในหน้า Settings',
        });
        _isChatLoading = false;
      });
    }

    WidgetsBinding.instance.addPostFrameCallback((_) {
      final s = scrollCtrl ?? _chatScrollCtrl;
      if (s.hasClients) {
        s.animateTo(
          s.position.maxScrollExtent,
          duration: const Duration(milliseconds: 250),
          curve: Curves.easeOut,
        );
      }
    });
  }

  // --------------------------------------------------------------------------
  // Pop-up Expanded Chat Modal
  // --------------------------------------------------------------------------
  void _showExpandedChatModal() {
    final modalTextCtrl = TextEditingController();
    final modalScrollCtrl = ScrollController();

    showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setModalState) {
          return Dialog(
            backgroundColor: const Color(0xFF141923),
            shape: RoundedRectangleBorder(
              borderRadius: BorderRadius.circular(16),
              side: const BorderSide(color: Color(0xFF2E82FE), width: 1.2),
            ),
            insetPadding: const EdgeInsets.symmetric(horizontal: 24, vertical: 24),
            child: SizedBox(
              width: 900,
              height: 680,
              child: Column(
                children: [
                  // Modal Header
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 20, vertical: 14),
                    decoration: const BoxDecoration(
                      color: Color(0xFF1B2333),
                      borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
                    ),
                    child: Row(
                      children: [
                        CircleAvatar(
                          backgroundColor: AppColors.bullish.withOpacity(0.2),
                          radius: 16,
                          child: const Text('A', style: TextStyle(color: AppColors.bullish, fontWeight: FontWeight.bold)),
                        ),
                        const SizedBox(width: 12),
                        Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            const Text('Apex AI Institutional Advisor', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold, fontSize: 16)),
                            Text('Interactive Live Terminal • $_selectedSymbol ($_selectedTimeframe) \$${_lastPrice.toStringAsFixed(2)}', style: const TextStyle(color: Colors.white54, fontSize: 12)),
                          ],
                        ),
                        const Spacer(),
                        IconButton(
                          icon: const Icon(Icons.close, color: Colors.white70),
                          tooltip: 'ย่อหน้าต่างกลับสู่กราฟ',
                          onPressed: () => Navigator.pop(ctx),
                        ),
                      ],
                    ),
                  ),

                  // Prompt quick chips
                  Container(
                    color: const Color(0xFF10141D),
                    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                    child: SingleChildScrollView(
                      scrollDirection: Axis.horizontal,
                      child: Row(
                        children: [
                          _modalQuickChip('📊 สรุปโครงสร้าง SMC', () async {
                            await _sendChatMessage('ช่วยสรุปโครงสร้าง SMC และ Invalidation ของ $_selectedSymbol ให้หน่อย', modalTextCtrl, modalScrollCtrl);
                            setModalState(() {});
                          }),
                          const SizedBox(width: 8),
                          _modalQuickChip('🎯 แนวต้าน & Take Profit', () async {
                            await _sendChatMessage('$_selectedSymbol มีแนวต้านสำคัญหรือเป้า TP ตรงไหนบ้าง', modalTextCtrl, modalScrollCtrl);
                            setModalState(() {});
                          }),
                          const SizedBox(width: 8),
                          _modalQuickChip('🛑 จุด Stop Loss ที่ปลอดภัย', () async {
                            await _sendChatMessage('ถ้าจะเปิดไม้ $_selectedSymbol ตอนนี้ ควรวาง Stop Loss ที่จุดไหนตามโครงสร้าง', modalTextCtrl, modalScrollCtrl);
                            setModalState(() {});
                          }),
                          const SizedBox(width: 8),
                          _modalQuickChip('⚖️ ประเมิน R:R & Risk', () async {
                            await _sendChatMessage('ช่วยประเมินความคุ้มค่า Risk/Reward และความเสี่ยงของ $_selectedSymbol ในจังหวะนี้', modalTextCtrl, modalScrollCtrl);
                            setModalState(() {});
                          }),
                        ],
                      ),
                    ),
                  ),

                  // Message List
                  Expanded(
                    child: ListView.builder(
                      controller: modalScrollCtrl,
                      padding: const EdgeInsets.all(16),
                      itemCount: _chatMessages.length,
                      itemBuilder: (ctx, i) {
                        final msg = _chatMessages[i];
                        final isUser = msg['role'] == 'user';
                        return Align(
                          alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
                          child: Container(
                            margin: const EdgeInsets.only(bottom: 12),
                            padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                            constraints: const BoxConstraints(maxWidth: 650),
                            decoration: BoxDecoration(
                              color: isUser ? const Color(0xFF2E82FE).withOpacity(0.2) : const Color(0xFF1E2533),
                              borderRadius: BorderRadius.circular(10),
                              border: isUser ? Border.all(color: const Color(0xFF2E82FE).withOpacity(0.5)) : Border.all(color: AppColors.border),
                            ),
                            child: Text(
                              msg['content'] ?? '',
                              style: TextStyle(
                                color: isUser ? Colors.white : const Color(0xFFE2E8F0),
                                fontSize: 13,
                                height: 1.5,
                              ),
                            ),
                          ),
                        );
                      },
                    ),
                  ),

                  // Modal Input Row
                  Container(
                    padding: const EdgeInsets.all(12),
                    decoration: const BoxDecoration(
                      color: Color(0xFF1B2333),
                      borderRadius: BorderRadius.vertical(bottom: Radius.circular(16)),
                    ),
                    child: Row(
                      children: [
                        Expanded(
                          child: TextField(
                            controller: modalTextCtrl,
                            style: const TextStyle(color: Colors.white, fontSize: 14),
                            decoration: InputDecoration(
                              hintText: 'ถาม Apex เกี่ยวกับ $_selectedSymbol ณ ราคา \$${_lastPrice.toStringAsFixed(2)}...',
                              hintStyle: const TextStyle(color: Colors.white38),
                              border: InputBorder.none,
                              contentPadding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                            ),
                            onSubmitted: (_) async {
                              await _sendChatMessage(null, modalTextCtrl, modalScrollCtrl);
                              setModalState(() {});
                            },
                          ),
                        ),
                        IconButton(
                          icon: _isChatLoading
                              ? const SizedBox(width: 18, height: 18, child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.bullish))
                              : const Icon(Icons.send, color: AppColors.bullish),
                          onPressed: () async {
                            await _sendChatMessage(null, modalTextCtrl, modalScrollCtrl);
                            setModalState(() {});
                          },
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }

  Widget _modalQuickChip(String title, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: const Color(0xFF1E2533),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: const Color(0xFF2E82FE).withOpacity(0.4)),
        ),
        child: Text(title, style: const TextStyle(fontSize: 11, color: Color(0xFF93C5FD), fontWeight: FontWeight.w500)),
      ),
    );
  }

  // --------------------------------------------------------------------------
  // Unified Bottom Dock: Apex AI Chat + Open Positions Table + Expand Pop-up
  // --------------------------------------------------------------------------
  Widget _buildBottomDock() {
    return Container(
      color: AppColors.surface,
      child: Column(
        children: [
          // Dock Tab Bar Header
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            color: const Color(0xFF121620),
            child: Row(
              children: [
                // Tab 1: Apex AI Chat
                _dockTabButton(
                  title: '💬 APEX AI ADVISOR CHAT',
                  isSelected: _bottomDockTab == 0,
                  badgeColor: AppColors.bullish,
                  onTap: () => setState(() => _bottomDockTab = 0),
                ),
                const SizedBox(width: 8),
                // Tab 2: Open Positions
                _dockTabButton(
                  title: '📌 OPEN POSITIONS (${_openPositions.length})',
                  isSelected: _bottomDockTab == 1,
                  badgeColor: const Color(0xFF00E5FF),
                  onTap: () => setState(() => _bottomDockTab = 1),
                ),
                const Spacer(),
                // Expand / Pop-up Full Dialog Button
                ElevatedButton.icon(
                  onPressed: _showExpandedChatModal,
                  icon: const Icon(Icons.open_in_full, size: 13, color: Colors.white),
                  label: const Text('Pop-up ขยายแชท', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white)),
                  style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF2E82FE).withOpacity(0.3),
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                  ),
                ),
                const SizedBox(width: 8),
                IconButton(
                  icon: const Icon(Icons.refresh, size: 16, color: Colors.white70),
                  tooltip: 'Refresh',
                  padding: EdgeInsets.zero,
                  constraints: const BoxConstraints(),
                  onPressed: () {
                    _fetchOpenPositions();
                    _fetchChartData();
                  },
                ),
              ],
            ),
          ),
          const Divider(),
          // Dock Content
          Expanded(
            child: _bottomDockTab == 0 ? _buildApexChatDock() : _buildPositionsTable(),
          ),
        ],
      ),
    );
  }

  Widget _dockTabButton({
    required String title,
    required bool isSelected,
    required Color badgeColor,
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: isSelected ? badgeColor.withOpacity(0.18) : Colors.transparent,
          borderRadius: BorderRadius.circular(6),
          border: Border.all(color: isSelected ? badgeColor : Colors.transparent),
        ),
        child: Text(
          title,
          style: TextStyle(
            fontSize: 11,
            fontWeight: isSelected ? FontWeight.bold : FontWeight.w600,
            color: isSelected ? Colors.white : AppColors.textMuted,
          ),
        ),
      ),
    );
  }

  // --------------------------------------------------------------------------
  // Tab 1: Apex AI Embedded Chat Dock
  // --------------------------------------------------------------------------
  Widget _buildApexChatDock() {
    return Column(
      children: [
        // Quick suggestions row
        Container(
          color: const Color(0xFF10141D),
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _dockQuickPrompt('📊 สรุป SMC $_selectedSymbol', 'ช่วยสรุปโครงสร้าง SMC และ Invalidation ของ $_selectedSymbol ให้หน่อย'),
                const SizedBox(width: 6),
                _dockQuickPrompt('🎯 เป้า Take Profit', '$_selectedSymbol มีเป้าทำกำไร TP ตามโครงสร้างตรงไหนบ้าง'),
                const SizedBox(width: 6),
                _dockQuickPrompt('🛑 วาง Stop Loss', 'จุด Stop Loss ที่ปลอดภัยตามโครงสร้าง SMC ของ $_selectedSymbol อยู่ตรงไหน'),
                const SizedBox(width: 6),
                _dockQuickPrompt('⚖️ ประเมิน R:R', 'ช่วยประเมินความคุ้มค่า Risk/Reward ของ $_selectedSymbol'),
              ],
            ),
          ),
        ),

        // Scrollable Chat Messages
        Expanded(
          child: ListView.builder(
            controller: _chatScrollCtrl,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            itemCount: _chatMessages.length,
            itemBuilder: (ctx, i) {
              final msg = _chatMessages[i];
              final isUser = msg['role'] == 'user';
              return Align(
                alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
                child: Container(
                  margin: const EdgeInsets.only(bottom: 6),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  constraints: BoxConstraints(maxWidth: MediaQuery.of(context).size.width * 0.55),
                  decoration: BoxDecoration(
                    color: isUser ? const Color(0xFF2E82FE).withOpacity(0.2) : const Color(0xFF1E2533),
                    borderRadius: BorderRadius.circular(8),
                    border: isUser ? Border.all(color: const Color(0xFF2E82FE).withOpacity(0.4)) : null,
                  ),
                  child: Text(
                    msg['content'] ?? '',
                    style: TextStyle(
                      color: isUser ? Colors.white : const Color(0xFFE2E8F0),
                      fontSize: 12,
                      height: 1.4,
                    ),
                  ),
                ),
              );
            },
          ),
        ),

        // Bottom Chat Input Bar
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
          color: const Color(0xFF121620),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _chatInputCtrl,
                  style: const TextStyle(color: Colors.white, fontSize: 12),
                  decoration: InputDecoration(
                    hintText: 'ถาม Apex AI เกี่ยวกับ $_selectedSymbol (\$${_lastPrice.toStringAsFixed(2)})...',
                    hintStyle: const TextStyle(color: Colors.white30, fontSize: 12),
                    border: InputBorder.none,
                    isDense: true,
                    contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                  ),
                  onSubmitted: (_) => _sendChatMessage(),
                ),
              ),
              IconButton(
                icon: _isChatLoading
                    ? const SizedBox(width: 14, height: 14, child: CircularProgressIndicator(strokeWidth: 2, color: AppColors.bullish))
                    : const Icon(Icons.send, size: 16, color: AppColors.bullish),
                padding: EdgeInsets.zero,
                constraints: const BoxConstraints(),
                onPressed: () => _sendChatMessage(),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Widget _dockQuickPrompt(String label, String prompt) {
    return GestureDetector(
      onTap: () => _sendChatMessage(prompt),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
        decoration: BoxDecoration(
          color: const Color(0xFF1E2533),
          borderRadius: BorderRadius.circular(4),
          border: Border.all(color: const Color(0xFF2E82FE).withOpacity(0.3)),
        ),
        child: Text(label, style: const TextStyle(fontSize: 10, color: Color(0xFF93C5FD))),
      ),
    );
  }

  // --------------------------------------------------------------------------
  // Tab 2: Open Positions Table
  // --------------------------------------------------------------------------
  Widget _buildPositionsTable() {
    if (_openPositions.isEmpty) {
      return const Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.hourglass_empty, size: 24, color: AppColors.textMuted),
            SizedBox(height: 6),
            Text('No open positions.', style: TextStyle(fontSize: 12, color: AppColors.textMuted)),
            SizedBox(height: 2),
            Text('Click [BUY / LONG] or [SELL / SHORT] on the right to open a paper trade.', style: TextStyle(fontSize: 11, color: Colors.white38)),
          ],
        ),
      );
    }

    return ListView.separated(
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      itemCount: _openPositions.length,
      separatorBuilder: (_, __) => const Divider(color: AppColors.border),
      itemBuilder: (context, index) {
        final pos = _openPositions[index];
        final tradeId = pos['id']?.toString() ?? '';
        final sym = pos['symbol']?.toString() ?? 'BTC/USDT';
        final dir = (pos['direction']?.toString() ?? 'long').toLowerCase();
        final isLong = dir == 'long';
        final color = isLong ? AppColors.bullish : AppColors.bearish;

        final entry = (pos['entry'] as num?)?.toDouble() ?? 100.0;
        final size = (pos['size'] as num?)?.toDouble() ?? (pos['position_size'] as num?)?.toDouble() ?? 1.0;

        // Correct symbol-specific live Mark Price (not the currently viewed chart symbol)
        final markPrice = _getMarkPriceForSymbol(sym, entry);
        final pnlPct = isLong ? ((markPrice - entry) / entry) * 100 : ((entry - markPrice) / entry) * 100;
        final pnlVal = isLong ? (markPrice - entry) * size : (entry - markPrice) * size;
        final isProfit = pnlVal >= 0;

        return Row(
          children: [
            // Side Badge
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: color.withOpacity(0.15),
                borderRadius: BorderRadius.circular(4),
                border: Border.all(color: color),
              ),
              child: Text(
                dir.toUpperCase(),
                style: TextStyle(color: color, fontSize: 10, fontWeight: FontWeight.bold),
              ),
            ),
            const SizedBox(width: 8),
            // Symbol (Clickable to switch chart to this symbol)
            GestureDetector(
              onTap: () => _switchToSymbol(sym),
              child: MouseRegion(
                cursor: SystemMouseCursors.click,
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text(
                      sym,
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Color(0xFF93C5FD), decoration: TextDecoration.underline),
                    ),
                    const SizedBox(width: 3),
                    const Icon(Icons.arrow_outward, size: 11, color: Color(0xFF93C5FD)),
                  ],
                ),
              ),
            ),
            const SizedBox(width: 16),
            // Size
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Size', style: TextStyle(fontSize: 9, color: AppColors.textMuted)),
                Text('$size', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600)),
              ],
            ),
            const SizedBox(width: 16),
            // Entry
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Entry Price', style: TextStyle(fontSize: 9, color: AppColors.textMuted)),
                Text('\$${entry.toStringAsFixed(2)}', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, fontFamily: 'monospace')),
              ],
            ),
            const SizedBox(width: 16),
            // Mark Price
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text('Mark Price', style: TextStyle(fontSize: 9, color: AppColors.textMuted)),
                Text('\$${markPrice.toStringAsFixed(2)}', style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600, fontFamily: 'monospace')),
              ],
            ),
            const Spacer(),
            // PnL
            Column(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(
                  '${isProfit ? '+' : ''}\$${pnlVal.toStringAsFixed(2)}',
                  style: TextStyle(
                    fontSize: 13,
                    fontWeight: FontWeight.bold,
                    color: isProfit ? AppColors.bullish : AppColors.bearish,
                    fontFamily: 'monospace',
                  ),
                ),
                Text(
                  '(${isProfit ? '+' : ''}${pnlPct.toStringAsFixed(2)}%)',
                  style: TextStyle(
                    fontSize: 11,
                    color: isProfit ? AppColors.bullish : AppColors.bearish,
                  ),
                ),
              ],
            ),
            const SizedBox(width: 16),
            // Close Button
            ElevatedButton(
              onPressed: () => _closePosition(tradeId),
              style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF232A38),
                foregroundColor: Colors.white70,
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                minimumSize: Size.zero,
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(4)),
              ),
              child: const Text('Close ✕', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold)),
            ),
          ],
        );
      },
    );
  }

  // --------------------------------------------------------------------------
  // Right Panel: Apex AI Institutional Advisor & Quick Execution
  // --------------------------------------------------------------------------
  Widget _buildApexAIPanel() {
    final bias = (_smcOverlayData?['bias'] as String? ?? 'BULLISH').toUpperCase();
    final isBull = bias == 'BULLISH';
    final isBear = bias == 'BEARISH';
    final signalColor = isBull ? AppColors.bullish : (isBear ? AppColors.bearish : AppColors.neutral);

    final confluence = (_smcOverlayData?['confluence'] as num? ?? 8).toInt();
    final inDiscount = _smcOverlayData?['in_discount'] == true;
    final inPremium = _smcOverlayData?['in_premium'] == true;
    final zoneName = inDiscount ? 'DEEP DISCOUNT' : (inPremium ? 'PREMIUM ZONE' : 'EQUILIBRIUM');

    final entryPrice = _lastPrice > 0 ? _lastPrice : 64200.0;
    final slPrice = isBull ? entryPrice * 0.992 : entryPrice * 1.008;
    final tp1Price = isBull ? entryPrice * 1.018 : entryPrice * 0.982;
    final tp2Price = isBull ? entryPrice * 1.032 : entryPrice * 0.968;

    return Container(
      color: AppColors.panel,
      height: double.infinity,
      child: SingleChildScrollView(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            // Apex AI Header Badge
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                    color: const Color(0xFF2E82FE).withOpacity(0.2),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: const Icon(Icons.psychology, size: 18, color: Color(0xFF2E82FE)),
                ),
                const SizedBox(width: 8),
                const Text(
                  'APEX AI ADVISOR',
                  style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, letterSpacing: 1.1),
                ),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                  decoration: BoxDecoration(
                    color: signalColor.withOpacity(0.15),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: signalColor),
                  ),
                  child: Text(
                    isBull ? '🟢 LONG BIAS' : (isBear ? '🔴 SHORT BIAS' : '⚪ NEUTRAL'),
                    style: TextStyle(color: signalColor, fontWeight: FontWeight.bold, fontSize: 11),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 12),

            // Confluence Score Gauge
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.surface,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: AppColors.border),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text('Institutional Confluence', style: TextStyle(fontSize: 12, color: AppColors.textMuted)),
                      Text('$confluence / 10', style: TextStyle(fontSize: 14, fontWeight: FontWeight.bold, color: signalColor)),
                    ],
                  ),
                  const SizedBox(height: 8),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: LinearProgressIndicator(
                      value: (confluence / 10).clamp(0.0, 1.0),
                      backgroundColor: const Color(0xFF232A38),
                      valueColor: AlwaysStoppedAnimation<Color>(signalColor),
                      minHeight: 6,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      _checkChip('HTF Trend', isBull ? 'Bullish' : 'Bearish', true),
                      _checkChip('Market Zone', zoneName, inDiscount || inPremium),
                      _checkChip('Liquidity', 'Swept', true),
                    ],
                  ),
                ],
              ),
            ),
            const SizedBox(height: 12),

            // Trade Execution Levels (Entry / SL / TP)
            const Text('EXECUTION BLUEPRINT', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: AppColors.textMuted)),
            const SizedBox(height: 6),
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.surface,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: AppColors.border),
              ),
              child: Column(
                children: [
                  _levelRow('Entry Zone', '\$${entryPrice.toStringAsFixed(2)}', Colors.white),
                  const Divider(),
                  _levelRow('Invalidation (SL)', '\$${slPrice.toStringAsFixed(2)}', AppColors.bearish, sub: 'Below Order Block'),
                  const Divider(),
                  _levelRow('Take Profit 1', '\$${tp1Price.toStringAsFixed(2)}', AppColors.bullish, sub: '1.8R Ratio'),
                  const Divider(),
                  _levelRow('Major Target (TP2)', '\$${tp2Price.toStringAsFixed(2)}', AppColors.bullish, sub: '3.2R Confluence Zone'),
                ],
              ),
            ),
            const SizedBox(height: 12),

            // AI Assessment Note
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFF1A2234),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: const Color(0xFF2E82FE).withOpacity(0.3)),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.info_outline, size: 16, color: Color(0xFF2E82FE)),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      isBull
                          ? 'SMC Analysis: Price swept liquidity below equal lows and is reacting to the Bullish Order Block in discount. Risk defined at 1.0% with 2.5+ R:R potential.'
                          : 'SMC Analysis: Bearish structure confirmed after premium liquidity mitigation. Protect capital with strict SL placement.',
                      style: const TextStyle(fontSize: 11, color: Colors.white70, height: 1.4),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),

            // Quick Execution Action Buttons
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: () => _executePaperOrder('long'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.bullish,
                      foregroundColor: Colors.black,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
                    ),
                    child: const Text('BUY / LONG', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13)),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: ElevatedButton(
                    onPressed: () => _executePaperOrder('short'),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.bearish,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
                    ),
                    child: const Text('SELL / SHORT', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13)),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 14),

            // Risk Sizing & Auto-Monitor Status
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFF131722),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: AppColors.border),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Row(
                    children: [
                      Icon(Icons.shield_outlined, size: 14, color: Color(0xFF00E5FF)),
                      SizedBox(width: 6),
                      Text('INSTITUTIONAL RISK CONTROL', style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: Color(0xFF00E5FF))),
                    ],
                  ),
                  const SizedBox(height: 8),
                  const Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Risk / Trade Limit:', style: TextStyle(fontSize: 11, color: Colors.white54)),
                      Text('1.0% (\$100.00)', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.white)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  const Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Max Daily Drawdown:', style: TextStyle(fontSize: 11, color: Colors.white54)),
                      Text('3.0% (\$300.00)', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: AppColors.neutral)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text('Auto TP/SL Monitor:', style: TextStyle(fontSize: 11, color: Colors.white54)),
                      Row(
                        children: [
                          Container(width: 6, height: 6, decoration: const BoxDecoration(color: AppColors.bullish, shape: BoxShape.circle)),
                          const SizedBox(width: 4),
                          const Text('24/7 ACTIVE', style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: AppColors.bullish)),
                        ],
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _checkChip(String label, String value, bool valid) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label, style: const TextStyle(fontSize: 9, color: AppColors.textMuted)),
        const SizedBox(height: 2),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(valid ? Icons.check_circle : Icons.cancel, size: 11, color: valid ? AppColors.bullish : AppColors.textMuted),
            const SizedBox(width: 3),
            Text(value, style: const TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: Colors.white)),
          ],
        ),
      ],
    );
  }

  Widget _levelRow(String label, String value, Color color, {String? sub}) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceBetween,
        children: [
          Text(label, style: const TextStyle(fontSize: 12, color: AppColors.textMuted)),
          Column(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              Text(value, style: TextStyle(fontSize: 13, fontWeight: FontWeight.bold, color: color, fontFamily: 'monospace')),
              if (sub != null) Text(sub, style: const TextStyle(fontSize: 9, color: AppColors.textMuted)),
            ],
          ),
        ],
      ),
    );
  }

  // --------------------------------------------------------------------------
  // Bottom Status Bar
  // --------------------------------------------------------------------------
  Widget _buildBottomStatusBar() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: Row(
        children: [
          const Icon(Icons.cloud_done, size: 14, color: AppColors.bullish),
          const SizedBox(width: 6),
          Text(
            'Feed: $_selectedExchange.com  •  Latency: 28ms  •  Mode: Paper Trading',
            style: const TextStyle(fontSize: 11, color: AppColors.textMuted),
          ),
          const Spacer(),
          Text(
            'Last updated: ${DateTime.now().toLocal().toString().split('.').first}',
            style: const TextStyle(fontSize: 11, color: AppColors.textMuted),
          ),
        ],
      ),
    );
  }
}
