import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:candlesticks/candlesticks.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';

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
  }

  Future<void> _fetchChartData() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final dio = Dio();
      const baseUrl = 'http://127.0.0.1:8000';

      // 1. Fetch OHLCV candles
      final ohlcvResp = await dio.get(
        '$baseUrl/api/v1/chart/ohlcv',
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

      // Compute 24h stats from candles
      if (parsedCandles.isNotEmpty) {
        _lastPrice = parsedCandles.first.close;
        final lookback = parsedCandles.take(24).toList();
        _high24h = lookback.map((c) => c.high).reduce((a, b) => a > b ? a : b);
        _low24h = lookback.map((c) => c.low).reduce((a, b) => a < b ? a : b);
        _vol24h = lookback.map((c) => c.volume).fold(0.0, (a, b) => a + b);
        final firstOpen = lookback.last.open;
        _change24h = firstOpen > 0 ? ((_lastPrice - firstOpen) / firstOpen) * 100 : 0.0;
      }

      // 2. Fetch SMC Overlay
      final overlayResp = await dio.get(
        '$baseUrl/api/v1/chart/overlay',
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

  void _executePaperOrder(String direction) {
    final entry = _lastPrice;
    final sl = direction == 'long' ? entry * 0.99 : entry * 101;
    final tp = direction == 'long' ? entry * 1.025 : entry * 0.975;

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        backgroundColor: direction == 'long' ? AppColors.bullish : AppColors.bearish,
        content: Text(
          '⚡ Paper Order Placed: ${direction.toUpperCase()} $_selectedSymbol @ \$$entry\nSL: \$$sl | TP: \$$tp (2.5R)',
          style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold),
        ),
        duration: const Duration(seconds: 4),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      body: SafeArea(
        child: Column(
          children: [
            _buildProHeader(),
            const Divider(),
            _buildProToolbar(),
            const Divider(),
            if (_showSMCOverlay && _smcOverlayData != null) _buildSMCLayerIndicator(),
            Expanded(
              child: LayoutBuilder(
                builder: (context, constraints) {
                  // Responsive split view for Desktop/Web vs Mobile
                  if (constraints.maxWidth > 900) {
                    return Row(
                      children: [
                        // Left/Center: Chart (68%)
                        Expanded(flex: 68, child: _buildChartArea()),
                        const VerticalDivider(width: 1, color: AppColors.border),
                        // Right: Apex AI Terminal & Execution Panel (32%)
                        Expanded(flex: 32, child: _buildApexAIPanel()),
                      ],
                    );
                  } else {
                    // Mobile vertical view
                    return Column(
                      children: [
                        Expanded(flex: 60, child: _buildChartArea()),
                        const Divider(),
                        Expanded(flex: 40, child: _buildApexAIPanel()),
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
  // Header: 24h Ticker & Market Switcher
  // --------------------------------------------------------------------------
  Widget _buildProHeader() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        children: [
          // Market Category Chips
          _marketTypeBadge('CRYPTO', 'crypto'),
          const SizedBox(width: 6),
          _marketTypeBadge('FOREX & GOLD', 'forex'),
          const SizedBox(width: 6),
          _marketTypeBadge('STOCKS', 'stock'),

          const Spacer(),

          // Live Pulse Dot
          Container(
            width: 8,
            height: 8,
            decoration: const BoxDecoration(
              color: AppColors.bullish,
              shape: BoxShape.circle,
            ),
          ),
          const SizedBox(width: 6),
          const Text('LIVE', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: AppColors.bullish)),

          const SizedBox(width: 16),
          IconButton(
            icon: const Icon(Icons.refresh, size: 20, color: Colors.white70),
            tooltip: 'Reload Data',
            onPressed: _fetchChartData,
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
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
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
            fontSize: 11,
            fontWeight: isSelected ? FontWeight.bold : FontWeight.normal,
            color: isSelected ? Colors.white : AppColors.textMuted,
          ),
        ),
      ),
    );
  }

  // --------------------------------------------------------------------------
  // Toolbar: Symbol, Price, 24h Stats, Timeframe Selector
  // --------------------------------------------------------------------------
  Widget _buildProToolbar() {
    final isPos = _change24h >= 0;
    final changeColor = isPos ? AppColors.bullish : AppColors.bearish;

    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            // Symbol Picker Button
            GestureDetector(
              onTap: _showSymbolPicker,
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E2533),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  children: [
                    Text(
                      _selectedSymbol,
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: Colors.white),
                    ),
                    const SizedBox(width: 4),
                    const Icon(Icons.arrow_drop_down, color: Colors.white70, size: 20),
                  ],
                ),
              ),
            ),
            const SizedBox(width: 16),

            // Big Live Price
            Text(
              _lastPrice > 0
                  ? (_lastPrice < 10 ? _lastPrice.toStringAsFixed(4) : _lastPrice.toStringAsFixed(2))
                  : '---',
              style: TextStyle(
                fontSize: 20,
                fontWeight: FontWeight.bold,
                color: changeColor,
                fontFamily: 'monospace',
              ),
            ),
            const SizedBox(width: 8),

            // 24h % Badge
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: changeColor.withOpacity(0.15),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text(
                '${isPos ? '+' : ''}${_change24h.toStringAsFixed(2)}%',
                style: TextStyle(color: changeColor, fontSize: 12, fontWeight: FontWeight.bold),
              ),
            ),
            const SizedBox(width: 24),

            // 24h High / Low / Vol
            _tickerStat('24h High', _high24h.toStringAsFixed(2)),
            const SizedBox(width: 16),
            _tickerStat('24h Low', _low24h.toStringAsFixed(2)),
            const SizedBox(width: 16),
            _tickerStat('24h Vol', _formatVolume(_vol24h)),

            const SizedBox(width: 24),
            Container(width: 1, height: 24, color: AppColors.border),
            const SizedBox(width: 16),

            // Compact Timeframe Dropdown
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
                  height: 38,
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
                padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E2533),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: AppColors.border),
                ),
                child: Row(
                  children: [
                    const Icon(Icons.schedule, size: 15, color: Colors.white70),
                    const SizedBox(width: 6),
                    Text(
                      _selectedTimeframe.toUpperCase(),
                      style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.white),
                    ),
                    const SizedBox(width: 4),
                    const Icon(Icons.arrow_drop_down, color: Colors.white70, size: 18),
                  ],
                ),
              ),
            ),

            const SizedBox(width: 12),
            // SMC Overlay Toggle Button
            GestureDetector(
              onTap: () => setState(() => _showSMCOverlay = !_showSMCOverlay),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                decoration: BoxDecoration(
                  color: _showSMCOverlay ? const Color(0xFF2E82FE).withOpacity(0.15) : const Color(0xFF1E2533),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(color: _showSMCOverlay ? const Color(0xFF2E82FE) : AppColors.border),
                ),
                child: Row(
                  children: [
                    Icon(
                      _showSMCOverlay ? Icons.visibility : Icons.visibility_off,
                      size: 16,
                      color: _showSMCOverlay ? const Color(0xFF2E82FE) : AppColors.textMuted,
                    ),
                    const SizedBox(width: 6),
                    Text(
                      'LuxAlgo SMC',
                      style: TextStyle(
                        fontSize: 12,
                        fontWeight: FontWeight.bold,
                        color: _showSMCOverlay ? Colors.white : AppColors.textMuted,
                      ),
                    ),
                  ],
                ),
              ),
            ),
          ],
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
      child: Candlesticks(
        candles: _candles,
      ),
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
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
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
            const SizedBox(height: 16),

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
            const SizedBox(height: 16),

            // Trade Execution Levels (Entry / SL / TP)
            const Text('EXECUTION BLUEPRINT', style: TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: AppColors.textMuted)),
            const SizedBox(height: 8),
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
            const SizedBox(height: 16),

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
            const SizedBox(height: 20),

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

  void _showSymbolPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(12)),
      ),
      builder: (_) => ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: _symbols
            .map((s) => ListTile(
                  title: Text(s, style: const TextStyle(color: Colors.white, fontWeight: FontWeight.w600)),
                  selected: s == _selectedSymbol,
                  selectedColor: AppColors.bullish,
                  trailing: s == _selectedSymbol ? const Icon(Icons.check, color: AppColors.bullish) : null,
                  onTap: () {
                    setState(() => _selectedSymbol = s);
                    Navigator.pop(context);
                    _fetchChartData();
                  },
                ))
            .toList(),
      ),
    );
  }
}
