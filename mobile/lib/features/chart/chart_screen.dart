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

  final _timeframes = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];
  final _cryptoSymbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'XRP/USDT'];
  final _forexSymbols = ['XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY'];
  final _stockSymbols = ['AAPL', 'TSLA', 'NVDA', 'MSFT'];

  bool _showSMCOverlay = true;
  bool _isLoading = true;
  String? _errorMessage;

  List<Candle> _candles = [];
  Map<String, dynamic>? _smcOverlayData;

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
      final baseUrl = 'http://127.0.0.1:8000';

      // 1. Fetch OHLCV candles
      final ohlcvResp = await dio.get(
        '$baseUrl/api/v1/chart/ohlcv',
        queryParameters: {
          'symbol': _selectedSymbol,
          'timeframe': _selectedTimeframe,
          'market_type': _selectedMarket,
          'exchange': _selectedExchange,
          'limit': 150,
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

      // Sort latest first for candlesticks package
      parsedCandles.sort((a, b) => b.date.compareTo(a.date));

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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        backgroundColor: AppColors.surface,
        title: _buildSymbolSelector(),
        actions: [
          IconButton(
            icon: Icon(
              _showSMCOverlay ? Icons.layers : Icons.layers_outlined,
              color: _showSMCOverlay ? AppColors.bullish : Colors.grey,
            ),
            tooltip: 'Toggle SMC Overlay',
            onPressed: () => setState(() => _showSMCOverlay = !_showSMCOverlay),
          ),
          IconButton(
            icon: const Icon(Icons.refresh),
            onPressed: _fetchChartData,
          ),
        ],
      ),
      body: Column(
        children: [
          _buildControls(),
          if (_showSMCOverlay && _smcOverlayData != null) _buildSMCOverlayBanner(),
          Expanded(child: _buildChartArea()),
          _buildSMCSummary(),
        ],
      ),
    );
  }

  Widget _buildSymbolSelector() {
    return GestureDetector(
      onTap: _showSymbolPicker,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(
            _selectedSymbol,
            style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
          ),
          const Icon(Icons.arrow_drop_down, size: 20),
        ],
      ),
    );
  }

  Widget _buildControls() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
      child: Row(
        children: [
          _buildChip(_selectedMarket.toUpperCase(), onTap: _showMarketPicker),
          const SizedBox(width: 6),
          _buildChip('HTF: $_selectedHtfTimeframe', onTap: _showHtfPicker),
          const SizedBox(width: 8),
          Expanded(
            child: SizedBox(
              height: 34,
              child: ListView(
                scrollDirection: Axis.horizontal,
                children: _timeframes.map((tf) {
                  final selected = tf == _selectedTimeframe;
                  return Padding(
                    padding: const EdgeInsets.only(right: 4),
                    child: ChoiceChip(
                      label: Text(
                        tf,
                        style: TextStyle(
                          fontSize: 11,
                          color: selected ? Colors.black : Colors.white,
                        ),
                      ),
                      selected: selected,
                      selectedColor: AppColors.bullish,
                      backgroundColor: const Color(0xFF252540),
                      onSelected: (_) {
                        setState(() => _selectedTimeframe = tf);
                        _fetchChartData();
                      },
                      padding: const EdgeInsets.symmetric(horizontal: 6),
                      materialTapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    ),
                  );
                }).toList(),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildChip(String label, {VoidCallback? onTap}) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
        decoration: BoxDecoration(
          color: const Color(0xFF252540),
          borderRadius: BorderRadius.circular(20),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(label, style: const TextStyle(fontSize: 11, color: Colors.white70)),
            const Icon(Icons.arrow_drop_down, size: 14, color: Colors.white70),
          ],
        ),
      ),
    );
  }

  Widget _buildSMCOverlayBanner() {
    final ob = _smcOverlayData?['order_block'] as Map<String, dynamic>?;
    final fvg = _smcOverlayData?['fvg'] as Map<String, dynamic>?;
    final eq = _smcOverlayData?['equilibrium'] as num?;

    return Container(
      color: const Color(0xFF16192E),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            if (ob != null) ...[
              _tag(
                'OB: ${ob['bottom']?.toStringAsFixed(1)} - ${ob['top']?.toStringAsFixed(1)}',
                AppColors.orderBlock,
              ),
              const SizedBox(width: 8),
            ],
            if (fvg != null) ...[
              _tag(
                'FVG: ${fvg['bottom']?.toStringAsFixed(1)} - ${fvg['top']?.toStringAsFixed(1)}',
                AppColors.fvg,
              ),
              const SizedBox(width: 8),
            ],
            if (eq != null) ...[
              _tag(
                'EQ: ${eq.toStringAsFixed(1)}',
                AppColors.eqLine,
              ),
            ],
          ],
        ),
      ),
    );
  }

  Widget _tag(String text, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withOpacity(0.2),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withOpacity(0.5)),
      ),
      child: Text(text, style: TextStyle(fontSize: 11, color: color, fontWeight: FontWeight.bold)),
    );
  }

  Widget _buildChartArea() {
    if (_isLoading) {
      return Container(
        color: const Color(0xFF131722),
        child: const Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              CircularProgressIndicator(color: AppColors.bullish),
              SizedBox(height: 12),
              Text('Fetching live market data...', style: TextStyle(color: Colors.white54, fontSize: 13)),
            ],
          ),
        ),
      );
    }

    if (_errorMessage != null || _candles.isEmpty) {
      return Container(
        color: const Color(0xFF131722),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.error_outline, size: 48, color: AppColors.bearish),
              const SizedBox(height: 12),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 24),
                child: Text(
                  _errorMessage ?? 'No candle data returned.',
                  style: const TextStyle(color: Colors.white70, fontSize: 13),
                  textAlign: TextAlign.center,
                ),
              ),
              const SizedBox(height: 16),
              ElevatedButton.icon(
                onPressed: _fetchChartData,
                icon: const Icon(Icons.refresh),
                label: const Text('Retry'),
                style: ElevatedButton.styleFrom(backgroundColor: AppColors.surface),
              ),
            ],
          ),
        ),
      );
    }

    return Container(
      color: const Color(0xFF131722),
      child: Candlesticks(
        candles: _candles,
      ),
    );
  }

  Widget _buildSMCSummary() {
    final bias = (_smcOverlayData?['bias'] as String? ?? 'NEUTRAL').toUpperCase();
    final isBull = bias == 'BULLISH';
    final isBear = bias == 'BEARISH';
    final biasColor = isBull ? AppColors.bullish : (isBear ? AppColors.bearish : AppColors.neutral);

    final confluence = _smcOverlayData?['confluence'] ?? 0;
    final inDiscount = _smcOverlayData?['in_discount'] == true;
    final inPremium = _smcOverlayData?['in_premium'] == true;
    final zone = inDiscount ? 'DISCOUNT' : (inPremium ? 'PREMIUM' : 'EQUILIBRIUM');
    final zoneColor = inDiscount ? AppColors.bullish : (inPremium ? AppColors.bearish : AppColors.neutral);

    final sweep = _smcOverlayData?['liquidity_swept'] == true ? 'YES' : 'NO';
    final sweepColor = sweep == 'YES' ? AppColors.bullish : Colors.white54;

    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: [
          _statItem('Bias', bias, biasColor),
          _statItem('Confluence', '$confluence/10', AppColors.neutral),
          _statItem('Zone', zone, zoneColor),
          _statItem('Sweep', sweep, sweepColor),
        ],
      ),
    );
  }

  Widget _statItem(String label, String value, Color color) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: const TextStyle(fontSize: 10, color: Colors.white38)),
        const SizedBox(height: 2),
        Text(value, style: TextStyle(fontSize: 12, color: color, fontWeight: FontWeight.bold)),
      ],
    );
  }

  void _showSymbolPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      builder: (_) => ListView(
        children: _symbols
            .map((s) => ListTile(
                  title: Text(s, style: const TextStyle(color: Colors.white)),
                  selected: s == _selectedSymbol,
                  selectedColor: AppColors.bullish,
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

  void _showMarketPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      builder: (_) => Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          ListTile(
            leading: const Text('🪙', style: TextStyle(fontSize: 20)),
            title: const Text('Crypto (BTC, ETH, SOL...)', style: TextStyle(color: Colors.white)),
            onTap: () {
              setState(() {
                _selectedMarket = 'crypto';
                _selectedSymbol = _cryptoSymbols.first;
              });
              Navigator.pop(context);
              _fetchChartData();
            },
          ),
          ListTile(
            leading: const Text('💱', style: TextStyle(fontSize: 20)),
            title: const Text('Forex & Gold (XAUUSD, EURUSD...)', style: TextStyle(color: Colors.white)),
            onTap: () {
              setState(() {
                _selectedMarket = 'forex';
                _selectedSymbol = _forexSymbols.first;
              });
              Navigator.pop(context);
              _fetchChartData();
            },
          ),
          ListTile(
            leading: const Text('📈', style: TextStyle(fontSize: 20)),
            title: const Text('Stocks (AAPL, TSLA, NVDA...)', style: TextStyle(color: Colors.white)),
            onTap: () {
              setState(() {
                _selectedMarket = 'stock';
                _selectedSymbol = _stockSymbols.first;
              });
              Navigator.pop(context);
              _fetchChartData();
            },
          ),
        ],
      ),
    );
  }

  void _showHtfPicker() {
    showModalBottomSheet(
      context: context,
      backgroundColor: AppColors.surface,
      builder: (_) => ListView(
        children: ['1h', '4h', '1d', '1w']
            .map((tf) => ListTile(
                  title: Text('HTF: $tf', style: const TextStyle(color: Colors.white)),
                  selected: tf == _selectedHtfTimeframe,
                  selectedColor: AppColors.bullish,
                  onTap: () {
                    setState(() => _selectedHtfTimeframe = tf);
                    Navigator.pop(context);
                    _fetchChartData();
                  },
                ))
            .toList(),
      ),
    );
  }
}
