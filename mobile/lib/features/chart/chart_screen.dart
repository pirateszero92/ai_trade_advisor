import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
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
  final _forexSymbols = ['EURUSD', 'GBPUSD', 'USDJPY', 'XAUUSD'];
  final _stockSymbols = ['AAPL', 'TSLA', 'NVDA', 'MSFT'];
  bool _showSMCOverlay = true;

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
            onPressed: () => setState(() {}),
          ),
        ],
      ),
      body: Column(
        children: [
          _buildControls(),
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
          // Market type
          _buildChip(_selectedMarket.toUpperCase(), onTap: _showMarketPicker),
          const SizedBox(width: 6),
          _buildChip('HTF: $_selectedHtfTimeframe', onTap: _showHtfPicker),
          const SizedBox(width: 8),
          // Timeframe selector
          Expanded(
            child: SizedBox(
              height: 38,
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
                      onSelected: (_) => setState(() => _selectedTimeframe = tf),
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

  Widget _buildChartArea() {
    // Placeholder for lightweight_charts_flutter widget
    // Will be replaced with actual chart widget once lightweight_charts_flutter is configured
    return Container(
      color: const Color(0xFF131722),
      child: Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.candlestick_chart, size: 64, color: Colors.white24),
            const SizedBox(height: 16),
            Text(
              '$_selectedSymbol • $_selectedTimeframe • ${_selectedExchange.toUpperCase()}',
              style: const TextStyle(color: Colors.white54, fontSize: 16),
            ),
            const SizedBox(height: 8),
            const Text(
              'Chart loading...\n(Connect to backend)',
              style: TextStyle(color: Colors.white30, fontSize: 12),
              textAlign: TextAlign.center,
            ),
            if (_showSMCOverlay) ...[
              const SizedBox(height: 24),
              _buildSMCLegend(),
            ],
          ],
        ),
      ),
    );
  }

  Widget _buildSMCLegend() {
    return Wrap(
      spacing: 12,
      runSpacing: 4,
      children: [
        _legendItem('Order Block', AppColors.orderBlock),
        _legendItem('FVG', AppColors.fvg),
        _legendItem('BOS/CHoCH', AppColors.bullish),
        _legendItem('EQH/EQL', AppColors.eqLine),
      ],
    );
  }

  Widget _legendItem(String label, Color color) {
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Container(width: 12, height: 12, color: color),
        const SizedBox(width: 4),
        Text(label, style: const TextStyle(fontSize: 10, color: Colors.white54)),
      ],
    );
  }

  Widget _buildSMCSummary() {
    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.all(12),
      child: Row(
        mainAxisAlignment: MainAxisAlignment.spaceAround,
        children: [
          _statItem('HTF Bias', 'BULLISH', AppColors.bullish),
          _statItem('Confluence', '80/100', AppColors.neutral),
          _statItem('Zone', 'DISCOUNT', AppColors.bullish),
          _statItem('Sweep', 'YES', AppColors.bullish),
        ],
      ),
    );
  }

  Widget _statItem(String label, String value, Color color) {
    return Column(
      mainAxisSize: MainAxisSize.min,
      children: [
        Text(label, style: const TextStyle(fontSize: 10, color: Colors.white38)),
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
            leading: const Text('🪙'),
            title: const Text('Crypto (Binance/Bybit)', style: TextStyle(color: Colors.white)),
            onTap: () {
              setState(() {
                _selectedMarket = 'crypto';
                _selectedSymbol = _cryptoSymbols.first;
              });
              Navigator.pop(context);
            },
          ),
          ListTile(
            leading: const Text('💱'),
            title: const Text('Forex + Gold (MT5)', style: TextStyle(color: Colors.white)),
            onTap: () {
              setState(() {
                _selectedMarket = 'forex';
                _selectedSymbol = _forexSymbols.first;
              });
              Navigator.pop(context);
            },
          ),
          ListTile(
            leading: const Text('📈'),
            title: const Text('Stocks', style: TextStyle(color: Colors.white)),
            onTap: () {
              setState(() {
                _selectedMarket = 'stock';
                _selectedSymbol = _stockSymbols.first;
              });
              Navigator.pop(context);
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
                  },
                ))
            .toList(),
      ),
    );
  }
}
