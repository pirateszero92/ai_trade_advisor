import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:candlesticks/candlesticks.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';
import '../../core/api/api_client.dart';
import '../../core/api/ws_client.dart';
import '../../core/trading/strategy_gate_view.dart';
import '../settings/settings_screen.dart';
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
  String get _selectedHtfTimeframe {
    final watchItem = _watchlistMap[_selectedSymbol];
    final watchTf = watchItem?['timeframe']?.toString().toLowerCase();
    final configuredHtf = watchItem?['htf_timeframe']?.toString().toLowerCase();
    if (watchTf == _selectedTimeframe.toLowerCase() &&
        configuredHtf != null &&
        configuredHtf.isNotEmpty) {
      return configuredHtf;
    }
    return const {
      '1m',
      '2m',
      '3m',
      '5m',
      '15m',
      '30m',
      '1h',
    }.contains(_selectedTimeframe)
        ? '4h'
        : '1w';
  }

  final _timeframes = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w'];
  List<String> _cryptoSymbols = [];
  List<String> _forexSymbols = [];
  List<String> _stockSymbols = [];

  bool _showSMCOverlay = true;
  bool _isLoading = true;
  String? _errorMessage;

  List<Candle> _candles = [];
  Map<String, dynamic>? _smcOverlayData;
  List<Map<String, dynamic>> _openPositions = [];
  Timer? _liveTickerTimer;

  // Bottom Dock Tab & Chat State
  int _bottomDockTab = 0; // 0: Apex AI Chat, 1: AI Blueprint, 2: Open Positions
  final _chatInputCtrl = TextEditingController();
  final _chatScrollCtrl = ScrollController();
  final _qtyCtrl = TextEditingController(text: '0.10');
  final _symbolSearchCtrl = TextEditingController();
  bool _isChatLoading = false;

  // Dynamic Risk Sizing & Execution Automation State
  double _selectedRiskPct = 1.0;
  bool _autoBeEnabled = true;
  bool _trailingStopEnabled = true;
  double _accountCapital = 0.0;

  // AI Blueprint Execution Suite Controllers & State
  final _bpEntryCtrl = TextEditingController();
  final _bpSlCtrl = TextEditingController();
  final _bpTpCtrl = TextEditingController();
  final _bpTp2Ctrl = TextEditingController();
  String _bpEntryMode =
      'ai'; // 'ai' (AI Plan Limit), 'market' (Market), 'custom' (Custom Price)
  Map<String, dynamic>? _activeAiBlueprint;
  Map<String, dynamic>? _mtfMatrixData;
  bool _isMtfLoading = false;

  void _recalcRiskSize([double? overrideRiskPct]) {
    final riskPct = overrideRiskPct ?? _selectedRiskPct;
    final curEntry = double.tryParse(_bpEntryCtrl.text) ?? _lastPrice;
    final curSl = double.tryParse(_bpSlCtrl.text) ??
        (curEntry > 0 ? curEntry * 0.99 : 0.0);
    final slDist = (curEntry - curSl).abs();

    if (curEntry <= 0 || slDist <= 0 || _accountCapital <= 0) return;

    final riskAmount = _accountCapital * (riskPct / 100.0);
    double calculatedUnits = riskAmount / slDist;

    // Hard Cap 5x leverage
    final maxAllowed = (_accountCapital * 5.0) / curEntry;
    calculatedUnits = calculatedUnits.clamp(0.0001, maxAllowed);

    if (_selectedMarket == 'stock' ||
        _selectedSymbol.toUpperCase().contains('THB')) {
      _qtyCtrl.text = calculatedUnits.round().clamp(1, 100000).toString();
    } else if (_selectedSymbol.toUpperCase().contains('BTC')) {
      _qtyCtrl.text = calculatedUnits.toStringAsFixed(4);
    } else if (_selectedSymbol.toUpperCase().contains('ETH') ||
        _selectedSymbol.toUpperCase().contains('SOL')) {
      _qtyCtrl.text = calculatedUnits.toStringAsFixed(3);
    } else if (_selectedMarket == 'forex') {
      _qtyCtrl.text =
          (calculatedUnits / 1000).clamp(0.01, 50.0).toStringAsFixed(2);
    } else {
      _qtyCtrl.text = calculatedUnits.toStringAsFixed(2);
    }
  }

  // Per-symbol isolated memory for chat conversations and AI blueprints
  final Map<String, List<Map<String, String>>> _symbolChatMessages = {};
  final Map<String, Map<String, dynamic>> _symbolBlueprints = {};

  List<Map<String, String>> _chatMessages = [
    {
      'role': 'assistant',
      'content':
          'สวัสดีครับ ผม Apex AI ที่ปรึกษาการเทรดสถาบันของคุณ กำลังมอนิเตอร์โครงสร้างตลาดสด มีข้อสงสัยหรืออยากให้ช่วยวิเคราะห์จุดไหนของกราฟนี้ไหมครับ?',
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

  String get _currSym =>
      _selectedSymbol.toUpperCase().contains('THB') ? '฿' : '\$';
  String _fmtPrice(double v) {
    if (v == 0) return '0.00';
    if (v > 0 && v < 0.0001) return v.toStringAsFixed(8);
    if (v > 0 && v < 0.01) return v.toStringAsFixed(6);
    if (v > 0 && v < 10) return v.toStringAsFixed(4);
    return v.toStringAsFixed(2);
  }

  StreamSubscription<Map<String, dynamic>>? _wsPriceSub;
  StreamSubscription<WsConnectionState>? _wsStateSub;
  WsConnectionState _wsState = WsConnectionState.disconnected;
  final CancelToken _requestCancelToken = CancelToken();

  @override
  void initState() {
    super.initState();
    _wsState = AppWebSocketClient.instance.currentState;
    _wsStateSub =
        AppWebSocketClient.instance.connectionStateStream.listen((state) {
      if (mounted) setState(() => _wsState = state);
    });
    _wsPriceSub =
        AppWebSocketClient.instance.priceStream.listen(_onWsPriceTick);
    _bootstrapFromWatchlist();
    _fetchOpenPositions();
    _startLiveTicker();
  }

  void _onWsPriceTick(Map<String, dynamic> ticks) {
    if (!mounted) return;
    final normSel = _selectedSymbol
        .replaceAll('/', '')
        .replaceAll('-', '')
        .replaceAll('_', '')
        .toUpperCase();
    if (ticks.containsKey(normSel) || ticks.containsKey(_selectedSymbol)) {
      final t =
          (ticks[normSel] ?? ticks[_selectedSymbol]) as Map<String, dynamic>;
      final newPrice = (t['price'] as num?)?.toDouble() ?? 0.0;
      if (newPrice > 0) {
        setState(() {
          _lastPrice = newPrice;
          _change24h = (t['change_24h'] as num?)?.toDouble() ?? _change24h;
          _high24h = (t['high_24h'] as num?)?.toDouble() ?? _high24h;
          _low24h = (t['low_24h'] as num?)?.toDouble() ?? _low24h;
          _vol24h = (t['volume_24h'] as num?)?.toDouble() ?? _vol24h;
          _symbolLivePrices[_selectedSymbol] = _lastPrice;
        });
      }
    }
  }

  Future<void> _bootstrapFromWatchlist() async {
    await _fetchWatchlist();
    if (!mounted) return;
    await _fetchChartData();
  }

  @override
  void dispose() {
    _requestCancelToken.cancel('Chart screen disposed');
    _wsPriceSub?.cancel();
    _wsStateSub?.cancel();
    _liveTickerTimer?.cancel();
    _chatInputCtrl.dispose();
    _chatScrollCtrl.dispose();
    _qtyCtrl.dispose();
    _symbolSearchCtrl.dispose();
    _bpEntryCtrl.dispose();
    _bpSlCtrl.dispose();
    _bpTpCtrl.dispose();
    _bpTp2Ctrl.dispose();
    super.dispose();
  }

  final Map<String, double> _symbolLivePrices = {};
  Map<String, Map<String, dynamic>> _watchlistMap = {};

  void _resolveExchangeForCurrentSymbol() {
    final item = _watchlistMap[_selectedSymbol];
    if (item != null &&
        item['exchange'] != null &&
        item['exchange'].toString().isNotEmpty) {
      _selectedExchange = item['exchange'].toString().toLowerCase();
    } else {
      if (_selectedSymbol.toUpperCase().contains('THB')) {
        _selectedExchange = 'innovestx';
      } else if (_selectedMarket == 'forex') {
        _selectedExchange = 'mt5';
      } else if (_selectedMarket == 'stock') {
        _selectedExchange = 'alpaca';
      } else {
        _selectedExchange = 'binance';
      }
    }
  }

  Future<void> _fetchWatchlist() async {
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(
        AppApi.url('/api/v1/settings/watchlist'),
        cancelToken: _requestCancelToken,
      );
      if (resp.statusCode == 200 && resp.data != null) {
        final List<dynamic> list = resp.data['watchlist'] ?? [];
        if (!mounted) return;
        final newCrypto = <String>[];
        final newForex = <String>[];
        final newStock = <String>[];
        final newMap = <String, Map<String, dynamic>>{};

        for (var item in list) {
          final raw = item as Map;
          final sym = raw['symbol']?.toString().trim().toUpperCase() ?? '';
          final mType =
              raw['market_type']?.toString().toLowerCase() ?? 'crypto';
          final ex = raw['exchange']?.toString().toLowerCase() ??
              (sym.contains('THB') ? 'innovestx' : 'binance');
          if (sym.isEmpty) continue;

          newMap[sym] = {
            'symbol': sym,
            'market_type': mType,
            'exchange': ex,
            'timeframe': raw['timeframe'] ?? '1h',
            'htf_timeframe': raw['htf_timeframe'] ?? '4h',
          };

          if (mType == 'stock') {
            if (!newStock.contains(sym)) newStock.add(sym);
          } else if (mType == 'forex') {
            if (!newForex.contains(sym)) newForex.add(sym);
          } else {
            if (!newCrypto.contains(sym)) newCrypto.add(sym);
          }
        }

        setState(() {
          _watchlistMap = newMap;
          _cryptoSymbols = newCrypto;
          _forexSymbols = newForex;
          _stockSymbols = newStock;
          if (!_symbols.contains(_selectedSymbol)) {
            if (_cryptoSymbols.isNotEmpty) {
              _selectedMarket = 'crypto';
              _selectedSymbol = _cryptoSymbols.first;
            } else if (_forexSymbols.isNotEmpty) {
              _selectedMarket = 'forex';
              _selectedSymbol = _forexSymbols.first;
            } else if (_stockSymbols.isNotEmpty) {
              _selectedMarket = 'stock';
              _selectedSymbol = _stockSymbols.first;
            }
          }
          _resolveExchangeForCurrentSymbol();
          final configuredTf = _watchlistMap[_selectedSymbol]?['timeframe']
              ?.toString()
              .toLowerCase();
          if (configuredTf != null && _timeframes.contains(configuredTf)) {
            _selectedTimeframe = configuredTf;
          }
        });
      }
    } catch (_) {}
  }

  Future<void> _fetchLiveTicker() async {
    try {
      _resolveExchangeForCurrentSymbol();
      final dio = AppApi.dio;
      final resp = await dio.get(
        AppApi.url('/api/v1/chart/ticker'),
        cancelToken: _requestCancelToken,
        queryParameters: {
          'symbol': _selectedSymbol,
          'market_type': _selectedMarket,
        },
      );
      if (resp.statusCode == 200 && resp.data != null) {
        final d = resp.data;
        if (!mounted) return;
        final newPrice = (d['price'] as num?)?.toDouble() ?? 0.0;
        if (newPrice <= 0.0) {
          return; // Strict guard against 0.0 or corrupted price
        }

        setState(() {
          _lastPrice = newPrice;
          _change24h = (d['change_24h'] as num?)?.toDouble() ?? _change24h;
          _high24h = (d['high_24h'] as num?)?.toDouble() ?? _high24h;
          _low24h = (d['low_24h'] as num?)?.toDouble() ?? _low24h;
          _vol24h = (d['volume_24h'] as num?)?.toDouble() ?? _vol24h;
          _symbolLivePrices[_selectedSymbol] = _lastPrice;

          if (_candles.isNotEmpty) {
            final lastCandle = _candles.first;
            if (lastCandle.open > 0.0) {
              _candles[0] = Candle(
                date: lastCandle.date,
                open: lastCandle.open,
                high: math.max(lastCandle.high, _lastPrice),
                low: math.min(lastCandle.low, _lastPrice),
                close: _lastPrice,
                volume: lastCandle.volume,
              );
            }
          }
        });
      }
    } catch (_) {}
  }

  static String _normalizeSym(String s) => s
      .replaceAll('/', '')
      .replaceAll('-', '')
      .replaceAll('_', '')
      .toUpperCase();

  bool _isMultiPriceFetching = false;

  Future<void> _fetchMultiAssetLivePrices() async {
    if (_isMultiPriceFetching) return;
    _isMultiPriceFetching = true;
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(
        AppApi.url('/api/v1/signals/live-prices'),
        cancelToken: _requestCancelToken,
      );
      final prices = resp.data['prices'] as Map<String, dynamic>? ?? {};
      if (prices.isNotEmpty && mounted) {
        bool hasChanges = false;
        for (var entry in prices.entries) {
          final sym = entry.key;
          final pData = entry.value as Map<String, dynamic>;
          final p = (pData['price'] as num?)?.toDouble();
          if (p != null && p > 0) {
            if (_symbolLivePrices[sym] != p) {
              _symbolLivePrices[sym] = p;
              hasChanges = true;
            }
            final norm = _normalizeSym(sym);
            if (_symbolLivePrices[norm] != p) {
              _symbolLivePrices[norm] = p;
              hasChanges = true;
            }
            if (sym == _selectedSymbol ||
                norm == _normalizeSym(_selectedSymbol)) {
              if (_lastPrice != p) {
                _lastPrice = p;
                hasChanges = true;
              }
            }
          }
        }
        if (hasChanges && mounted) {
          setState(() {});
        }
      }
    } catch (_) {
    } finally {
      _isMultiPriceFetching = false;
    }
  }

  void _startLiveTicker() {
    _liveTickerTimer?.cancel();
    _liveTickerTimer = Timer.periodic(const Duration(seconds: 5), (t) {
      if (!mounted) return;
      if (_wsState != WsConnectionState.connected || t.tick % 6 == 0) {
        _fetchLiveTicker();
        _fetchMultiAssetLivePrices();
      }
      if (t.tick % 6 == 0) {
        _fetchOpenPositions();
      }
      if (t.tick % 12 == 0) {
        _fetchWatchlist();
      }
    });
  }

  double? _getMarkPriceForSymbol(String symbol) {
    if (symbol == _selectedSymbol && _lastPrice > 0) {
      _symbolLivePrices[symbol] = _lastPrice;
      _symbolLivePrices[_normalizeSym(symbol)] = _lastPrice;
      return _lastPrice;
    }
    if (_symbolLivePrices.containsKey(symbol) &&
        _symbolLivePrices[symbol]! > 0) {
      return _symbolLivePrices[symbol]!;
    }
    final norm = _normalizeSym(symbol);
    if (_symbolLivePrices.containsKey(norm) && _symbolLivePrices[norm]! > 0) {
      return _symbolLivePrices[norm]!;
    }
    return null;
  }

  void _switchToSymbol(String symbol,
      {String? targetMarket, String? targetExchange}) {
    if (symbol == _selectedSymbol &&
        targetMarket == null &&
        targetExchange == null) {
      return;
    }

    // 1. Preserve chat messages and blueprint for the previous symbol
    if (_chatMessages.isNotEmpty) {
      _symbolChatMessages[_selectedSymbol] =
          List<Map<String, String>>.from(_chatMessages);
    }
    if (_activeAiBlueprint != null &&
        _activeAiBlueprint!['symbol'] == _selectedSymbol) {
      _symbolBlueprints[_selectedSymbol] =
          Map<String, dynamic>.from(_activeAiBlueprint!);
    }

    String m = targetMarket ?? 'crypto';
    if (targetMarket == null) {
      if (_forexSymbols.contains(symbol)) {
        m = 'forex';
      } else if (_stockSymbols.contains(symbol)) {
        m = 'stock';
      } else {
        m = 'crypto';
      }
    }

    setState(() {
      _selectedMarket = m;
      _selectedSymbol = symbol;
      if (targetExchange != null) {
        _selectedExchange = targetExchange;
      } else {
        _resolveExchangeForCurrentSymbol();
      }
      final configuredTf =
          _watchlistMap[symbol]?['timeframe']?.toString().toLowerCase();
      if (configuredTf != null && _timeframes.contains(configuredTf)) {
        _selectedTimeframe = configuredTf;
      }

      // 2. Restore or initialize chat messages for newly selected symbol
      if (_symbolChatMessages.containsKey(symbol)) {
        _chatMessages =
            List<Map<String, String>>.from(_symbolChatMessages[symbol]!);
      } else {
        _chatMessages = [
          {
            'role': 'assistant',
            'content':
                'สวัสดีครับ ผม Apex AI ที่ปรึกษาการเทรดสถาบันของคุณ กำลังมอนิเตอร์โครงสร้างตลาดสดของ $symbol มีข้อสงสัยหรืออยากให้ช่วยวิเคราะห์จุดไหนของกราฟนี้ไหมครับ?',
          },
        ];
      }

      // 3. Restore or clear active blueprint for new symbol
      if (_symbolBlueprints.containsKey(symbol)) {
        _activeAiBlueprint =
            Map<String, dynamic>.from(_symbolBlueprints[symbol]!);
      } else {
        _activeAiBlueprint = null;
      }

      // 4. Clear price controllers to avoid displaying stale price levels from previous symbol
      _bpEntryCtrl.clear();
      _bpSlCtrl.clear();
      _bpTpCtrl.clear();
      _bpTp2Ctrl.clear();
    });

    _fetchChartData();
    _fetchLiveTicker();
  }

  Future<void> _fetchChartData() async {
    setState(() {
      _isLoading = true;
      _errorMessage = null;
    });

    try {
      final dio = AppApi.dio;

      // Candles and Strategy Gate must come from the exact same immutable
      // closed-candle snapshot. A second OHLCV request could cross a candle
      // boundary and make the chart disagree with its own overlay.
      final overlayResp = await dio.get(
        AppApi.url('/api/v1/chart/overlay'),
        cancelToken: _requestCancelToken,
        queryParameters: {
          'symbol': _selectedSymbol,
          'timeframe': _selectedTimeframe,
          'htf_timeframe': _selectedHtfTimeframe,
          'market_type': _selectedMarket,
          'exchange': _selectedExchange,
        },
      );

      final requestedSymbol = _selectedSymbol;
      final List<dynamic> rawCandles = overlayResp.data['candles'] ?? [];
      final List<Candle> parsedCandles = [];

      for (final c in rawCandles) {
        try {
          if (c is! Map) continue;
          final tStr = c['t']?.toString();
          final oNum = (c['o'] as num?)?.toDouble();
          final hNum = (c['h'] as num?)?.toDouble();
          final lNum = (c['l'] as num?)?.toDouble();
          final cNum = (c['c'] as num?)?.toDouble();
          final vNum = (c['v'] as num?)?.toDouble() ?? 0.0;
          if (tStr == null ||
              oNum == null ||
              hNum == null ||
              lNum == null ||
              cNum == null) {
            continue;
          }
          parsedCandles.add(
            Candle(
              date: DateTime.parse(tStr),
              open: oNum,
              high: hNum,
              low: lNum,
              close: cNum,
              volume: vNum,
            ),
          );
        } catch (_) {
          // Skip malformed candle safely
        }
      }

      parsedCandles.sort((a, b) => b.date.compareTo(a.date));

      if (parsedCandles.isNotEmpty) {
        _lastPrice = parsedCandles.first.close;
        _symbolLivePrices[_selectedSymbol] = _lastPrice;
      }
      _fetchLiveTicker();

      if (!mounted || _selectedSymbol != requestedSymbol) return;
      final embeddedMtf = overlayResp.data['mtf'] is Map
          ? Map<String, dynamic>.from(overlayResp.data['mtf'] as Map)
          : null;
      setState(() {
        _candles = parsedCandles;
        _smcOverlayData = (overlayResp.data is Map<String, dynamic>)
            ? overlayResp.data as Map<String, dynamic>
            : null;
        _mtfMatrixData = embeddedMtf;
        _isLoading = false;

        final gate = StrategyGateView.fromPayload(_smcOverlayData);
        if (!gate.approved) {
          _symbolBlueprints.remove(_selectedSymbol);
          if (_activeAiBlueprint?['symbol'] == _selectedSymbol) {
            _activeAiBlueprint = null;
          }
        }

        // Auto-refresh text controllers and blueprint for current symbol
        final currentBp = gate.approved &&
                (_activeAiBlueprint != null &&
                    _activeAiBlueprint!['symbol'] == _selectedSymbol &&
                    _blueprintMatchesGate(_activeAiBlueprint, gate))
            ? _activeAiBlueprint
            : (gate.approved &&
                    _blueprintMatchesGate(
                        _symbolBlueprints[_selectedSymbol], gate)
                ? _symbolBlueprints[_selectedSymbol]
                : null);

        final dir = (currentBp?['direction'] ??
                (gate.approved ? gate.action : gate.setupDirection))
            .toString()
            .toUpperCase();
        final isBull =
            dir.contains('BUY') || dir.contains('LONG') || dir.contains('BULL');
        final live = _lastPrice > 0 ? _lastPrice : null;
        final aiEntry = (currentBp?['entry'] as num?)?.toDouble() ??
            (_smcOverlayData?['entry'] as num?)?.toDouble();
        final aiSl = (currentBp?['stop_loss'] as num?)?.toDouble() ??
            (_smcOverlayData?['stop_loss'] as num?)?.toDouble();
        final aiTp = (currentBp?['take_profit'] as num?)?.toDouble() ??
            (_smcOverlayData?['take_profit'] as num?)?.toDouble();
        final aiTp2 = (currentBp?['take_profit_2'] as num?)?.toDouble();

        final curTextVal = double.tryParse(_bpEntryCtrl.text);
        final isMismatched = curTextVal == null ||
            (curTextVal > 0 &&
                live != null &&
                (curTextVal / live > 3.0 || curTextVal / live < 0.33));
        final hasValidSetup = aiEntry != null &&
            aiSl != null &&
            aiTp != null &&
            aiEntry > 0 &&
            ((isBull && aiSl < aiEntry && aiTp > aiEntry) ||
                (!isBull && aiSl > aiEntry && aiTp < aiEntry));

        if (hasValidSetup &&
            (_bpEntryCtrl.text.isEmpty ||
                isMismatched ||
                _bpEntryMode == 'ai')) {
          _bpEntryCtrl.text = _fmtPrice(aiEntry);
          _bpSlCtrl.text = _fmtPrice(aiSl);
          _bpTpCtrl.text = _fmtPrice(aiTp);
          _bpTp2Ctrl.text = aiTp2 != null && aiTp2 > 0 ? _fmtPrice(aiTp2) : '';
        }
      });
      if (embeddedMtf == null) {
        _fetchMtfMatrix(_selectedSymbol);
      }
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _errorMessage = 'Failed to load live data: $e';
        _isLoading = false;
      });
    }
  }

  Future<void> _fetchMtfMatrix([String? sym]) async {
    final targetSym = sym ?? _selectedSymbol;
    if (_isMtfLoading) return;
    _isMtfLoading = true;
    try {
      final resp = await AppApi.dio.get(
        AppApi.url('/api/v1/signals/mtf-matrix'),
        cancelToken: _requestCancelToken,
        queryParameters: {
          'symbol': targetSym,
          'market_type': _selectedMarket,
          'exchange': _selectedExchange,
        },
      );
      if (resp.data != null && resp.data['data'] != null) {
        if (!mounted) return;
        setState(() {
          _mtfMatrixData = Map<String, dynamic>.from(resp.data['data'] as Map);
          _isMtfLoading = false;
        });
      }
    } catch (_) {
      if (mounted) setState(() => _isMtfLoading = false);
    }
  }

  Future<void> _fetchOpenPositions() async {
    try {
      final resp = await AppApi.dio.get(
        AppApi.url('/api/v1/paper/orders'),
        cancelToken: _requestCancelToken,
        queryParameters: {'status': 'open'},
      );
      final List<dynamic> list = resp.data['trades'] ?? [];
      double cap = _accountCapital;
      try {
        final portResp = await AppApi.dio.get(
          AppApi.url('/api/v1/paper/account'),
          cancelToken: _requestCancelToken,
        );
        if (portResp.data != null && portResp.data['equity'] != null) {
          cap = (portResp.data['equity'] as num).toDouble();
        }
      } catch (_) {}
      if (!mounted) return;
      setState(() {
        _openPositions =
            list.map((e) => Map<String, dynamic>.from(e as Map)).toList();
        _accountCapital = cap;
      });
    } catch (e) {
      // ignore
    }
  }

  bool _blueprintMatchesGate(
      Map<String, dynamic>? blueprint, StrategyGateView gate) {
    if (blueprint == null || !gate.approved) return false;
    final direction = blueprint['direction']?.toString().toLowerCase();
    return (gate.allowsLong && direction == 'long') ||
        (gate.allowsShort && direction == 'short');
  }

  void _syncBlueprintFromAI(String rawResponse) {
    final gate = StrategyGateView.fromPayload(_smcOverlayData);
    if (!gate.approved) {
      _symbolBlueprints.remove(_selectedSymbol);
      if (_activeAiBlueprint?['symbol'] == _selectedSymbol) {
        _activeAiBlueprint = null;
      }
      return;
    }

    Map<String, dynamic>? parsed;
    try {
      final match = RegExp(r'\{[\s\S]*\}').firstMatch(rawResponse);
      if (match != null) {
        parsed = jsonDecode(match.group(0)!);
      }
    } catch (_) {}

    if (parsed == null) return;

    String direction = 'WAIT';
    int confidence = 0;
    double? entry;
    double? sl;
    double? tp;
    double? tp2;
    String reasoning = '';
    String zoneName = 'DISCOUNT';
    String htfTrend = 'BULLISH';

    final rec = (parsed['recommendation'] ?? parsed['direction'] ?? '')
        .toString()
        .toUpperCase();
    if (rec.contains('BUY') || rec.contains('LONG')) {
      direction = 'LONG';
    } else if (rec.contains('SELL') || rec.contains('SHORT')) {
      direction = 'SHORT';
    }
    if (direction == 'WAIT') {
      _symbolBlueprints.remove(_selectedSymbol);
      if (_activeAiBlueprint?['symbol'] == _selectedSymbol) {
        _activeAiBlueprint = null;
      }
      return;
    }
    if ((gate.allowsLong && direction != 'LONG') ||
        (gate.allowsShort && direction != 'SHORT')) {
      _symbolBlueprints.remove(_selectedSymbol);
      if (_activeAiBlueprint?['symbol'] == _selectedSymbol) {
        _activeAiBlueprint = null;
      }
      return;
    }
    confidence = ((parsed['confidence'] as num?)?.toInt() ?? 0).clamp(0, 100);
    entry = (parsed['entry'] as num?)?.toDouble();
    sl = (parsed['stop_loss'] as num?)?.toDouble() ??
        (parsed['sl'] as num?)?.toDouble();
    tp = (parsed['take_profit'] as num?)?.toDouble() ??
        (parsed['tp'] as num?)?.toDouble();
    tp2 = (parsed['take_profit_2'] as num?)?.toDouble() ??
        (parsed['tp2'] as num?)?.toDouble();
    reasoning = parsed['reasoning']?.toString() ??
        parsed['risk_notes']?.toString() ??
        '';

    final hasValidGeometry = entry != null &&
        sl != null &&
        tp != null &&
        entry.isFinite &&
        sl.isFinite &&
        tp.isFinite &&
        entry > 0 &&
        (direction == 'LONG'
            ? (sl < entry && tp > entry)
            : (sl > entry && tp < entry));
    if (!hasValidGeometry) return;

    final isLong = direction == 'LONG';

    final inDiscount = _smcOverlayData?['in_discount'] == true;
    final inPremium = _smcOverlayData?['in_premium'] == true;
    zoneName = inDiscount
        ? 'DISCOUNT ZONE'
        : (inPremium ? 'PREMIUM ZONE' : 'EQUILIBRIUM');
    htfTrend = isLong ? 'Bullish' : 'Bearish';

    _activeAiBlueprint = {
      'symbol': _selectedSymbol,
      'direction': direction,
      'confidence': confidence,
      'entry': entry,
      'stop_loss': sl,
      'take_profit': tp,
      if (tp2 != null && tp2.isFinite && tp2 > 0) 'take_profit_2': tp2,
      'reasoning': reasoning.isNotEmpty ? reasoning : rawResponse,
      'zone_name': zoneName,
      'htf_trend': htfTrend,
      'timestamp': DateTime.now(),
    };
    _symbolBlueprints[_selectedSymbol] =
        Map<String, dynamic>.from(_activeAiBlueprint!);

    if (_bpEntryMode == 'ai' || _bpEntryCtrl.text.isEmpty) {
      _bpEntryCtrl.text = _fmtPrice(entry);
      _bpSlCtrl.text = _fmtPrice(sl);
      _bpTpCtrl.text = _fmtPrice(tp);
      _bpTp2Ctrl.text =
          tp2 != null && tp2.isFinite && tp2 > 0 ? _fmtPrice(tp2) : '';
    }
  }

  Future<void> _executeOrder(String direction,
      {double? customEntry, double? customSl, double? customTp}) async {
    if (!ref.read(settingsProvider).isPaperMode) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          backgroundColor: AppColors.bearish,
          content: Text(
              'Chart Live execution ยังปิดแบบ fail-safe จนกว่า protective-order OMS จะพร้อม กรุณากลับ Paper เพื่อทดสอบคำสั่ง'),
        ),
      );
      return;
    }
    final live = _lastPrice > 0 ? _lastPrice : null;
    final isMarketOrder = _bpEntryMode == 'market';
    final entry = isMarketOrder ? live : customEntry;
    final sl = customSl;
    final tp = customTp;
    final size = double.tryParse(_qtyCtrl.text.trim());
    final validGeometry = entry != null &&
        sl != null &&
        tp != null &&
        entry.isFinite &&
        sl.isFinite &&
        tp.isFinite &&
        entry > 0 &&
        (direction == 'long'
            ? (sl < entry && tp > entry)
            : (sl > entry && tp < entry));
    if (!validGeometry ||
        size == null ||
        !size.isFinite ||
        size <= 0 ||
        live == null) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          backgroundColor: AppColors.bearish,
          content: Text(
              'ไม่สามารถส่งคำสั่งได้: ต้องมีราคาสดและ Entry / SL / TP / Size ที่ถูกต้อง'),
        ),
      );
      return;
    }

    final isThb = _selectedSymbol.toUpperCase().contains('THB') ||
        _selectedExchange == 'innovestx';
    const targetMode = 'paper';
    final targetExchange = isThb ? 'innovestx' : _selectedExchange;
    final curr = isThb ? '฿' : '\$';
    final isPending = !isMarketOrder;
    final orderTypeTag = isPending ? 'LIM' : 'MKT';
    final cleanSym = _selectedSymbol.replaceAll('/', '');
    final tag =
        '#$cleanSym-${direction.toUpperCase()}-$orderTypeTag-${DateTime.now().millisecondsSinceEpoch % 1000}';

    try {
      final dio = AppApi.dio;
      final resp = await dio.post(
        AppApi.url('/api/v1/paper/orders'),
        cancelToken: _requestCancelToken,
        data: {
          'symbol': _selectedSymbol,
          'direction': direction,
          'entry': entry,
          'stop_loss': sl,
          'take_profit': tp,
          'order_type': isMarketOrder ? 'market' : 'limit',
          'position_size': size,
          'size': size,
          'risk_pct': _selectedRiskPct,
          'auto_be': _autoBeEnabled,
          'trailing_stop': _trailingStopEnabled,
          'exchange': targetExchange,
          'mode': targetMode,
          'tag': tag,
          'idempotency_key': '${DateTime.now().microsecondsSinceEpoch}-$tag',
          'notes':
              'Executed from Apex AI Blueprint Suite [Risk: $_selectedRiskPct% | BE: $_autoBeEnabled | Trail: $_trailingStopEnabled]',
        },
      );

      if (!mounted) return;
      final isSuccessPending = resp.data['status'] == 'pending' || isPending;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: isSuccessPending
              ? const Color(0xFFFFB300)
              : (direction == 'long' ? AppColors.bullish : AppColors.bearish),
          content: Text(
            isSuccessPending
                ? '⏳ ตั้งคำสั่งรอดักราคาสำเร็จ: $tag\nรอราคา Match ที่ $curr${_fmtPrice(entry)} | SL: $curr${_fmtPrice(sl)} | TP: $curr${_fmtPrice(tp)}\n🛡️ Auto-BE: ${_autoBeEnabled ? "ON" : "OFF"} | 🚀 Trailing: ${_trailingStopEnabled ? "ON" : "OFF"}'
                : '⚡ Position Opened: ${direction.toUpperCase()} $size $_selectedSymbol @ $curr${_fmtPrice(entry)}\nSL: $curr${_fmtPrice(sl)} | TP: $curr${_fmtPrice(tp)}\n🛡️ Auto-BE: ${_autoBeEnabled ? "ON" : "OFF"} | 🚀 Trailing: ${_trailingStopEnabled ? "ON" : "OFF"}',
            style: const TextStyle(
                color: Colors.black, fontWeight: FontWeight.bold),
          ),
          duration: const Duration(seconds: 4),
        ),
      );

      await _fetchOpenPositions();
    } catch (e) {
      String errDetail = e.toString();
      if (e is DioException && e.response?.data != null) {
        final data = e.response!.data;
        if (data is Map && data['detail'] != null) {
          errDetail = data['detail'].toString();
        }
      }
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: AppColors.bearish,
          content: Text('❌ ส่งคำสั่งล้มเหลว: $errDetail',
              style: const TextStyle(
                  color: Colors.white, fontWeight: FontWeight.bold)),
          duration: const Duration(seconds: 5),
        ),
      );
    }
  }

  Future<void> _closePosition(String tradeId) async {
    try {
      final pos = _openPositions
          .firstWhere((p) => p['id']?.toString() == tradeId, orElse: () => {});
      final sym = pos['symbol']?.toString() ?? _selectedSymbol;

      final resp = await AppApi.dio.post(
        AppApi.url('/api/v1/paper/orders/$tradeId/close'),
        cancelToken: _requestCancelToken,
        data: {'reason': 'Manual Close'},
      );

      final pnl = (resp.data['pnl'] as num?)?.toDouble() ?? 0.0;
      final closePrice = (resp.data['close_price'] as num?)?.toDouble();
      final isProfit = pnl >= 0;

      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          backgroundColor: isProfit ? AppColors.bullish : AppColors.bearish,
          content: Text(
            '✅ Closed $sym${closePrice != null ? ' @ \$${_fmtPrice(closePrice)}' : ''} | Realized PnL: ${isProfit ? '+' : ''}\$$pnl',
            style: const TextStyle(
                color: Colors.black, fontWeight: FontWeight.bold),
          ),
          duration: const Duration(seconds: 4),
        ),
      );

      await _fetchOpenPositions();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
            backgroundColor: AppColors.bearish,
            content: Text('Error closing position: $e')),
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
                              if (_showSMCOverlay && _smcOverlayData != null)
                                _buildSMCLayerIndicator(),
                              if (_marketRegimeData != null)
                                _buildMarketRegimeBar(),
                              if (_indicatorDecisionData != null)
                                _buildIndicatorDecisionBar(),
                              if (_mtfMatrixData != null) _buildMtfMatrixBar(),
                              Expanded(flex: 62, child: _buildChartArea()),
                              const Divider(),
                              Expanded(flex: 38, child: _buildBottomDock()),
                            ],
                          ),
                        ),
                        const VerticalDivider(
                            width: 1, color: AppColors.border),
                        Expanded(flex: 30, child: _buildApexAIPanel()),
                      ],
                    );
                  } else {
                    return Column(
                      children: [
                        if (_showSMCOverlay && _smcOverlayData != null)
                          _buildSMCLayerIndicator(),
                        if (_marketRegimeData != null) _buildMarketRegimeBar(),
                        if (_indicatorDecisionData != null)
                          _buildIndicatorDecisionBar(),
                        if (_mtfMatrixData != null) _buildMtfMatrixBar(),
                        Expanded(flex: 50, child: _buildChartArea()),
                        const Divider(),
                        Expanded(flex: 50, child: _buildBottomDock()),
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
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
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

            // Symbol Selector Button (Synced with Proactive Watchlist)
            GestureDetector(
              onTap: _showSymbolPickerModal,
              behavior: HitTestBehavior.opaque,
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                decoration: BoxDecoration(
                  color: const Color(0xFF1E2533),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(
                      color: const Color(0xFF2E82FE).withValues(alpha: 0.6)),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (_selectedSymbol.toUpperCase().contains('THB'))
                      Container(
                        margin: const EdgeInsets.only(right: 6),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 5, vertical: 1),
                        decoration: BoxDecoration(
                          color:
                              const Color(0xFF9B59B6).withValues(alpha: 0.25),
                          borderRadius: BorderRadius.circular(3),
                        ),
                        child: const Text(
                          'THB',
                          style: TextStyle(
                              fontSize: 9,
                              fontWeight: FontWeight.bold,
                              color: Color(0xFFC39BD3)),
                        ),
                      ),
                    Text(
                      _selectedSymbol,
                      style: const TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 13,
                          color: Colors.white),
                    ),
                    const SizedBox(width: 4),
                    const Icon(Icons.arrow_drop_down,
                        color: Colors.white70, size: 18),
                  ],
                ),
              ),
            ),

            const SizedBox(width: 12),

            // Live Price Display
            Text(
              _lastPrice > 0
                  ? (_lastPrice < 10
                      ? _lastPrice.toStringAsFixed(4)
                      : _lastPrice.toStringAsFixed(2))
                  : '---',
              style: TextStyle(
                fontSize: 14,
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
                color: changeColor.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(4),
              ),
              child: Text(
                '${isPos ? '+' : ''}${_change24h.toStringAsFixed(2)}%',
                style: TextStyle(
                    color: changeColor,
                    fontSize: 11,
                    fontWeight: FontWeight.bold),
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
                          fontWeight:
                              isSel ? FontWeight.bold : FontWeight.normal,
                          color: isSel ? AppColors.bullish : Colors.white,
                          fontSize: 13,
                        ),
                      ),
                      if (isSel)
                        const Icon(Icons.check,
                            size: 16, color: AppColors.bullish),
                    ],
                  ),
                );
              }).toList(),
              child: Container(
                padding:
                    const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
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
                      style: const TextStyle(
                          fontWeight: FontWeight.bold,
                          fontSize: 12,
                          color: Colors.white),
                    ),
                    const SizedBox(width: 2),
                    const Icon(Icons.arrow_drop_down,
                        color: Colors.white70, size: 16),
                  ],
                ),
              ),
            ),

            const SizedBox(width: 8),

            // LuxAlgo SMC Toggle
            GestureDetector(
              behavior: HitTestBehavior.opaque,
              onTap: () => setState(() => _showSMCOverlay = !_showSMCOverlay),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
                decoration: BoxDecoration(
                  color: _showSMCOverlay
                      ? const Color(0xFF2E82FE).withValues(alpha: 0.15)
                      : const Color(0xFF1E2533),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(
                      color: _showSMCOverlay
                          ? const Color(0xFF2E82FE)
                          : AppColors.border),
                ),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(
                      _showSMCOverlay ? Icons.visibility : Icons.visibility_off,
                      size: 14,
                      color: _showSMCOverlay
                          ? const Color(0xFF2E82FE)
                          : AppColors.textMuted,
                    ),
                    const SizedBox(width: 4),
                    Text(
                      'LuxAlgo SMC',
                      style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        color: _showSMCOverlay
                            ? Colors.white
                            : AppColors.textMuted,
                      ),
                    ),
                  ],
                ),
              ),
            ),

            const SizedBox(width: 14),

            // Live WebSocket Status Badge
            InkWell(
              onTap: () {
                if (_wsState != WsConnectionState.connected) {
                  AppWebSocketClient.instance.connect();
                }
              },
              borderRadius: BorderRadius.circular(6),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
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
                      _wsState == WsConnectionState.connected
                          ? '⚡ WS (15ms)'
                          : '🟡 Fallback',
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
            ),

            const SizedBox(width: 8),

            // AI Voice Briefing Button
            InkWell(
              onTap: () => _showVoiceBriefingModal(context),
              borderRadius: BorderRadius.circular(6),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 7, vertical: 3),
                decoration: BoxDecoration(
                  color: const Color(0xFF9B59B6).withValues(alpha: 0.2),
                  borderRadius: BorderRadius.circular(6),
                  border: Border.all(
                      color: const Color(0xFF9B59B6).withValues(alpha: 0.7),
                      width: 0.8),
                ),
                child: const Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.mic, size: 12, color: Color(0xFFD7BDE2)),
                    SizedBox(width: 3),
                    Text('🎙️ Briefing',
                        style: TextStyle(
                            fontSize: 9.5,
                            fontWeight: FontWeight.bold,
                            color: Color(0xFFD7BDE2))),
                  ],
                ),
              ),
            ),

            const SizedBox(width: 10),
            IconButton(
              icon: const Icon(Icons.refresh, size: 18, color: Colors.white70),
              tooltip: 'รีเฟรชข้อมูลและ Watchlist',
              onPressed: () async {
                await _fetchWatchlist();
                _fetchChartData();
                _fetchLiveTicker();
              },
              padding: EdgeInsets.zero,
              constraints: const BoxConstraints(),
            ),
          ],
        ),
      ),
    );
  }

  void _showVoiceBriefingModal(BuildContext context) {
    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF10141E),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) {
        return FutureBuilder<Response>(
          future: AppApi.dio.get(
            AppApi.url('/api/v1/briefing/morning'),
            cancelToken: _requestCancelToken,
          ),
          builder: (ctx, snapshot) {
            final data = snapshot.data?.data as Map<String, dynamic>? ?? {};
            final script = data['script']?.toString() ??
                'กำลังประมวลผลบทสรุปตลาดเช้าจาก Apex AI...';
            final regime = data['regime']?.toString() ??
                'Accumulation / Re-accumulation Structure';
            final setups = (data['key_focus_setups'] as List<dynamic>?)
                    ?.map((e) => e as Map<String, dynamic>)
                    .toList() ??
                [];

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
                      const Row(
                        children: [
                          Icon(Icons.record_voice_over,
                              size: 20, color: Color(0xFF00E5FF)),
                          SizedBox(width: 8),
                          Text(
                            'Apex AI Daily Voice Briefing',
                            style: TextStyle(
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
                  const SizedBox(height: 8),

                  // Regime Box
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF141926),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(
                          color:
                              const Color(0xFF00E5FF).withValues(alpha: 0.4)),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.hub,
                            size: 16, color: Color(0xFF00E5FF)),
                        const SizedBox(width: 8),
                        Expanded(
                          child: Text(
                            regime,
                            style: const TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.bold,
                                color: Color(0xFF00E5FF)),
                          ),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 10),

                  // Audio Player Bar Simulation
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    decoration: BoxDecoration(
                      color: const Color(0xFF1B2333),
                      borderRadius: BorderRadius.circular(8),
                      border: Border.all(color: const Color(0xFF2E3D59)),
                    ),
                    child: Row(
                      children: [
                        const Icon(Icons.play_circle_filled,
                            size: 28, color: AppColors.bullish),
                        const SizedBox(width: 10),
                        const Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('🔊 AI Audio Speech (Thai Voice)',
                                  style: TextStyle(
                                      fontSize: 11,
                                      fontWeight: FontWeight.bold,
                                      color: Colors.white)),
                              SizedBox(height: 2),
                              Text(
                                  'Apex Proactive Market Intelligence • 1:45 min',
                                  style: TextStyle(
                                      fontSize: 9.5, color: Colors.white54)),
                            ],
                          ),
                        ),
                        Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 6, vertical: 2),
                          decoration: BoxDecoration(
                            color: AppColors.bullish.withValues(alpha: 0.2),
                            borderRadius: BorderRadius.circular(4),
                          ),
                          child: const Text('AUTO TTS',
                              style: TextStyle(
                                  fontSize: 9,
                                  fontWeight: FontWeight.bold,
                                  color: AppColors.bullish)),
                        ),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Script Body
                  const Text('สรุปเนื้อหาบรรยาย:',
                      style: TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.bold,
                          color: Colors.white70)),
                  const SizedBox(height: 6),
                  Container(
                    constraints: const BoxConstraints(maxHeight: 180),
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF141926),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: SingleChildScrollView(
                      child: Text(
                        script,
                        style: const TextStyle(
                            fontSize: 11.5,
                            color: Colors.white70,
                            height: 1.45),
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),

                  // Key Setups
                  if (setups.isNotEmpty) ...[
                    const Text('คู่ที่น่าจับตาประจำวัน (Top SMC Focus):',
                        style: TextStyle(
                            fontSize: 11,
                            fontWeight: FontWeight.bold,
                            color: Colors.white70)),
                    const SizedBox(height: 6),
                    Row(
                      children: setups.map((s) {
                        return Expanded(
                          child: Container(
                            margin: const EdgeInsets.only(right: 6),
                            padding: const EdgeInsets.all(8),
                            decoration: BoxDecoration(
                              color: const Color(0xFF161C2A),
                              borderRadius: BorderRadius.circular(6),
                              border:
                                  Border.all(color: const Color(0xFF2E384D)),
                            ),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(s['symbol']?.toString() ?? '',
                                    style: const TextStyle(
                                        fontSize: 11,
                                        fontWeight: FontWeight.bold,
                                        color: Colors.white)),
                                const SizedBox(height: 2),
                                Text(s['grade']?.toString() ?? '',
                                    style: const TextStyle(
                                        fontSize: 9.5,
                                        fontWeight: FontWeight.bold,
                                        color: Color(0xFFFFD700))),
                                const SizedBox(height: 2),
                                Text(s['strategy']?.toString() ?? '',
                                    style: const TextStyle(
                                        fontSize: 8.5, color: Colors.white54),
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis),
                              ],
                            ),
                          ),
                        );
                      }).toList(),
                    ),
                  ],
                ],
              ),
            );
          },
        );
      },
    );
  }

  Widget _marketTypeBadge(String title, String market) {
    final isSelected = _selectedMarket == market;
    return GestureDetector(
      behavior: HitTestBehavior.opaque,
      onTap: () {
        final targetList = market == 'forex'
            ? _forexSymbols
            : (market == 'stock' ? _stockSymbols : _cryptoSymbols);
        final targetSym = targetList.isNotEmpty ? targetList.first : 'BTC/USDT';
        _switchToSymbol(targetSym, targetMarket: market);
      },
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        decoration: BoxDecoration(
          color: isSelected
              ? const Color(0xFF2E82FE).withValues(alpha: 0.2)
              : const Color(0xFF1E2533),
          borderRadius: BorderRadius.circular(4),
          border: Border.all(
            color:
                isSelected ? const Color(0xFF2E82FE) : const Color(0xFF232A38),
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
        Text(label,
            style: const TextStyle(fontSize: 10, color: AppColors.textMuted)),
        const SizedBox(height: 2),
        Text(value,
            style: const TextStyle(
                fontSize: 12,
                fontWeight: FontWeight.w600,
                color: Colors.white)),
      ],
    );
  }

  String _formatVolume(double vol) {
    if (vol >= 1000000000) return '${(vol / 1000000000).toStringAsFixed(2)}B';
    if (vol >= 1000000) return '${(vol / 1000000).toStringAsFixed(2)}M';
    if (vol >= 1000) return '${(vol / 1000).toStringAsFixed(1)}K';
    return vol.toStringAsFixed(0);
  }

  Future<void> _showSymbolPickerModal() async {
    await _fetchWatchlist();
    if (!mounted) return;

    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF131722),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => StatefulBuilder(
        builder: (context, setModalState) {
          final query = _symbolSearchCtrl.text.trim().toUpperCase();
          final list = _symbols
              .where((s) => query.isEmpty || s.contains(query))
              .toList();

          return Container(
            height: MediaQuery.of(context).size.height * 0.75,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Center(
                  child: Container(
                    width: 40,
                    height: 4,
                    decoration: BoxDecoration(
                        color: Colors.white24,
                        borderRadius: BorderRadius.circular(2)),
                  ),
                ),
                const SizedBox(height: 12),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    Text(
                      'เลือกสินทรัพย์ (${_selectedMarket.toUpperCase()})',
                      style: const TextStyle(
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                          color: Colors.white),
                    ),
                    Container(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 8, vertical: 2),
                      decoration: BoxDecoration(
                        color: AppColors.bullish.withValues(alpha: 0.2),
                        borderRadius: BorderRadius.circular(6),
                      ),
                      child: Text(
                        '${_symbols.length} รายการใน Watchlist',
                        style: const TextStyle(
                            fontSize: 11,
                            color: AppColors.bullish,
                            fontWeight: FontWeight.bold),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 12),
                // Search bar
                TextField(
                  controller: _symbolSearchCtrl,
                  onChanged: (_) => setModalState(() {}),
                  style: const TextStyle(color: Colors.white, fontSize: 14),
                  decoration: InputDecoration(
                    prefixIcon: const Icon(Icons.search, color: Colors.white54),
                    hintText: 'ค้นหาเหรียญใน Watchlist (เช่น THB, BTC, XRP)...',
                    hintStyle:
                        const TextStyle(color: Colors.white30, fontSize: 13),
                    filled: true,
                    fillColor: const Color(0xFF1E2533),
                    contentPadding: const EdgeInsets.symmetric(vertical: 0),
                    border: OutlineInputBorder(
                      borderRadius: BorderRadius.circular(10),
                      borderSide: const BorderSide(
                          color: Color(0xFF2E82FE), width: 0.8),
                    ),
                  ),
                ),
                const SizedBox(height: 12),
                Expanded(
                  child: list.isEmpty
                      ? const Center(
                          child: Text(
                            'ไม่พบสินทรัพย์ที่ค้นหา',
                            style:
                                TextStyle(color: Colors.white38, fontSize: 13),
                          ),
                        )
                      : ListView.builder(
                          itemCount: list.length,
                          itemBuilder: (context, i) {
                            final s = list[i];
                            final isSel = s == _selectedSymbol;
                            final isTHB = s.toUpperCase().contains('THB');
                            final isUSDT = s.toUpperCase().contains('USDT');

                            Color tagBg =
                                const Color(0xFF2E82FE).withValues(alpha: 0.2);
                            Color tagFg = const Color(0xFF2E82FE);
                            String tagLabel = _selectedMarket.toUpperCase();

                            if (_selectedMarket == 'crypto') {
                              if (isTHB) {
                                tagBg = const Color(0xFF9B59B6)
                                    .withValues(alpha: 0.25);
                                tagFg = const Color(0xFFC39BD3);
                                tagLabel = '🟣 INNOVESTX (THB)';
                              } else if (isUSDT) {
                                tagBg = const Color(0xFF2E82FE)
                                    .withValues(alpha: 0.25);
                                tagFg = const Color(0xFF5DADE2);
                                tagLabel = '🌐 BINANCE (USDT)';
                              }
                            } else if (_selectedMarket == 'forex') {
                              tagBg = const Color(0xFFF39C12)
                                  .withValues(alpha: 0.25);
                              tagFg = const Color(0xFFF8C471);
                              tagLabel = '💱 FOREX/GOLD (MT5)';
                            } else if (_selectedMarket == 'stock') {
                              tagBg = const Color(0xFF00C087)
                                  .withValues(alpha: 0.25);
                              tagFg = const Color(0xFF00C087);
                              tagLabel = '📈 STOCK (ALPACA)';
                            }

                            return Container(
                              margin: const EdgeInsets.only(bottom: 6),
                              decoration: BoxDecoration(
                                color: isSel
                                    ? const Color(0xFF2E82FE)
                                        .withValues(alpha: 0.15)
                                    : const Color(0xFF1B2333),
                                borderRadius: BorderRadius.circular(8),
                                border: Border.all(
                                  color: isSel
                                      ? const Color(0xFF2E82FE)
                                      : const Color(0xFF232A38),
                                  width: isSel ? 1.2 : 0.8,
                                ),
                              ),
                              child: ListTile(
                                onTap: () {
                                  Navigator.pop(ctx);
                                  _switchToSymbol(s);
                                },
                                title: Row(
                                  children: [
                                    Text(
                                      s,
                                      style: TextStyle(
                                        fontWeight: FontWeight.bold,
                                        fontSize: 14,
                                        color: isSel
                                            ? AppColors.bullish
                                            : Colors.white,
                                      ),
                                    ),
                                    const SizedBox(width: 8),
                                    Container(
                                      padding: const EdgeInsets.symmetric(
                                          horizontal: 8, vertical: 3),
                                      decoration: BoxDecoration(
                                        color: tagBg,
                                        borderRadius: BorderRadius.circular(6),
                                      ),
                                      child: Text(
                                        tagLabel,
                                        style: TextStyle(
                                          fontSize: 10,
                                          fontWeight: FontWeight.bold,
                                          color: tagFg,
                                        ),
                                      ),
                                    ),
                                    const Spacer(),
                                  ],
                                ),
                                trailing: isSel
                                    ? const Icon(Icons.check_circle,
                                        color: AppColors.bullish, size: 20)
                                    : null,
                              ),
                            );
                          },
                        ),
                ),
                const SizedBox(height: 8),
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton.icon(
                    onPressed: () {
                      Navigator.pop(ctx);
                      _showAddAssetCatalogDialog();
                    },
                    icon: const Icon(Icons.add_circle, size: 18),
                    label: const Text('➕ เพิ่มสินทรัพย์ใหม่เข้า Watchlist',
                        style: TextStyle(fontWeight: FontWeight.bold)),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: const Color(0xFF2E82FE),
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                    ),
                  ),
                ),
              ],
            ),
          );
        },
      ),
    );
  }

  void _showAddAssetCatalogDialog() {
    final customSymCtrl = TextEditingController();
    final searchCtrl = TextEditingController();
    String activeCategory = _selectedMarket == 'forex'
        ? 'forex_metals'
        : (_selectedMarket == 'stock' ? 'stocks' : 'innovestx_thb');
    String selectedTf = _selectedTimeframe;
    String customMarketType = _selectedMarket;

    final selectedItems = <Map<String, dynamic>>{};
    bool isLoadingCatalog = true;
    bool hasInitiatedFetch = false;
    Map<String, List<Map<String, dynamic>>> catalog = {};

    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF131722),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => StatefulBuilder(
        builder: (context, setModalState) {
          if (isLoadingCatalog && !hasInitiatedFetch) {
            hasInitiatedFetch = true;
            AppApi.dio
                .get(
              AppApi.url('/api/v1/settings/assets/catalog'),
              cancelToken: _requestCancelToken,
            )
                .then((resp) {
              final data = resp.data as Map<String, dynamic>;
              setModalState(() {
                catalog = {
                  'innovestx_thb': List<Map<String, dynamic>>.from(
                      data['innovestx_thb'] ?? []),
                  'crypto_global': List<Map<String, dynamic>>.from(
                      data['crypto_global'] ?? []),
                  'forex_metals': List<Map<String, dynamic>>.from(
                      data['forex_metals'] ?? []),
                  'stocks':
                      List<Map<String, dynamic>>.from(data['stocks'] ?? []),
                };
                isLoadingCatalog = false;
              });
            }).catchError((_) {
              setModalState(() {
                isLoadingCatalog = false;
              });
            });
          }

          final existingNormSymbols = _watchlistMap.keys
              .map((e) =>
                  e.replaceAll('/', '').replaceAll('-', '').toUpperCase())
              .toSet();

          List<Map<String, dynamic>> currentList =
              catalog[activeCategory] ?? [];
          final q = searchCtrl.text.trim().toUpperCase();
          if (q.isNotEmpty) {
            currentList = currentList.where((it) {
              final sym = (it['symbol'] ?? '').toString().toUpperCase();
              final name = (it['name'] ?? '').toString().toUpperCase();
              return sym.contains(q) || name.contains(q);
            }).toList();
          }

          return Container(
            height: MediaQuery.of(context).size.height * 0.85,
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Center(
                  child: Container(
                    width: 40,
                    height: 4,
                    decoration: BoxDecoration(
                        color: Colors.white24,
                        borderRadius: BorderRadius.circular(2)),
                  ),
                ),
                const SizedBox(height: 12),
                Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Row(
                      children: [
                        Icon(Icons.playlist_add,
                            color: AppColors.bullish, size: 22),
                        SizedBox(width: 8),
                        Text(
                          'เลือกสินทรัพย์เข้า Watchlist & Chart',
                          style: TextStyle(
                              fontSize: 16,
                              fontWeight: FontWeight.bold,
                              color: Colors.white),
                        ),
                      ],
                    ),
                    IconButton(
                      icon: const Icon(Icons.close, color: Colors.white54),
                      onPressed: () => Navigator.pop(ctx),
                    ),
                  ],
                ),
                const SizedBox(height: 8),

                // Category Selector Bar
                SingleChildScrollView(
                  scrollDirection: Axis.horizontal,
                  child: Row(
                    children: [
                      _chartCatChip(
                          '🟣 InnovestX (THB)',
                          'innovestx_thb',
                          activeCategory,
                          (cat) => setModalState(() => activeCategory = cat)),
                      const SizedBox(width: 6),
                      _chartCatChip(
                          '🌐 Crypto (USDT)',
                          'crypto_global',
                          activeCategory,
                          (cat) => setModalState(() => activeCategory = cat)),
                      const SizedBox(width: 6),
                      _chartCatChip(
                          '💱 Forex & Gold',
                          'forex_metals',
                          activeCategory,
                          (cat) => setModalState(() => activeCategory = cat)),
                      const SizedBox(width: 6),
                      _chartCatChip('📈 US Stocks', 'stocks', activeCategory,
                          (cat) => setModalState(() => activeCategory = cat)),
                      const SizedBox(width: 6),
                      _chartCatChip(
                          '✍️ กำหนดเอง (Custom)',
                          'custom',
                          activeCategory,
                          (cat) => setModalState(() => activeCategory = cat)),
                    ],
                  ),
                ),
                const SizedBox(height: 10),

                // Timeframe Bar + Search Bar
                if (activeCategory != 'custom') ...[
                  Row(
                    children: [
                      Expanded(
                        child: TextField(
                          controller: searchCtrl,
                          onChanged: (_) => setModalState(() {}),
                          style: const TextStyle(
                              color: Colors.white, fontSize: 13),
                          decoration: InputDecoration(
                            prefixIcon: const Icon(Icons.search,
                                color: Colors.white54, size: 18),
                            hintText: 'ค้นหาชื่อเหรียญหรือสัญลักษณ์...',
                            hintStyle: const TextStyle(
                                color: Colors.white30, fontSize: 12),
                            filled: true,
                            fillColor: const Color(0xFF1E2533),
                            contentPadding: const EdgeInsets.symmetric(
                                vertical: 0, horizontal: 10),
                            border: OutlineInputBorder(
                              borderRadius: BorderRadius.circular(8),
                              borderSide: const BorderSide(
                                  color: Color(0xFF2E82FE), width: 0.8),
                            ),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 8),
                        decoration: BoxDecoration(
                          color: const Color(0xFF1E2533),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(
                              color: const Color(0xFF2E82FE)
                                  .withValues(alpha: 0.4)),
                        ),
                        child: DropdownButtonHideUnderline(
                          child: DropdownButton<String>(
                            value: selectedTf,
                            dropdownColor: const Color(0xFF1B2333),
                            style: const TextStyle(
                                color: Colors.white,
                                fontSize: 12,
                                fontWeight: FontWeight.bold),
                            items: const [
                              DropdownMenuItem(
                                  value: '15m', child: Text('TF 15M')),
                              DropdownMenuItem(
                                  value: '1h', child: Text('TF 1H')),
                              DropdownMenuItem(
                                  value: '4h', child: Text('TF 4H')),
                              DropdownMenuItem(
                                  value: '1d', child: Text('TF 1D')),
                            ],
                            onChanged: (v) =>
                                setModalState(() => selectedTf = v ?? '1h'),
                          ),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 10),

                  // Asset list
                  Expanded(
                    child: isLoadingCatalog
                        ? const Center(
                            child: CircularProgressIndicator(
                                color: AppColors.bullish))
                        : currentList.isEmpty
                            ? const Center(
                                child: Text('ไม่พบสินทรัพย์ในหมวดนี้',
                                    style: TextStyle(
                                        color: Colors.white38, fontSize: 13)))
                            : ListView.builder(
                                itemCount: currentList.length,
                                itemBuilder: (context, idx) {
                                  final item = currentList[idx];
                                  final sym = item['symbol']?.toString() ?? '';
                                  final name = item['name']?.toString() ?? '';
                                  final ex =
                                      item['exchange']?.toString() ?? 'binance';
                                  final mType =
                                      item['market_type']?.toString() ??
                                          'crypto';

                                  final normSym = sym
                                      .replaceAll('/', '')
                                      .replaceAll('-', '')
                                      .toUpperCase();
                                  final isAlreadyInWatchlist =
                                      existingNormSymbols.contains(normSym);

                                  final isChecked = selectedItems
                                      .any((it) => it['symbol'] == sym);

                                  Color tagBg = const Color(0xFF2E82FE)
                                      .withValues(alpha: 0.2);
                                  Color tagFg = const Color(0xFF2E82FE);
                                  String tagLabel = mType.toUpperCase();

                                  if (activeCategory == 'innovestx_thb' ||
                                      sym.endsWith('/THB')) {
                                    tagBg = const Color(0xFF9B59B6)
                                        .withValues(alpha: 0.25);
                                    tagFg = const Color(0xFFC39BD3);
                                    tagLabel = 'THB';
                                  } else if (activeCategory ==
                                      'crypto_global') {
                                    tagBg = const Color(0xFF2E82FE)
                                        .withValues(alpha: 0.25);
                                    tagFg = const Color(0xFF5DADE2);
                                    tagLabel = 'USDT';
                                  } else if (activeCategory == 'forex_metals') {
                                    tagBg = const Color(0xFFF39C12)
                                        .withValues(alpha: 0.25);
                                    tagFg = const Color(0xFFF8C471);
                                    tagLabel = 'MT5';
                                  } else if (activeCategory == 'stocks') {
                                    tagBg = const Color(0xFF00C087)
                                        .withValues(alpha: 0.25);
                                    tagFg = const Color(0xFF00C087);
                                    tagLabel = 'ALPACA';
                                  }

                                  return Container(
                                    margin: const EdgeInsets.only(bottom: 6),
                                    decoration: BoxDecoration(
                                      color: isAlreadyInWatchlist
                                          ? const Color(0xFF141923)
                                          : (isChecked
                                              ? const Color(0xFF2E82FE)
                                                  .withValues(alpha: 0.15)
                                              : const Color(0xFF1B2333)),
                                      borderRadius: BorderRadius.circular(8),
                                      border: Border.all(
                                        color: isChecked
                                            ? const Color(0xFF2E82FE)
                                            : (isAlreadyInWatchlist
                                                ? Colors.white10
                                                : const Color(0xFF232A38)),
                                        width: isChecked ? 1.2 : 0.8,
                                      ),
                                    ),
                                    child: ListTile(
                                      dense: true,
                                      enabled: !isAlreadyInWatchlist,
                                      onTap: isAlreadyInWatchlist
                                          ? null
                                          : () {
                                              setModalState(() {
                                                if (isChecked) {
                                                  selectedItems.removeWhere(
                                                      (it) =>
                                                          it['symbol'] == sym);
                                                } else {
                                                  selectedItems.add({
                                                    'symbol': sym,
                                                    'market_type': mType,
                                                    'timeframe': selectedTf,
                                                    'htf_timeframe':
                                                        selectedTf == '1d'
                                                            ? '1w'
                                                            : '4h',
                                                    'exchange': ex,
                                                  });
                                                }
                                              });
                                            },
                                      leading: Container(
                                        padding: const EdgeInsets.symmetric(
                                            horizontal: 6, vertical: 3),
                                        decoration: BoxDecoration(
                                            color: tagBg,
                                            borderRadius:
                                                BorderRadius.circular(4)),
                                        child: Text(tagLabel,
                                            style: TextStyle(
                                                fontSize: 10,
                                                fontWeight: FontWeight.bold,
                                                color: tagFg)),
                                      ),
                                      title: Text(
                                        sym,
                                        style: TextStyle(
                                          fontWeight: FontWeight.bold,
                                          fontSize: 14,
                                          color: isAlreadyInWatchlist
                                              ? Colors.white38
                                              : (isChecked
                                                  ? AppColors.bullish
                                                  : Colors.white),
                                        ),
                                      ),
                                      subtitle: Text(
                                        name,
                                        style: TextStyle(
                                            fontSize: 11,
                                            color: isAlreadyInWatchlist
                                                ? Colors.white24
                                                : Colors.white54),
                                      ),
                                      trailing: isAlreadyInWatchlist
                                          ? Container(
                                              padding:
                                                  const EdgeInsets.symmetric(
                                                      horizontal: 6,
                                                      vertical: 2),
                                              decoration: BoxDecoration(
                                                color: Colors.white
                                                    .withValues(alpha: 0.05),
                                                borderRadius:
                                                    BorderRadius.circular(4),
                                              ),
                                              child: const Row(
                                                mainAxisSize: MainAxisSize.min,
                                                children: [
                                                  Icon(Icons.check,
                                                      size: 14,
                                                      color: AppColors.bullish),
                                                  SizedBox(width: 4),
                                                  Text('อยู่ใน Watchlist แล้ว',
                                                      style: TextStyle(
                                                          fontSize: 10,
                                                          color:
                                                              Colors.white54)),
                                                ],
                                              ),
                                            )
                                          : Checkbox(
                                              value: isChecked,
                                              activeColor: AppColors.bullish,
                                              checkColor: Colors.black,
                                              onChanged: (val) {
                                                setModalState(() {
                                                  if (val == true) {
                                                    selectedItems.add({
                                                      'symbol': sym,
                                                      'market_type': mType,
                                                      'timeframe': selectedTf,
                                                      'htf_timeframe':
                                                          selectedTf == '1d'
                                                              ? '1w'
                                                              : '4h',
                                                      'exchange': ex,
                                                    });
                                                  } else {
                                                    selectedItems.removeWhere(
                                                        (it) =>
                                                            it['symbol'] ==
                                                            sym);
                                                  }
                                                });
                                              },
                                            ),
                                    ),
                                  );
                                },
                              ),
                  ),
                ] else ...[
                  // Custom input
                  Expanded(
                    child: SingleChildScrollView(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('เพิ่มสินทรัพย์ระบุเอง (Custom Symbol):',
                              style: TextStyle(
                                  fontSize: 13,
                                  fontWeight: FontWeight.bold,
                                  color: Colors.white)),
                          const SizedBox(height: 10),
                          TextField(
                            controller: customSymCtrl,
                            textCapitalization: TextCapitalization.characters,
                            style: const TextStyle(
                                color: Colors.white,
                                fontWeight: FontWeight.bold),
                            decoration: const InputDecoration(
                              hintText: 'เช่น DOGE/THB, BTC/USD, GBPJPY, AMZN',
                              hintStyle: TextStyle(
                                  color: Colors.white30, fontSize: 13),
                              prefixIcon:
                                  Icon(Icons.edit, color: Colors.white60),
                            ),
                          ),
                          const SizedBox(height: 14),
                          const Text('ประเภทตลาด (Market Type):',
                              style: TextStyle(
                                  fontSize: 12, color: AppColors.textMuted)),
                          const SizedBox(height: 6),
                          Row(
                            children: [
                              _chartDlgMarketChip(
                                  'Crypto',
                                  'crypto',
                                  customMarketType,
                                  (m) => setModalState(
                                      () => customMarketType = m)),
                              const SizedBox(width: 6),
                              _chartDlgMarketChip(
                                  'Forex/Gold',
                                  'forex',
                                  customMarketType,
                                  (m) => setModalState(
                                      () => customMarketType = m)),
                              const SizedBox(width: 6),
                              _chartDlgMarketChip(
                                  'Stocks',
                                  'stock',
                                  customMarketType,
                                  (m) => setModalState(
                                      () => customMarketType = m)),
                            ],
                          ),
                          const SizedBox(height: 14),
                          const Text('Timeframe สแกนหลัก:',
                              style: TextStyle(
                                  fontSize: 12, color: AppColors.textMuted)),
                          const SizedBox(height: 6),
                          Wrap(
                            spacing: 8,
                            children: ['15m', '1h', '4h', '1d'].map((tf) {
                              final isSel = selectedTf == tf;
                              return ChoiceChip(
                                label: Text(tf.toUpperCase(),
                                    style: TextStyle(
                                        fontSize: 11,
                                        color: isSel
                                            ? Colors.black
                                            : Colors.white)),
                                selected: isSel,
                                selectedColor: AppColors.bullish,
                                backgroundColor: const Color(0xFF252540),
                                onSelected: (_) =>
                                    setModalState(() => selectedTf = tf),
                              );
                            }).toList(),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],

                const SizedBox(height: 10),
                // Submit button
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    onPressed: () async {
                      if (activeCategory == 'custom') {
                        final sym = customSymCtrl.text.trim().toUpperCase();
                        if (sym.isNotEmpty) {
                          Navigator.pop(ctx);
                          await _addAndSelectSymbol(sym, customMarketType);
                        }
                      } else {
                        if (selectedItems.isEmpty) return;
                        Navigator.pop(ctx);
                        final itemsList = selectedItems.toList();
                        try {
                          final dio = AppApi.dio;
                          await dio.post(
                            AppApi.url('/api/v1/settings/watchlist/batch'),
                            cancelToken: _requestCancelToken,
                            data: {'items': itemsList},
                          );
                        } catch (_) {}
                        await _fetchWatchlist();
                        if (itemsList.isNotEmpty) {
                          final first = itemsList.first;
                          _switchToSymbol(
                            first['symbol'] ?? _selectedSymbol,
                            targetMarket: first['market_type'] ?? 'crypto',
                          );
                        }
                      }
                    },
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.bullish,
                      foregroundColor: Colors.black,
                      padding: const EdgeInsets.symmetric(vertical: 12),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                    ),
                    child: Text(
                      activeCategory == 'custom'
                          ? '➕ เพิ่มสินทรัพย์ & ดูกราฟทันที'
                          : '➕ เพิ่มที่เลือก (${selectedItems.length}) เข้า Watchlist & ดูกราฟ',
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 13),
                    ),
                  ),
                ),
              ],
            ),
          );
        },
      ),
    ).whenComplete(() {
      customSymCtrl.dispose();
      searchCtrl.dispose();
    });
  }

  Widget _chartCatChip(
      String label, String catId, String activeCat, Function(String) onSelect) {
    final isSel = activeCat == catId;
    return GestureDetector(
      onTap: () => onSelect(catId),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: isSel
              ? AppColors.bullish.withValues(alpha: 0.2)
              : const Color(0xFF1E2533),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
              color: isSel ? AppColors.bullish : const Color(0xFF232A38)),
        ),
        child: Text(
          label,
          style: TextStyle(
            fontSize: 12,
            fontWeight: isSel ? FontWeight.bold : FontWeight.normal,
            color: isSel ? AppColors.bullish : Colors.white70,
          ),
        ),
      ),
    );
  }

  Widget _chartDlgMarketChip(String label, String market, String selectedMarket,
      Function(String) onSelect) {
    final isSel = selectedMarket == market;
    return Expanded(
      child: GestureDetector(
        onTap: () => onSelect(market),
        child: Container(
          padding: const EdgeInsets.symmetric(vertical: 8),
          alignment: Alignment.center,
          decoration: BoxDecoration(
            color: isSel
                ? const Color(0xFF2E82FE).withValues(alpha: 0.25)
                : const Color(0xFF1E2533),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
                color: isSel ? const Color(0xFF2E82FE) : AppColors.border),
          ),
          child: Text(
            label,
            style: TextStyle(
              fontSize: 11,
              fontWeight: isSel ? FontWeight.bold : FontWeight.normal,
              color: isSel ? Colors.white : AppColors.textMuted,
            ),
          ),
        ),
      ),
    );
  }

  Future<void> _addAndSelectSymbol(String sym, String market) async {
    final isTHB = sym.toUpperCase().contains('THB');
    final ex = isTHB
        ? 'innovestx'
        : (market == 'crypto'
            ? 'binance'
            : (market == 'forex' ? 'mt5' : 'alpaca'));

    if (market == 'stock') {
      if (!_stockSymbols.contains(sym)) _stockSymbols.add(sym);
    } else if (market == 'forex') {
      if (!_forexSymbols.contains(sym)) _forexSymbols.add(sym);
    } else {
      if (!_cryptoSymbols.contains(sym)) _cryptoSymbols.add(sym);
    }

    _switchToSymbol(sym, targetMarket: market, targetExchange: ex);

    // Save to backend watchlist so proactive scanner also monitors it
    try {
      final dio = AppApi.dio;
      await dio.post(
        AppApi.url('/api/v1/settings/watchlist'),
        cancelToken: _requestCancelToken,
        data: {
          'symbol': sym,
          'market_type': market,
          'timeframe': _selectedTimeframe,
          'htf_timeframe': _selectedHtfTimeframe,
          'exchange': ex,
        },
      );
      await _fetchWatchlist();
    } catch (_) {}
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
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            const Text('SMC ZONES:',
                style: TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: AppColors.textMuted)),
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
            const SizedBox(width: 14),
            const Text('HTF: ',
                style: TextStyle(fontSize: 10, color: AppColors.textMuted)),
            Text(_selectedHtfTimeframe.toUpperCase(),
                style: const TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                    color: Colors.white)),
          ],
        ),
      ),
    );
  }

  Widget _smcZonePill(String text, Color color) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.15),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withValues(alpha: 0.4)),
      ),
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Container(
              width: 6,
              height: 6,
              decoration: BoxDecoration(color: color, shape: BoxShape.circle)),
          const SizedBox(width: 5),
          Text(text,
              style: TextStyle(
                  fontSize: 11, color: color, fontWeight: FontWeight.bold)),
        ],
      ),
    );
  }

  Map<String, dynamic>? get _indicatorDecisionData {
    final raw = _smcOverlayData?['indicator_decision'];
    if (raw is! Map || raw.isEmpty) return null;
    return Map<String, dynamic>.from(raw);
  }

  Map<String, dynamic>? get _marketRegimeData {
    final raw = _smcOverlayData?['market_regime'];
    if (raw is! Map || raw.isEmpty) return null;
    return Map<String, dynamic>.from(raw);
  }

  Color _marketRegimeColor(Map<String, dynamic> regime) {
    switch (regime['regime']?.toString()) {
      case 'trending':
        return regime['direction'] == 'bearish'
            ? AppColors.bearish
            : AppColors.bullish;
      case 'ranging':
        return const Color(0xFFFFC857);
      case 'volatile':
        return const Color(0xFFFF7A45);
      case 'compression':
        return const Color(0xFFB388FF);
      default:
        return const Color(0xFF687386);
    }
  }

  IconData _marketRegimeIcon(String regime) {
    switch (regime) {
      case 'trending':
        return Icons.trending_up;
      case 'ranging':
        return Icons.swap_horiz;
      case 'volatile':
        return Icons.bolt;
      case 'compression':
        return Icons.compress;
      default:
        return Icons.help_outline;
    }
  }

  Widget _buildMarketRegimeBar() {
    final regime = _marketRegimeData!;
    final policyRaw = regime['effective_policy'] ?? regime['policy'];
    final policy = policyRaw is Map
        ? Map<String, dynamic>.from(policyRaw)
        : <String, dynamic>{};
    final regimeId = regime['regime']?.toString() ?? 'unknown';
    final label = regime['label']?.toString() ?? regimeId.toUpperCase();
    final direction = regime['direction']?.toString() ?? 'neutral';
    final confidence = (regime['confidence'] as num? ?? 0).round();
    final entryAllowed = policy['entry_allowed'] == true;
    final riskMultiplier = (policy['risk_multiplier'] as num? ?? 0).toDouble();
    final minConfluence = (policy['min_confluence'] as num? ?? 100).round();
    final minRr = (policy['min_rr'] as num? ?? 0).toDouble();
    final color = _marketRegimeColor(regime);

    return Material(
      color: const Color(0xFF10151F),
      child: InkWell(
        onTap: _showMarketRegimeDetails,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 5),
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                Icon(_marketRegimeIcon(regimeId), size: 14, color: color),
                const SizedBox(width: 5),
                const Text(
                  'MARKET REGIME',
                  style: TextStyle(
                    fontSize: 9.5,
                    color: Colors.white70,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(width: 7),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 7, vertical: 2),
                  decoration: BoxDecoration(
                    color: color.withValues(alpha: 0.13),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(
                        color: color.withValues(alpha: 0.55), width: 0.8),
                  ),
                  child: Text(
                    '${label.toUpperCase()} · $confidence%',
                    style: TextStyle(
                        fontSize: 9.5,
                        color: color,
                        fontWeight: FontWeight.bold),
                  ),
                ),
                const SizedBox(width: 7),
                Text(
                  direction == 'neutral' ? 'NEUTRAL' : direction.toUpperCase(),
                  style: const TextStyle(
                      fontSize: 9,
                      color: Colors.white54,
                      fontWeight: FontWeight.w600),
                ),
                const SizedBox(width: 8),
                Text(
                  'C$minConfluence · RR ${minRr.toStringAsFixed(1)} · RISK ×${riskMultiplier.toStringAsFixed(2)}',
                  style: const TextStyle(
                      fontSize: 9,
                      color: AppColors.textMuted,
                      fontWeight: FontWeight.w600),
                ),
                const SizedBox(width: 8),
                Text(
                  entryAllowed ? 'SELECTIVE ENTRY' : 'NO NEW ENTRY',
                  style: TextStyle(
                      fontSize: 9,
                      color:
                          entryAllowed ? AppColors.bullish : AppColors.bearish,
                      fontWeight: FontWeight.bold),
                ),
                const Icon(Icons.chevron_right,
                    size: 14, color: Colors.white38),
              ],
            ),
          ),
        ),
      ),
    );
  }

  void _showMarketRegimeDetails() {
    final regime = _marketRegimeData;
    if (regime == null) return;
    final policyRaw = regime['effective_policy'] ?? regime['policy'];
    final policy = policyRaw is Map
        ? Map<String, dynamic>.from(policyRaw)
        : <String, dynamic>{};
    final metrics = regime['metrics'] is Map
        ? Map<String, dynamic>.from(regime['metrics'] as Map)
        : <String, dynamic>{};
    final evidence = (regime['evidence'] as List? ?? const [])
        .map((item) => item.toString())
        .toList(growable: false);
    final regimeId = regime['regime']?.toString() ?? 'unknown';
    final color = _marketRegimeColor(regime);
    final entryAllowed = policy['entry_allowed'] == true;

    showModalBottomSheet<void>(
      context: context,
      backgroundColor: const Color(0xFF131722),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.76,
        child: SafeArea(
          child: ListView(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 18),
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
              const SizedBox(height: 10),
              Row(
                children: [
                  Icon(_marketRegimeIcon(regimeId), color: color, size: 21),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      'Market Regime · ${regime['label'] ?? regimeId}',
                      style: const TextStyle(
                          color: Colors.white,
                          fontSize: 16,
                          fontWeight: FontWeight.bold),
                    ),
                  ),
                  IconButton(
                    onPressed: () => Navigator.pop(ctx),
                    icon: const Icon(Icons.close,
                        size: 19, color: Colors.white54),
                  ),
                ],
              ),
              Container(
                padding: const EdgeInsets.all(12),
                decoration: BoxDecoration(
                  color: color.withValues(alpha: 0.08),
                  borderRadius: BorderRadius.circular(8),
                  border: Border.all(color: color.withValues(alpha: 0.45)),
                ),
                child: Row(
                  children: [
                    Expanded(
                      child: _indicatorSummaryMetric(
                        'CONFIDENCE',
                        '${(regime['confidence'] as num? ?? 0).round()}%',
                        color,
                      ),
                    ),
                    Expanded(
                      child: _indicatorSummaryMetric(
                        'DIRECTION',
                        (regime['direction'] ?? 'neutral')
                            .toString()
                            .toUpperCase(),
                        color,
                      ),
                    ),
                    Expanded(
                      child: _indicatorSummaryMetric(
                        'ENTRY GATE',
                        entryAllowed ? 'SELECTIVE' : 'BLOCKED',
                        entryAllowed ? AppColors.bullish : AppColors.bearish,
                      ),
                    ),
                  ],
                ),
              ),
              const SizedBox(height: 12),
              const Text('ADAPTIVE POLICY',
                  style: TextStyle(
                      fontSize: 10,
                      color: AppColors.textMuted,
                      fontWeight: FontWeight.bold)),
              const SizedBox(height: 6),
              Wrap(
                spacing: 6,
                runSpacing: 6,
                children: [
                  _regimeChip('Min confluence',
                      '${(policy['min_confluence'] as num? ?? 100).round()}'),
                  _regimeChip('Min R:R',
                      (policy['min_rr'] as num? ?? 0).toStringAsFixed(1)),
                  _regimeChip('Risk multiplier',
                      '×${(policy['risk_multiplier'] as num? ?? 0).toStringAsFixed(2)}'),
                  if (policy['require_direction_alignment'] == true)
                    _regimeChip('Required', 'Direction alignment'),
                  if (policy['require_liquidity_sweep'] == true)
                    _regimeChip('Required', 'Liquidity sweep'),
                  if (policy['require_volume_confirmation'] == true)
                    _regimeChip('Required', 'Volume Delta'),
                  if (policy['require_squeeze_fire'] == true)
                    _regimeChip('Required', 'Squeeze release'),
                ],
              ),
              if (metrics.isNotEmpty) ...[
                const SizedBox(height: 14),
                const Text('MARKET-STATE STATISTICS (NOT EXTRA INDICATORS)',
                    style: TextStyle(
                        fontSize: 10,
                        color: AppColors.textMuted,
                        fontWeight: FontWeight.bold)),
                const SizedBox(height: 6),
                Wrap(
                  spacing: 6,
                  runSpacing: 6,
                  children: metrics.entries
                      .map((entry) => _regimeChip(
                          entry.key.replaceAll('_', ' '),
                          entry.value is num
                              ? (entry.value as num).toStringAsFixed(2)
                              : entry.value.toString()))
                      .toList(growable: false),
                ),
              ],
              if (evidence.isNotEmpty) ...[
                const SizedBox(height: 14),
                const Text('WHY THIS REGIME',
                    style: TextStyle(
                        fontSize: 10,
                        color: AppColors.textMuted,
                        fontWeight: FontWeight.bold)),
                const SizedBox(height: 6),
                ...evidence.map((item) => Padding(
                      padding: const EdgeInsets.only(bottom: 5),
                      child: Text('• $item',
                          style: const TextStyle(
                              fontSize: 11,
                              color: Colors.white70,
                              height: 1.3)),
                    )),
              ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _regimeChip(String label, String value) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(
        color: const Color(0xFF19202E),
        borderRadius: BorderRadius.circular(5),
        border: Border.all(color: const Color(0xFF303A4D)),
      ),
      child: Text('$label · $value',
          style: const TextStyle(fontSize: 9.5, color: Colors.white70)),
    );
  }

  List<Map<String, dynamic>> _indicatorLayers(Map<String, dynamic> decision) {
    final raw = decision['layers'];
    if (raw is! List) return const [];
    return raw
        .whereType<Map>()
        .map((layer) => Map<String, dynamic>.from(layer))
        .toList(growable: false);
  }

  Color _indicatorLayerColor(Map<String, dynamic> layer) {
    if (layer['enabled'] != true || layer['available'] != true) {
      return const Color(0xFF687386);
    }
    switch (layer['status']?.toString()) {
      case 'strong':
        return AppColors.bullish;
      case 'supporting':
        return const Color(0xFFFFC857);
      case 'weak':
        return AppColors.bearish;
      default:
        return const Color(0xFF687386);
    }
  }

  Widget _buildIndicatorDecisionBar() {
    final decision = _indicatorDecisionData!;
    final layers = _indicatorLayers(decision);
    final score = (decision['score'] as num? ?? 0).round();
    final coverage = (decision['coverage'] as num? ?? 0).toDouble();
    final ready = decision['ready'] == true;
    final stateColor = ready ? AppColors.bullish : AppColors.bearish;

    return Material(
      color: const Color(0xFF0B1118),
      child: InkWell(
        onTap: _showIndicatorDecisionDetails,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 5),
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                const Icon(Icons.account_tree_outlined,
                    size: 13, color: Color(0xFF00E5FF)),
                const SizedBox(width: 5),
                const Text(
                  '3-INDICATOR CORE',
                  style: TextStyle(
                    fontSize: 9.5,
                    fontWeight: FontWeight.w700,
                    color: Colors.white70,
                  ),
                ),
                const SizedBox(width: 8),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: stateColor.withValues(alpha: 0.13),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(
                        color: stateColor.withValues(alpha: 0.55), width: 0.8),
                  ),
                  child: Text(
                    '$score/100 · ${ready ? 'DATA OK' : 'DATA BLOCK'}',
                    style: TextStyle(
                      fontSize: 9.5,
                      color: stateColor,
                      fontWeight: FontWeight.bold,
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Text(
                  'DATA ${coverage.toStringAsFixed(0)}%',
                  style: const TextStyle(
                    fontSize: 9,
                    color: AppColors.textMuted,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(width: 8),
                ...layers.map(_buildIndicatorLayerPill),
                const Icon(Icons.chevron_right,
                    size: 14, color: Colors.white38),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _buildIndicatorLayerPill(Map<String, dynamic> layer) {
    final color = _indicatorLayerColor(layer);
    final enabled = layer['enabled'] == true;
    final available = layer['available'] == true;
    final value = !enabled
        ? 'OFF'
        : (!available ? 'N/A' : '${(layer['score'] as num? ?? 0).round()}');
    final label = layer['short_label']?.toString() ?? 'IND';

    return Container(
      margin: const EdgeInsets.only(right: 6),
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.11),
        borderRadius: BorderRadius.circular(4),
        border: Border.all(color: color.withValues(alpha: 0.35), width: 0.7),
      ),
      child: Text(
        '$label $value',
        style: TextStyle(
          fontSize: 9,
          color: color,
          fontWeight: FontWeight.w700,
        ),
      ),
    );
  }

  void _showIndicatorDecisionDetails() {
    final decision = _indicatorDecisionData;
    if (decision == null) return;
    final layers = _indicatorLayers(decision);
    final score = (decision['score'] as num? ?? 0).round();
    final coverage = (decision['coverage'] as num? ?? 0).toDouble();
    final minimum = (decision['minimum_coverage'] as num? ?? 0).toDouble();
    final ready = decision['ready'] == true;
    final blockingReasons = (decision['blocking_reasons'] as List? ?? const [])
        .map((reason) => reason.toString())
        .toList(growable: false);
    final stateColor = ready ? AppColors.bullish : AppColors.bearish;

    showModalBottomSheet<void>(
      context: context,
      backgroundColor: const Color(0xFF131722),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.vertical(top: Radius.circular(16)),
      ),
      builder: (ctx) => FractionallySizedBox(
        heightFactor: 0.78,
        child: SafeArea(
          child: Padding(
            padding: const EdgeInsets.fromLTRB(16, 10, 16, 12),
            child: Column(
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
                const SizedBox(height: 10),
                Row(
                  children: [
                    const Icon(Icons.account_tree_outlined,
                        color: Color(0xFF00E5FF), size: 20),
                    const SizedBox(width: 8),
                    const Expanded(
                      child: Text(
                        'Indicator Decision Core',
                        style: TextStyle(
                          color: Colors.white,
                          fontSize: 16,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                    ),
                    IconButton(
                      onPressed: () => Navigator.pop(ctx),
                      icon: const Icon(Icons.close,
                          size: 19, color: Colors.white54),
                    ),
                  ],
                ),
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(12),
                  decoration: BoxDecoration(
                    color: const Color(0xFF19202E),
                    borderRadius: BorderRadius.circular(8),
                    border:
                        Border.all(color: stateColor.withValues(alpha: 0.45)),
                  ),
                  child: Row(
                    children: [
                      Expanded(
                        child: _indicatorSummaryMetric(
                            'SCORE', '$score/100', stateColor),
                      ),
                      Expanded(
                        child: _indicatorSummaryMetric(
                          'DATA COVERAGE',
                          '${coverage.toStringAsFixed(0)}% / ${minimum.toStringAsFixed(0)}%',
                          coverage >= minimum
                              ? AppColors.bullish
                              : AppColors.bearish,
                        ),
                      ),
                      Expanded(
                        child: _indicatorSummaryMetric(
                          'DATA GATE',
                          ready ? 'READY' : 'BLOCKED',
                          stateColor,
                        ),
                      ),
                    ],
                  ),
                ),
                if (blockingReasons.isNotEmpty) ...[
                  const SizedBox(height: 8),
                  Container(
                    width: double.infinity,
                    padding: const EdgeInsets.all(9),
                    decoration: BoxDecoration(
                      color: AppColors.bearish.withValues(alpha: 0.1),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Text(
                      blockingReasons.join('\n'),
                      style: const TextStyle(
                          fontSize: 11, color: AppColors.bearish, height: 1.35),
                    ),
                  ),
                ],
                const SizedBox(height: 12),
                const Text(
                  '3 REGISTERED INDICATOR LAYERS',
                  style: TextStyle(
                    fontSize: 10,
                    color: AppColors.textMuted,
                    fontWeight: FontWeight.bold,
                  ),
                ),
                const SizedBox(height: 6),
                Expanded(
                  child: ListView.separated(
                    itemCount: layers.length,
                    separatorBuilder: (_, __) => const SizedBox(height: 7),
                    itemBuilder: (_, index) =>
                        _buildIndicatorLayerCard(layers[index]),
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _indicatorSummaryMetric(String label, String value, Color color) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: const TextStyle(fontSize: 8.5, color: AppColors.textMuted)),
        const SizedBox(height: 2),
        Text(value,
            style: TextStyle(
                fontSize: 12, color: color, fontWeight: FontWeight.bold)),
      ],
    );
  }

  Widget _buildIndicatorLayerCard(Map<String, dynamic> layer) {
    final color = _indicatorLayerColor(layer);
    final enabled = layer['enabled'] == true;
    final available = layer['available'] == true;
    final score = (layer['score'] as num? ?? 0).round();
    final weight = (layer['weight'] as num? ?? 0).toDouble();
    final points = (layer['weighted_points'] as num? ?? 0).toDouble();
    final evidence = (layer['evidence'] as List? ?? const [])
        .map((item) => item.toString())
        .toList(growable: false);
    final state = !enabled
        ? 'DISABLED'
        : (!available ? 'DATA UNAVAILABLE' : '${layer['status']} · $score/100');

    return Container(
      padding: const EdgeInsets.all(11),
      decoration: BoxDecoration(
        color: const Color(0xFF19202E),
        borderRadius: BorderRadius.circular(8),
        border: Border.all(color: color.withValues(alpha: 0.35)),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Container(
                width: 8,
                height: 8,
                decoration: BoxDecoration(color: color, shape: BoxShape.circle),
              ),
              const SizedBox(width: 7),
              Expanded(
                child: Text(
                  layer['label']?.toString() ?? 'Indicator',
                  style: const TextStyle(
                    fontSize: 12,
                    color: Colors.white,
                    fontWeight: FontWeight.bold,
                  ),
                ),
              ),
              Text(
                state.toUpperCase(),
                style: TextStyle(
                    fontSize: 9.5, color: color, fontWeight: FontWeight.bold),
              ),
            ],
          ),
          const SizedBox(height: 5),
          Text(
            layer['description']?.toString() ?? '',
            style: const TextStyle(
                fontSize: 10.5, color: Colors.white54, height: 1.3),
          ),
          if (enabled) ...[
            const SizedBox(height: 6),
            Text(
              'Weight ${weight.toStringAsFixed(0)} · Contribution ${points.toStringAsFixed(1)}',
              style: const TextStyle(fontSize: 9.5, color: AppColors.textMuted),
            ),
          ],
          if (evidence.isNotEmpty) ...[
            const SizedBox(height: 6),
            ...evidence.map(
              (item) => Padding(
                padding: const EdgeInsets.only(bottom: 2),
                child: Text(
                  '• $item',
                  style: const TextStyle(
                      fontSize: 10.5, color: Colors.white70, height: 1.3),
                ),
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _buildMtfMatrixBar() {
    final matrix = _mtfMatrixData?['matrix'] as Map<String, dynamic>? ?? {};
    final gradeBadge =
        _mtfMatrixData?['grade_badge']?.toString() ?? '⚖️ MTF MATRIX';
    final hasAbsorption = _mtfMatrixData?['absorption_found'] == true;
    final isGradeA =
        gradeBadge.contains('GRADE A') || gradeBadge.contains('SUPREME');

    return InkWell(
      onTap: _showMtfModal,
      child: Container(
        color: const Color(0xFF0D111A),
        padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 5),
        child: Row(
          children: [
            // Grade Badge
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
              decoration: BoxDecoration(
                color: isGradeA
                    ? AppColors.bullish.withValues(alpha: 0.18)
                    : const Color(0xFF222938),
                borderRadius: BorderRadius.circular(4),
                border: Border.all(
                    color:
                        isGradeA ? AppColors.bullish : const Color(0xFF38455E),
                    width: 0.8),
              ),
              child: Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(
                    gradeBadge,
                    style: TextStyle(
                      fontSize: 10,
                      fontWeight: FontWeight.bold,
                      color: isGradeA ? AppColors.bullish : Colors.white70,
                    ),
                  ),
                ],
              ),
            ),
            if (hasAbsorption) ...[
              const SizedBox(width: 6),
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 5, vertical: 2),
                decoration: BoxDecoration(
                  color: const Color(0xFF9B59B6).withValues(alpha: 0.2),
                  borderRadius: BorderRadius.circular(4),
                  border:
                      Border.all(color: const Color(0xFF9B59B6), width: 0.8),
                ),
                child: const Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Text('🐳 CVD Absorption',
                        style: TextStyle(
                            fontSize: 9,
                            color: Color(0xFFD29BFF),
                            fontWeight: FontWeight.bold)),
                  ],
                ),
              ),
            ],
            const Spacer(),
            // Ordered Phase 5 role profiles: Bias -> Setup -> Trigger.
            ...['4h', '1h', '15m'].map((tfKey) {
              final tfData = matrix[tfKey] as Map<String, dynamic>?;
              final bias = tfData?['bias']?.toString() ?? 'neutral';
              final isBull = bias == 'bullish';
              final isBear = bias == 'bearish';
              final color = isBull
                  ? AppColors.bullish
                  : (isBear ? AppColors.bearish : const Color(0xFFFFD54F));
              return Padding(
                padding: const EdgeInsets.only(left: 6),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Container(
                      width: 6.5,
                      height: 6.5,
                      decoration:
                          BoxDecoration(color: color, shape: BoxShape.circle),
                    ),
                    const SizedBox(width: 3),
                    Text(
                      tfKey.toUpperCase(),
                      style: TextStyle(
                        fontSize: 9.5,
                        fontWeight: FontWeight.w600,
                        color: tfKey == _selectedTimeframe.toLowerCase()
                            ? Colors.white
                            : Colors.white54,
                      ),
                    ),
                  ],
                ),
              );
            }),
            const SizedBox(width: 4),
            const Icon(Icons.chevron_right, size: 14, color: Colors.white38),
          ],
        ),
      ),
    );
  }

  void _showMtfModal() {
    final data = _mtfMatrixData;
    final matrix = data?['matrix'] as Map<String, dynamic>? ?? {};
    final gradeBadge = data?['grade_badge']?.toString() ?? '⚖️ GRADE B';
    final alignCount = data?['alignment_count'] ?? 2;
    final totalTf = data?['total_timeframes'] ?? 4;
    final alignedBias = data?['aligned_bias']?.toString() ?? 'BULLISH';
    final summaryTh =
        data?['summary_th']?.toString() ?? 'วิเคราะห์ความสอดคล้อง 4 Timeframes';
    final isBull = alignedBias == 'BULLISH';
    final biasColor = isBull
        ? AppColors.bullish
        : (alignedBias == 'BEARISH'
            ? AppColors.bearish
            : const Color(0xFFFFD54F));

    showModalBottomSheet(
      context: context,
      backgroundColor: const Color(0xFF131722),
      isScrollControlled: true,
      shape: const RoundedRectangleBorder(
          borderRadius: BorderRadius.vertical(top: Radius.circular(16))),
      builder: (ctx) => Container(
        height: MediaQuery.of(context).size.height * 0.72,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Center(
              child: Container(
                  width: 40,
                  height: 4,
                  decoration: BoxDecoration(
                      color: Colors.white24,
                      borderRadius: BorderRadius.circular(2))),
            ),
            const SizedBox(height: 12),
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                Row(
                  children: [
                    const Icon(Icons.hub_outlined,
                        color: Color(0xFF00E5FF), size: 20),
                    const SizedBox(width: 8),
                    Text('Multi-Timeframe Matrix ($_selectedSymbol)',
                        style: const TextStyle(
                            fontSize: 15,
                            fontWeight: FontWeight.bold,
                            color: Colors.white)),
                  ],
                ),
                IconButton(
                    icon: const Icon(Icons.close,
                        color: Colors.white54, size: 18),
                    onPressed: () => Navigator.pop(ctx)),
              ],
            ),
            const SizedBox(height: 6),
            // Overall Grade Card
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFF19202E),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: biasColor.withValues(alpha: 0.4)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text(gradeBadge,
                          style: const TextStyle(
                              fontSize: 14,
                              fontWeight: FontWeight.bold,
                              color: Colors.white)),
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 8, vertical: 2),
                        decoration: BoxDecoration(
                          color: biasColor.withValues(alpha: 0.2),
                          borderRadius: BorderRadius.circular(4),
                          border: Border.all(color: biasColor, width: 0.8),
                        ),
                        child: Text(
                          '$alignedBias ($alignCount/$totalTf TF)',
                          style: TextStyle(
                              color: biasColor,
                              fontSize: 11,
                              fontWeight: FontWeight.bold),
                        ),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Text(summaryTh,
                      style: const TextStyle(
                          fontSize: 12, color: Colors.white70, height: 1.4)),
                ],
              ),
            ),
            const SizedBox(height: 12),
            const Text('TIME FRAME BREAKDOWN',
                style: TextStyle(
                    fontSize: 11,
                    fontWeight: FontWeight.bold,
                    color: AppColors.textMuted)),
            const SizedBox(height: 8),
            // Ordered timeframe-role breakdown.
            Expanded(
              child: ListView(
                children: ['4h', '1h', '15m'].map((tfKey) {
                  final tfData = matrix[tfKey] as Map<String, dynamic>? ?? {};
                  final tfName = tfKey == '4h'
                      ? '4H (Market Bias)'
                      : (tfKey == '1h'
                          ? '1H (SMC Setup)'
                          : '15M (Entry Trigger)');
                  final bias = tfData['bias']?.toString() ?? 'neutral';
                  final isTfBull = bias == 'bullish';
                  final isTfBear = bias == 'bearish';
                  final tfColor = isTfBull
                      ? AppColors.bullish
                      : (isTfBear
                          ? AppColors.bearish
                          : const Color(0xFFFFD54F));
                  final stageStatus =
                      tfData['status']?.toString().toUpperCase() ?? 'BLOCKED';
                  final reasons = (tfData['reasons'] as List? ?? const [])
                      .map((item) => item.toString())
                      .toList(growable: false);
                  final triggerEvidence =
                      (tfData['trigger_evidence'] as List? ?? const [])
                          .map((item) => item.toString())
                          .toList(growable: false);
                  final statusLabel = triggerEvidence.isNotEmpty
                      ? triggerEvidence.join(', ').toUpperCase()
                      : (reasons.isEmpty ? stageStatus : reasons.first);
                  final zone = tfData['order_block'] != null
                      ? 'ORDER BLOCK'
                      : (tfData['fvg'] != null ? 'FVG' : 'EQUILIBRIUM');
                  final score = tfData['confluence'] ?? 0;
                  final deltaStatus =
                      tfData['delta_status']?.toString() ?? 'Neutral';
                  final hasAbs = tfData['delta_absorption'] == true;

                  return Container(
                    margin: const EdgeInsets.only(bottom: 8),
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFF161B26),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: const Color(0xFF252D3D)),
                    ),
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Row(
                          children: [
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                color: tfColor.withValues(alpha: 0.15),
                                borderRadius: BorderRadius.circular(4),
                                border: Border.all(color: tfColor, width: 0.8),
                              ),
                              child: Text(bias.toUpperCase(),
                                  style: TextStyle(
                                      color: tfColor,
                                      fontSize: 10,
                                      fontWeight: FontWeight.bold)),
                            ),
                            const SizedBox(width: 8),
                            Text(tfName,
                                style: const TextStyle(
                                    color: Colors.white,
                                    fontWeight: FontWeight.bold,
                                    fontSize: 12)),
                            const Spacer(),
                            Text('Confluence: $score/100',
                                style: TextStyle(
                                    color: tfColor,
                                    fontSize: 11,
                                    fontWeight: FontWeight.bold)),
                          ],
                        ),
                        const SizedBox(height: 6),
                        Row(
                          children: [
                            const Text('Zone: ',
                                style: TextStyle(
                                    fontSize: 10.5, color: Colors.white38)),
                            Text(zone,
                                style: const TextStyle(
                                    fontSize: 10.5,
                                    color: Colors.white70,
                                    fontWeight: FontWeight.w600)),
                            const SizedBox(width: 12),
                            const Text('Signals: ',
                                style: TextStyle(
                                    fontSize: 10.5, color: Colors.white38)),
                            Expanded(
                                child: Text(statusLabel,
                                    style: const TextStyle(
                                        fontSize: 10.5,
                                        color: Color(0xFF93C5FD),
                                        fontWeight: FontWeight.w600),
                                    overflow: TextOverflow.ellipsis)),
                          ],
                        ),
                        if (hasAbs || deltaStatus != 'Neutral') ...[
                          const SizedBox(height: 4),
                          Row(
                            children: [
                              const Text('Volume Delta: ',
                                  style: TextStyle(
                                      fontSize: 10.5, color: Colors.white38)),
                              Expanded(
                                child: Text(
                                  deltaStatus,
                                  style: TextStyle(
                                    fontSize: 10.5,
                                    color: hasAbs
                                        ? const Color(0xFFD29BFF)
                                        : Colors.white60,
                                    fontWeight: hasAbs
                                        ? FontWeight.bold
                                        : FontWeight.normal,
                                  ),
                                  overflow: TextOverflow.ellipsis,
                                ),
                              ),
                            ],
                          ),
                        ],
                      ],
                    ),
                  );
                }).toList(),
              ),
            ),
          ],
        ),
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
              CircularProgressIndicator(
                  color: AppColors.bullish, strokeWidth: 2),
              SizedBox(height: 16),
              Text('Connecting to market data feed...',
                  style: TextStyle(color: AppColors.textMuted, fontSize: 13)),
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
              const Icon(Icons.signal_cellular_connected_no_internet_4_bar,
                  size: 48, color: AppColors.bearish),
              const SizedBox(height: 12),
              Text(_errorMessage ?? 'No candle data available.',
                  style: const TextStyle(color: Colors.white70)),
              const SizedBox(height: 16),
              ElevatedButton.icon(
                onPressed: _fetchChartData,
                icon: const Icon(Icons.refresh),
                label: const Text('Retry Connection'),
                style: ElevatedButton.styleFrom(
                    backgroundColor: const Color(0xFF1E2533)),
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

  void _scrollChatToBottom([ScrollController? sc]) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      final s = sc ?? _chatScrollCtrl;
      if (s.hasClients) {
        s.animateTo(
          s.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
      if (_chatScrollCtrl.hasClients && sc != null && sc != _chatScrollCtrl) {
        _chatScrollCtrl.animateTo(
          _chatScrollCtrl.position.maxScrollExtent,
          duration: const Duration(milliseconds: 300),
          curve: Curves.easeOut,
        );
      }
    });
  }

  // --------------------------------------------------------------------------
  // Apex AI Chat Messaging & Context Injection
  // --------------------------------------------------------------------------
  Future<void> _sendChatMessage([
    String? customText,
    TextEditingController? sourceCtrl,
    ScrollController? scrollCtrl,
    StateSetter? modalState,
  ]) async {
    final ctrl = sourceCtrl ?? _chatInputCtrl;
    final text = (customText ?? ctrl.text).trim();
    if (text.isEmpty || _isChatLoading) return;

    if (customText == null) ctrl.clear();

    setState(() {
      _chatMessages.add({'role': 'user', 'content': text});
      _chatMessages.add({
        'role': 'assistant',
        'content': 'Apex กำลังวิเคราะห์โครงสร้าง SMC ของ $_selectedSymbol...'
      });
      _isChatLoading = true;
    });
    modalState?.call(() {});
    _scrollChatToBottom(scrollCtrl);

    try {
      final chatHistory = _chatMessages
          .where((m) => !m['content']!.startsWith('Apex กำลังวิเคราะห์'))
          .map((m) => {'role': m['role'], 'content': m['content']})
          .toList();
      final gate = StrategyGateView.fromPayload(_smcOverlayData);

      final resp = await AppApi.dio.post(
        AppApi.url('/api/v1/settings/llm/chat'),
        cancelToken: _requestCancelToken,
        options: Options(
          receiveTimeout: const Duration(seconds: 120),
          sendTimeout: const Duration(seconds: 120),
        ),
        data: {
          'messages': chatHistory,
          'context': {
            'symbol': _selectedSymbol,
            'market_type': _selectedMarket,
            'exchange': _selectedExchange,
            'timeframe': _selectedTimeframe,
            'price': _lastPrice,
            'currency': _currSym == '฿' ? 'THB (บาท)' : 'USD (\$)',
            'bias': _smcOverlayData?['bias'] ?? 'neutral',
            'confluence': _smcOverlayData?['confluence'] ?? 0,
            'open_positions': _openPositions.length,
            'strategy_approved': gate.approved,
            'strategy_direction': gate.action,
            'setup_direction': gate.setupDirection,
            'rejection_reasons': gate.rejectionReasons,
          }
        },
      );

      final reply = resp.data['response'] as String? ??
          'ขออภัยครับ เกิดข้อผิดพลาดในการประมวลผล';

      setState(() {
        _chatMessages.removeLast();
        _chatMessages.add({'role': 'assistant', 'content': reply});
        _isChatLoading = false;
        _syncBlueprintFromAI(reply);
      });
      modalState?.call(() {});
      _scrollChatToBottom(scrollCtrl);
    } catch (e) {
      setState(() {
        _chatMessages.removeLast();
        _chatMessages.add({
          'role': 'assistant',
          'content':
              '⚠️ ไม่สามารถเชื่อมต่อกับ AI Advisor ได้: $e\nกรุณาตรวจสอบการตั้งค่า Provider ในหน้า Settings',
        });
        _isChatLoading = false;
      });
      modalState?.call(() {});
      _scrollChatToBottom(scrollCtrl);
    }
  }

  // --------------------------------------------------------------------------
  // Fullscreen Apex AI Chat Assistant Dialog
  // --------------------------------------------------------------------------
  Future<void> _openApexChatDialog() async {
    final modalTextCtrl = TextEditingController();
    final modalScrollCtrl = ScrollController();

    try {
      await showDialog(
        context: context,
        barrierColor: Colors.black.withValues(alpha: 0.7),
        builder: (ctx) {
          return StatefulBuilder(
            builder: (dialogCtx, setModalState) {
              return Dialog(
                backgroundColor: const Color(0xFF141923),
                shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(16),
                  side: const BorderSide(color: AppColors.border, width: 1),
                ),
                insetPadding:
                    const EdgeInsets.symmetric(horizontal: 24, vertical: 24),
                child: ConstrainedBox(
                  constraints:
                      const BoxConstraints(maxWidth: 800, maxHeight: 720),
                  child: Column(
                    children: [
                      // Header
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 20, vertical: 14),
                        decoration: const BoxDecoration(
                          color: Color(0xFF1B2333),
                          borderRadius:
                              BorderRadius.vertical(top: Radius.circular(16)),
                        ),
                        child: Row(
                          children: [
                            Container(
                              padding: const EdgeInsets.all(6),
                              decoration: BoxDecoration(
                                color:
                                    AppColors.bullish.withValues(alpha: 0.15),
                                borderRadius: BorderRadius.circular(8),
                              ),
                              child: const Icon(Icons.smart_toy,
                                  color: AppColors.bullish, size: 20),
                            ),
                            const SizedBox(width: 12),
                            Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                const Text(
                                  'Apex AI Co-Pilot Advisor',
                                  style: TextStyle(
                                      color: Colors.white,
                                      fontWeight: FontWeight.bold,
                                      fontSize: 16),
                                ),
                                Text(
                                  'Live Context: $_selectedSymbol • \$${_lastPrice.toStringAsFixed(2)} • ${_selectedTimeframe.toUpperCase()}',
                                  style: const TextStyle(
                                      color: AppColors.bullish, fontSize: 11),
                                ),
                              ],
                            ),
                            const Spacer(),
                            IconButton(
                              icon: const Icon(Icons.close,
                                  color: Colors.white54),
                              onPressed: () => Navigator.pop(ctx),
                            ),
                          ],
                        ),
                      ),

                      // Prompt quick chips
                      Container(
                        color: const Color(0xFF10141D),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 16, vertical: 8),
                        child: SingleChildScrollView(
                          scrollDirection: Axis.horizontal,
                          child: Row(
                            children: [
                              _modalQuickChip('📊 สรุปโครงสร้าง SMC', () {
                                _sendChatMessage(
                                    'ช่วยสรุปโครงสร้าง SMC และ Invalidation ของ $_selectedSymbol ให้หน่อย',
                                    modalTextCtrl,
                                    modalScrollCtrl,
                                    setModalState);
                              }),
                              const SizedBox(width: 8),
                              _modalQuickChip('🎯 แนวต้าน & Take Profit', () {
                                _sendChatMessage(
                                    '$_selectedSymbol มีแนวต้านสำคัญหรือเป้า TP ตรงไหนบ้าง',
                                    modalTextCtrl,
                                    modalScrollCtrl,
                                    setModalState);
                              }),
                              const SizedBox(width: 8),
                              _modalQuickChip('🛑 จุด Stop Loss ที่ปลอดภัย',
                                  () {
                                _sendChatMessage(
                                    'ถ้าจะเปิดไม้ $_selectedSymbol ตอนนี้ ควรวาง Stop Loss ที่จุดไหนตามโครงสร้าง',
                                    modalTextCtrl,
                                    modalScrollCtrl,
                                    setModalState);
                              }),
                              const SizedBox(width: 8),
                              _modalQuickChip('⚖️ ประเมิน R:R & Risk', () {
                                _sendChatMessage(
                                    'ช่วยประเมินความคุ้มค่า Risk/Reward และความเสี่ยงของ $_selectedSymbol ในจังหวะนี้',
                                    modalTextCtrl,
                                    modalScrollCtrl,
                                    setModalState);
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
                              alignment: isUser
                                  ? Alignment.centerRight
                                  : Alignment.centerLeft,
                              child: Container(
                                margin: const EdgeInsets.only(bottom: 12),
                                padding: const EdgeInsets.symmetric(
                                    horizontal: 14, vertical: 10),
                                constraints:
                                    const BoxConstraints(maxWidth: 650),
                                decoration: BoxDecoration(
                                  color: isUser
                                      ? const Color(0xFF2E82FE)
                                          .withValues(alpha: 0.2)
                                      : const Color(0xFF1E2533),
                                  borderRadius: BorderRadius.circular(10),
                                  border: isUser
                                      ? Border.all(
                                          color: const Color(0xFF2E82FE)
                                              .withValues(alpha: 0.5))
                                      : Border.all(color: AppColors.border),
                                ),
                                child: Text(
                                  msg['content'] ?? '',
                                  style: TextStyle(
                                    color: isUser
                                        ? Colors.white
                                        : const Color(0xFFE2E8F0),
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
                          borderRadius: BorderRadius.vertical(
                              bottom: Radius.circular(16)),
                        ),
                        child: Row(
                          children: [
                            Expanded(
                              child: TextField(
                                controller: modalTextCtrl,
                                style: const TextStyle(
                                    color: Colors.white, fontSize: 14),
                                decoration: InputDecoration(
                                  hintText:
                                      'ถาม Apex เกี่ยวกับ $_selectedSymbol ณ ราคา $_currSym${_fmtPrice(_lastPrice)}...',
                                  hintStyle:
                                      const TextStyle(color: Colors.white38),
                                  border: InputBorder.none,
                                  contentPadding: const EdgeInsets.symmetric(
                                      horizontal: 14, vertical: 10),
                                ),
                                onSubmitted: (_) {
                                  _sendChatMessage(null, modalTextCtrl,
                                      modalScrollCtrl, setModalState);
                                },
                              ),
                            ),
                            IconButton(
                              icon: _isChatLoading
                                  ? const SizedBox(
                                      width: 18,
                                      height: 18,
                                      child: CircularProgressIndicator(
                                          strokeWidth: 2,
                                          color: AppColors.bullish))
                                  : const Icon(Icons.send,
                                      color: AppColors.bullish),
                              onPressed: () {
                                _sendChatMessage(null, modalTextCtrl,
                                    modalScrollCtrl, setModalState);
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
          );
        },
      );
    } finally {
      modalTextCtrl.dispose();
      modalScrollCtrl.dispose();
    }
  }

  Widget _modalQuickChip(String title, VoidCallback onTap) {
    return GestureDetector(
      onTap: onTap,
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          color: const Color(0xFF1E2533),
          borderRadius: BorderRadius.circular(6),
          border:
              Border.all(color: const Color(0xFF2E82FE).withValues(alpha: 0.4)),
        ),
        child: Text(title,
            style: const TextStyle(
                fontSize: 11,
                color: Color(0xFF93C5FD),
                fontWeight: FontWeight.w500)),
      ),
    );
  }

  // --------------------------------------------------------------------------
  // Unified Bottom Dock: Apex AI Chat + Analysis Blueprint + Open Positions
  // --------------------------------------------------------------------------
  Widget _buildBottomDock() {
    final isLandscape =
        MediaQuery.of(context).orientation == Orientation.landscape;

    return Container(
      color: AppColors.surface,
      child: Column(
        children: [
          // Dock Tab Bar Header
          Container(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
            color: const Color(0xFF121620),
            child: Row(
              children: [
                // Tab 1: Apex AI Chat
                Expanded(
                  child: _dockTabButton(
                    icon: Icons.chat_bubble_outline,
                    title: isLandscape ? '💬 APEX AI CHAT' : 'AI Chat',
                    isSelected: _bottomDockTab == 0,
                    badgeColor: AppColors.bullish,
                    onTap: () => setState(() => _bottomDockTab = 0),
                  ),
                ),
                const SizedBox(width: 5),
                // Tab 2: AI Blueprint
                Expanded(
                  child: _dockTabButton(
                    icon: Icons.analytics_outlined,
                    title: isLandscape ? '📊 AI BLUEPRINT' : 'AI Blueprint',
                    isSelected: _bottomDockTab == 1,
                    badgeColor: const Color(0xFF2E82FE),
                    onTap: () => setState(() => _bottomDockTab = 1),
                  ),
                ),
                const SizedBox(width: 5),
                // Tab 3: Positions
                Expanded(
                  child: _dockTabButton(
                    icon: Icons.push_pin_outlined,
                    title: isLandscape
                        ? '📌 POSITIONS (${_openPositions.length})'
                        : 'Positions (${_openPositions.length})',
                    isSelected: _bottomDockTab == 2,
                    badgeColor: const Color(0xFF00E5FF),
                    onTap: () => setState(() => _bottomDockTab = 2),
                  ),
                ),
                const SizedBox(width: 6),
                // Action: Pop-up Full Dialog
                InkWell(
                  onTap: _openApexChatDialog,
                  borderRadius: BorderRadius.circular(6),
                  child: Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 7, vertical: 6),
                    decoration: BoxDecoration(
                      color: const Color(0xFF2E82FE).withValues(alpha: 0.18),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(
                          color: const Color(0xFF2E82FE).withValues(alpha: 0.4),
                          width: 0.8),
                    ),
                    child: const Icon(Icons.open_in_full,
                        size: 13, color: Color(0xFF64B5F6)),
                  ),
                ),
                const SizedBox(width: 4),
                // Action: Refresh
                InkWell(
                  onTap: () {
                    _fetchOpenPositions();
                    _fetchChartData();
                  },
                  borderRadius: BorderRadius.circular(6),
                  child: Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 7, vertical: 6),
                    decoration: BoxDecoration(
                      color: const Color(0xFF252540),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: Colors.white12, width: 0.8),
                    ),
                    child: const Icon(Icons.refresh,
                        size: 13, color: Colors.white70),
                  ),
                ),
              ],
            ),
          ),
          const Divider(height: 1, color: Color(0xFF222B3D)),
          // Dock Content
          Expanded(
            child: _bottomDockTab == 0
                ? _buildApexChatDock()
                : _bottomDockTab == 1
                    ? _buildApexAIPanel()
                    : _buildPositionsTable(),
          ),
        ],
      ),
    );
  }

  Widget _dockTabButton({
    required IconData icon,
    required String title,
    required bool isSelected,
    required Color badgeColor,
    required VoidCallback onTap,
  }) {
    return InkWell(
      onTap: onTap,
      borderRadius: BorderRadius.circular(6),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 6),
        decoration: BoxDecoration(
          color: isSelected
              ? badgeColor.withValues(alpha: 0.18)
              : const Color(0xFF181D29),
          borderRadius: BorderRadius.circular(6),
          border: Border.all(
              color: isSelected ? badgeColor : const Color(0xFF2E384D),
              width: 1),
        ),
        child: Center(
          child: FittedBox(
            fit: BoxFit.scaleDown,
            child: Row(
              mainAxisSize: MainAxisSize.min,
              mainAxisAlignment: MainAxisAlignment.center,
              children: [
                Icon(icon,
                    size: 13, color: isSelected ? badgeColor : Colors.white60),
                const SizedBox(width: 4),
                Text(
                  title,
                  style: TextStyle(
                    fontSize: 11,
                    fontWeight: isSelected ? FontWeight.bold : FontWeight.w600,
                    color: isSelected ? Colors.white : Colors.white70,
                  ),
                ),
              ],
            ),
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
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 5),
          child: SingleChildScrollView(
            scrollDirection: Axis.horizontal,
            child: Row(
              children: [
                _dockQuickPrompt('📊 สรุป SMC $_selectedSymbol',
                    'ช่วยสรุปโครงสร้าง SMC และ Invalidation ของ $_selectedSymbol ให้หน่อย'),
                const SizedBox(width: 6),
                _dockQuickPrompt('🎯 เป้า Take Profit',
                    '$_selectedSymbol มีเป้าทำกำไร TP ตามโครงสร้างตรงไหนบ้าง'),
                const SizedBox(width: 6),
                _dockQuickPrompt('🛑 วาง Stop Loss',
                    'จุด Stop Loss ที่ปลอดภัยตามโครงสร้าง SMC ของ $_selectedSymbol อยู่ตรงไหน'),
                const SizedBox(width: 6),
                _dockQuickPrompt('⚖️ ประเมิน R:R',
                    'ช่วยประเมินความคุ้มค่า Risk/Reward ของ $_selectedSymbol'),
              ],
            ),
          ),
        ),

        // Scrollable Chat Messages (Spacious bubble for readable text)
        Expanded(
          child: ListView.builder(
            controller: _chatScrollCtrl,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            itemCount: _chatMessages.length,
            itemBuilder: (ctx, i) {
              final msg = _chatMessages[i];
              final isUser = msg['role'] == 'user';
              return Align(
                alignment:
                    isUser ? Alignment.centerRight : Alignment.centerLeft,
                child: Container(
                  margin: const EdgeInsets.only(bottom: 8),
                  padding:
                      const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  constraints: BoxConstraints(
                      maxWidth: MediaQuery.of(context).size.width * 0.85),
                  decoration: BoxDecoration(
                    color: isUser
                        ? const Color(0xFF2E82FE).withValues(alpha: 0.25)
                        : const Color(0xFF1E2533),
                    borderRadius: BorderRadius.circular(10),
                    border: isUser
                        ? Border.all(
                            color:
                                const Color(0xFF2E82FE).withValues(alpha: 0.5))
                        : Border.all(color: AppColors.border),
                  ),
                  child: Text(
                    msg['content'] ?? '',
                    style: TextStyle(
                      color: isUser ? Colors.white : const Color(0xFFE2E8F0),
                      fontSize: 13,
                      height: 1.45,
                    ),
                  ),
                ),
              );
            },
          ),
        ),

        // Bottom Chat Input Bar
        Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          color: const Color(0xFF121620),
          child: Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _chatInputCtrl,
                  style: const TextStyle(color: Colors.white, fontSize: 13),
                  decoration: InputDecoration(
                    hintText:
                        'ถาม Apex AI เกี่ยวกับ $_selectedSymbol ($_currSym${_fmtPrice(_lastPrice)})...',
                    hintStyle:
                        const TextStyle(color: Colors.white38, fontSize: 13),
                    border: InputBorder.none,
                    isDense: true,
                    contentPadding:
                        const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                  ),
                  onSubmitted: (_) => _sendChatMessage(),
                ),
              ),
              IconButton(
                icon: _isChatLoading
                    ? const SizedBox(
                        width: 16,
                        height: 16,
                        child: CircularProgressIndicator(
                            strokeWidth: 2, color: AppColors.bullish))
                    : const Icon(Icons.send,
                        size: 18, color: AppColors.bullish),
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
          border:
              Border.all(color: const Color(0xFF2E82FE).withValues(alpha: 0.3)),
        ),
        child: Text(label,
            style: const TextStyle(fontSize: 10, color: Color(0xFF93C5FD))),
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
            Text('No open positions.',
                style: TextStyle(fontSize: 12, color: AppColors.textMuted)),
            SizedBox(height: 2),
            Text(
                'Click [BUY / LONG] or [SELL / SHORT] on the right to open a paper trade.',
                style: TextStyle(fontSize: 11, color: Colors.white38)),
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
        final protectionStage =
            (pos['protection_stage']?.toString() ?? 'initial').toLowerCase();
        final trailStage = protectionStage.startsWith('trailing');
        final protectionLabel = switch (protectionStage) {
          'breakeven' => '🛡️ BE',
          'trailing_1_5r' => '🚀 +0.6R',
          'trailing_2_0r' => '🚀 +1.2R',
          'trailing_dynamic' => '💎 Dynamic',
          _ => '🚀 Trail Armed',
        };

        final entry = (pos['entry'] as num?)?.toDouble() ?? 0.0;
        final size = (pos['size'] as num?)?.toDouble() ??
            (pos['position_size'] as num?)?.toDouble() ??
            0.0;
        final isThb = sym.toUpperCase().contains('THB');
        final curr = isThb ? '฿' : '\$';

        // Correct symbol-specific live Mark Price (not the currently viewed chart symbol)
        final markPrice = _getMarkPriceForSymbol(sym);
        final hasLivePnl = markPrice != null && entry > 0 && size > 0;
        final pnlPct = hasLivePnl
            ? (isLong
                ? ((markPrice - entry) / entry) * 100
                : ((entry - markPrice) / entry) * 100)
            : null;
        final pnlVal = hasLivePnl
            ? (isLong ? (markPrice - entry) * size : (entry - markPrice) * size)
            : null;
        final isProfit = pnlVal != null && pnlVal >= 0;

        return SingleChildScrollView(
          scrollDirection: Axis.horizontal,
          child: ConstrainedBox(
            constraints: BoxConstraints(
                minWidth:
                    math.max(340.0, MediaQuery.of(context).size.width - 32)),
            child: Row(
              children: [
                // Side Badge
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                  decoration: BoxDecoration(
                    color: color.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(4),
                    border: Border.all(color: color),
                  ),
                  child: Text(
                    dir.toUpperCase(),
                    style: TextStyle(
                        color: color,
                        fontSize: 10,
                        fontWeight: FontWeight.bold),
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
                          style: const TextStyle(
                              fontWeight: FontWeight.bold,
                              fontSize: 13,
                              color: Color(0xFF93C5FD),
                              decoration: TextDecoration.underline),
                        ),
                        const SizedBox(width: 3),
                        const Icon(Icons.arrow_outward,
                            size: 11, color: Color(0xFF93C5FD)),
                      ],
                    ),
                  ),
                ),
                if (protectionStage != 'initial') ...[
                  const SizedBox(width: 6),
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                    decoration: BoxDecoration(
                      color: (trailStage
                              ? AppColors.bullish
                              : const Color(0xFF00E5FF))
                          .withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(
                          color: trailStage
                              ? AppColors.bullish
                              : const Color(0xFF00E5FF),
                          width: 0.6),
                    ),
                    child: Text(protectionLabel,
                        style: TextStyle(
                            color: trailStage
                                ? AppColors.bullish
                                : const Color(0xFF00E5FF),
                            fontSize: 8.5,
                            fontWeight: FontWeight.bold)),
                  ),
                ] else if (pos['trailing_stop'] != false) ...[
                  const SizedBox(width: 6),
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                    decoration: BoxDecoration(
                      color: AppColors.bullish.withValues(alpha: 0.15),
                      borderRadius: BorderRadius.circular(4),
                      border: Border.all(color: AppColors.bullish, width: 0.6),
                    ),
                    child: const Text('🚀 Trail Armed',
                        style: TextStyle(
                            color: AppColors.bullish,
                            fontSize: 8.5,
                            fontWeight: FontWeight.bold)),
                  ),
                ],
                const SizedBox(width: 14),
                // Size
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Size',
                        style:
                            TextStyle(fontSize: 9, color: AppColors.textMuted)),
                    Text('$size',
                        style: const TextStyle(
                            fontSize: 12, fontWeight: FontWeight.w600)),
                  ],
                ),
                const SizedBox(width: 16),
                // Entry
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Entry Price',
                        style:
                            TextStyle(fontSize: 9, color: AppColors.textMuted)),
                    Text('$curr${_fmtPrice(entry)}',
                        style: const TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            fontFamily: 'monospace')),
                  ],
                ),
                const SizedBox(width: 16),
                // Mark Price
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text('Mark Price',
                        style:
                            TextStyle(fontSize: 9, color: AppColors.textMuted)),
                    Text(
                        markPrice != null
                            ? '$curr${_fmtPrice(markPrice)}'
                            : 'Unavailable',
                        style: const TextStyle(
                            fontSize: 12,
                            fontWeight: FontWeight.w600,
                            fontFamily: 'monospace')),
                  ],
                ),
                const SizedBox(width: 24),
                // PnL
                Column(
                  crossAxisAlignment: CrossAxisAlignment.end,
                  children: [
                    Text(
                      pnlVal != null
                          ? '${isProfit ? '+' : '-'}$curr${pnlVal.abs().toStringAsFixed(2)}'
                          : 'Unavailable',
                      style: TextStyle(
                        fontSize: 13,
                        fontWeight: FontWeight.bold,
                        color: isProfit ? AppColors.bullish : AppColors.bearish,
                        fontFamily: 'monospace',
                      ),
                    ),
                    Text(
                      pnlPct != null
                          ? '(${isProfit ? '+' : ''}${pnlPct.toStringAsFixed(2)}%)'
                          : '--',
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
                    padding:
                        const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                    minimumSize: Size.zero,
                    tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    shape: RoundedRectangleBorder(
                        borderRadius: BorderRadius.circular(4)),
                  ),
                  child: const Text('Close ✕',
                      style:
                          TextStyle(fontSize: 11, fontWeight: FontWeight.bold)),
                ),
              ],
            ),
          ),
        );
      },
    );
  }

  // --------------------------------------------------------------------------
  // Right Panel: Apex AI Institutional Advisor & Quick Execution
  // --------------------------------------------------------------------------
  Widget _buildApexAIPanel() {
    final live = _lastPrice > 0 ? _lastPrice : 0.0;
    final gate = StrategyGateView.fromPayload(_smcOverlayData);
    final bp = gate.approved &&
            (_activeAiBlueprint != null &&
                _activeAiBlueprint!['symbol'] == _selectedSymbol &&
                _blueprintMatchesGate(_activeAiBlueprint, gate))
        ? _activeAiBlueprint
        : (gate.approved &&
                _blueprintMatchesGate(_symbolBlueprints[_selectedSymbol], gate)
            ? _symbolBlueprints[_selectedSymbol]
            : null);

    // Strategy Gate is the only execution authority. AI blueprints and raw
    // market bias may explain a setup, but can never grant an order.
    final isBull = gate.allowsLong;
    final isBear = gate.allowsShort;
    final setupIsBull = gate.setupDirection == 'long';
    final setupIsBear = gate.setupDirection == 'short';
    final signalColor = isBull
        ? AppColors.bullish
        : (isBear
            ? AppColors.bearish
            : (setupIsBull
                ? const Color(0xFFFFC857)
                : (setupIsBear ? const Color(0xFFFF9F43) : AppColors.neutral)));

    final confluence = gate.confluence;

    final zoneName = bp?['zone_name'] as String? ??
        (_smcOverlayData?['in_discount'] == true
            ? 'DISCOUNT ZONE'
            : (_smcOverlayData?['in_premium'] == true
                ? 'PREMIUM ZONE'
                : 'EQUILIBRIUM'));
    final htfTrend = bp?['htf_trend'] as String? ??
        (setupIsBull ? 'Bullish' : (setupIsBear ? 'Bearish' : 'Neutral'));

    // 2. Resolve Price Levels from Active Mode
    final aiEntry = (bp?['entry'] as num?)?.toDouble() ??
        (_smcOverlayData?['entry'] as num?)?.toDouble() ??
        0.0;
    final aiSl = (bp?['stop_loss'] as num?)?.toDouble() ??
        (_smcOverlayData?['stop_loss'] as num?)?.toDouble() ??
        0.0;
    final aiTp = (bp?['take_profit'] as num?)?.toDouble() ??
        (_smcOverlayData?['take_profit'] as num?)?.toDouble() ??
        0.0;
    final aiTp2 = (bp?['take_profit_2'] as num?)?.toDouble() ?? 0.0;
    final hasAiSetup = aiEntry > 0 &&
        ((isBull && aiSl < aiEntry && aiTp > aiEntry) ||
            (isBear && aiSl > aiEntry && aiTp < aiEntry));

    // Safeguard: re-populate controllers if empty or holding stale price from another symbol
    final curParsed = double.tryParse(_bpEntryCtrl.text);
    final isStale = curParsed == null ||
        (curParsed > 0 &&
            live > 0 &&
            (curParsed / live > 3.0 || curParsed / live < 0.33));
    if (hasAiSetup &&
        (_bpEntryCtrl.text.isEmpty || isStale || _bpEntryMode == 'ai')) {
      _bpEntryCtrl.text = _fmtPrice(aiEntry);
      _bpSlCtrl.text = _fmtPrice(aiSl);
      _bpTpCtrl.text = _fmtPrice(aiTp);
      _bpTp2Ctrl.text = aiTp2 > 0 ? _fmtPrice(aiTp2) : '';
    }

    double currentEntry = double.tryParse(_bpEntryCtrl.text) ?? 0.0;
    double currentSl = double.tryParse(_bpSlCtrl.text) ?? 0.0;
    double currentTp = double.tryParse(_bpTpCtrl.text) ?? 0.0;

    final isCustom = _bpEntryMode == 'custom';
    final isMarketMode = _bpEntryMode == 'market';
    final isPending = !isMarketMode;
    final diffFromLivePct =
        live > 0 ? ((currentEntry - live) / live * 100) : 0.0;

    final qty = double.tryParse(_qtyCtrl.text.trim()) ?? 0.0;
    final posValue = currentEntry * qty;
    final riskDist = (currentEntry - currentSl).abs();
    final rewardDist = (currentTp - currentEntry).abs();
    final riskAmount = riskDist * qty;
    final rewardAmount = rewardDist * qty;
    final riskPct = currentEntry > 0 ? (riskDist / currentEntry) * 100 : 0.0;
    final gainPct = currentEntry > 0 ? (rewardDist / currentEntry) * 100 : 0.0;
    final calculatedRR = riskDist > 0 ? (rewardDist / riskDist) : 0.0;
    final hasBaseOrderInput = live > 0 &&
        currentEntry > 0 &&
        currentSl > 0 &&
        currentTp > 0 &&
        qty > 0;
    final canExecuteLong = gate.allowsLong &&
        hasBaseOrderInput &&
        currentSl < currentEntry &&
        currentTp > currentEntry;
    final canExecuteShort = gate.allowsShort &&
        hasBaseOrderInput &&
        currentSl > currentEntry &&
        currentTp < currentEntry;

    final aiReasoning = gate.approved
        ? (bp?['reasoning']?.toString() ??
            'ผ่าน Strategy Gate สำหรับ $_selectedSymbol')
        : 'คำตัดสิน: WAIT — ${gate.waitReasonThai}';

    return Container(
      color: AppColors.panel,
      child: SingleChildScrollView(
        physics: const ClampingScrollPhysics(),
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 24),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            // Apex AI Header Badge
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(6),
                  decoration: BoxDecoration(
                    color: const Color(0xFF2E82FE).withValues(alpha: 0.2),
                    borderRadius: BorderRadius.circular(6),
                  ),
                  child: const Icon(Icons.psychology,
                      size: 18, color: Color(0xFF2E82FE)),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text(
                        'APEX AI ADVISOR',
                        style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.bold,
                            letterSpacing: 1.1),
                      ),
                      Row(
                        children: [
                          Container(
                            width: 5,
                            height: 5,
                            decoration: BoxDecoration(
                              color: gate.approved
                                  ? AppColors.bullish
                                  : const Color(0xFFFFC857),
                              shape: BoxShape.circle,
                            ),
                          ),
                          const SizedBox(width: 4),
                          Text(
                            gate.approved
                                ? '● Strategy Gate อนุมัติ ($_selectedSymbol)'
                                : '● Strategy Gate: WAIT ($_selectedSymbol)',
                            style: TextStyle(
                              fontSize: 9,
                              color: gate.approved
                                  ? AppColors.bullish
                                  : const Color(0xFFFFC857),
                              fontWeight: FontWeight.bold,
                            ),
                          ),
                        ],
                      ),
                    ],
                  ),
                ),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 3.5),
                  decoration: BoxDecoration(
                    color: signalColor.withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: signalColor, width: 1.2),
                  ),
                  child: Text(
                    isBear
                        ? '🔴 SHORT READY'
                        : (isBull
                            ? '🟢 LONG READY'
                            : '⏳ WAIT · ${gate.setupLabel}'),
                    style: TextStyle(
                        color: signalColor,
                        fontWeight: FontWeight.bold,
                        fontSize: 11),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 10),

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
                      const Text('Institutional Confluence',
                          style: TextStyle(
                              fontSize: 12, color: AppColors.textMuted)),
                      Text(
                        '$confluence / 100 ${confluence >= 80 ? '(Grade A+)' : (confluence >= 65 ? '(Grade B)' : '(Grade C)')}',
                        style: TextStyle(
                            fontSize: 13,
                            fontWeight: FontWeight.bold,
                            color: signalColor),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  ClipRRect(
                    borderRadius: BorderRadius.circular(4),
                    child: LinearProgressIndicator(
                      value: (confluence / 100.0).clamp(0.0, 1.0),
                      backgroundColor: const Color(0xFF232A38),
                      valueColor: AlwaysStoppedAnimation<Color>(signalColor),
                      minHeight: 6,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      _checkChip('HTF Trend', htfTrend, true),
                      _checkChip('Market Zone', zoneName, true),
                      _checkChip('Liquidity', 'Swept', true),
                    ],
                  ),
                  if (_mtfMatrixData != null) ...[
                    const Divider(height: 16, color: Color(0xFF222938)),
                    InkWell(
                      onTap: _showMtfModal,
                      child: Row(
                        children: [
                          const Text('4-TF Matrix:',
                              style: TextStyle(
                                  fontSize: 11, color: Colors.white54)),
                          const SizedBox(width: 6),
                          Text(
                            _mtfMatrixData!['grade_badge'] ?? '',
                            style: const TextStyle(
                                fontSize: 11,
                                fontWeight: FontWeight.bold,
                                color: Color(0xFF00E5FF)),
                          ),
                          const Spacer(),
                          const Text('ดูรายละเอียด ›',
                              style: TextStyle(
                                  fontSize: 10.5,
                                  color: Color(0xFF93C5FD),
                                  fontWeight: FontWeight.w600)),
                        ],
                      ),
                    ),
                  ],
                ],
              ),
            ),
            const SizedBox(height: 10),

            // AI Strategy Note / If-Then Scenario
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFF141926),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                    color: const Color(0xFF2E82FE).withValues(alpha: 0.35)),
              ),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Icon(Icons.lightbulb_outline,
                      size: 16, color: Color(0xFF5CA3FF)),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      aiReasoning,
                      style: const TextStyle(
                          fontSize: 11.5,
                          color: Color(0xFFD1D5DB),
                          height: 1.45),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),

            // Execution Blueprint & Order Control Suite
            Row(
              children: [
                const Text(
                  'EXECUTION BLUEPRINT & ORDER SUITE',
                  style: TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.bold,
                      color: AppColors.textMuted,
                      letterSpacing: 0.8),
                ),
                const Spacer(),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 6, vertical: 1.5),
                  decoration: BoxDecoration(
                    color: const Color(0xFF00E5FF).withValues(alpha: 0.15),
                    borderRadius: BorderRadius.circular(4),
                    border:
                        Border.all(color: const Color(0xFF00E5FF), width: 0.6),
                  ),
                  child: Text(
                    'สด $_currSym${_fmtPrice(live)}',
                    style: const TextStyle(
                        fontSize: 9.5,
                        fontWeight: FontWeight.bold,
                        color: Color(0xFF00E5FF),
                        fontFamily: 'monospace'),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 6),

            // 3-Mode Segmented Buttons
            Row(
              children: [
                _buildBpModeButton(
                  mode: 'ai',
                  title: '🎯 แผน AI (Limit)',
                  subtitle: _fmtPrice(aiEntry),
                  selected: _bpEntryMode == 'ai',
                  color: const Color(0xFF2E82FE),
                  onTap: hasAiSetup
                      ? () {
                          setState(() {
                            _bpEntryMode = 'ai';
                            _bpEntryCtrl.text = _fmtPrice(aiEntry);
                            _bpSlCtrl.text = _fmtPrice(aiSl);
                            _bpTpCtrl.text = _fmtPrice(aiTp);
                          });
                        }
                      : null,
                ),
                const SizedBox(width: 6),
                _buildBpModeButton(
                  mode: 'market',
                  title: '⚡ ตลาดสด (Market)',
                  subtitle: live > 0 ? _fmtPrice(live) : 'Unavailable',
                  selected: _bpEntryMode == 'market',
                  color: const Color(0xFF00E5FF),
                  onTap: live > 0
                      ? () {
                          setState(() {
                            _bpEntryMode = 'market';
                            _bpEntryCtrl.text = _fmtPrice(live);
                            final dist = live * 0.008;
                            _bpSlCtrl.text =
                                _fmtPrice(isBull ? live - dist : live + dist);
                            _bpTpCtrl.text = _fmtPrice(
                                isBull ? live + dist * 2.5 : live - dist * 2.5);
                          });
                        }
                      : null,
                ),
                const SizedBox(width: 6),
                _buildBpModeButton(
                  mode: 'custom',
                  title: '✏️ กำหนดเอง',
                  subtitle: 'พิมพ์ราคาอิสระ',
                  selected: _bpEntryMode == 'custom',
                  color: const Color(0xFFFFB300),
                  onTap: () {
                    setState(() {
                      _bpEntryMode = 'custom';
                    });
                  },
                ),
              ],
            ),
            const SizedBox(height: 8),

            // Helper Info Box for Selected Mode
            Container(
              width: double.infinity,
              padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
              decoration: BoxDecoration(
                color: isPending
                    ? const Color(0xFF2A200B)
                    : const Color(0xFF131A26),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(
                  color: isPending
                      ? const Color(0xFFFFB300).withValues(alpha: 0.5)
                      : const Color(0xFF2E82FE).withValues(alpha: 0.4),
                  width: 0.8,
                ),
              ),
              child: Row(
                children: [
                  Icon(
                    isPending ? Icons.hourglass_top : Icons.bolt,
                    size: 13,
                    color: isPending
                        ? const Color(0xFFFFB300)
                        : const Color(0xFF00E5FF),
                  ),
                  const SizedBox(width: 6),
                  Expanded(
                    child: Text(
                      isPending
                          ? 'คำสั่ง Limit Order รอดักราคา (ห่างจากราคาตลาด ${diffFromLivePct > 0 ? "+" : ""}${diffFromLivePct.toStringAsFixed(2)}%)'
                          : 'ส่งคำสั่งเปิด Position ทันทีที่ราคาตลาดสด $_currSym${_fmtPrice(live)}',
                      style: TextStyle(
                        fontSize: 10.5,
                        color: isPending
                            ? const Color(0xFFFFD54F)
                            : const Color(0xFF80DEEA),
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 10),

            // 3-Box Price Inputs (Entry / Stop Loss / Take Profit)
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: AppColors.surface,
                borderRadius: BorderRadius.circular(8),
                border: Border.all(color: AppColors.border),
              ),
              child: Column(
                children: [
                  // Entry Field
                  _buildPriceInputField(
                    label: '💵 ราคาเข้าซื้อ (Entry Price)',
                    controller: _bpEntryCtrl,
                    color: _bpEntryMode == 'ai'
                        ? const Color(0xFF5CA3FF)
                        : Colors.white,
                    isEditable: isCustom,
                    hintText: _fmtPrice(aiEntry),
                    helperText: _bpEntryMode == 'ai'
                        ? '🎯 ราคาแนะนำตามแผน AI Blueprint'
                        : (_bpEntryMode == 'market'
                            ? '⚡ ราคาตลาดสด ณ ปัจจุบัน'
                            : '✏️ ราคา Limit ที่คุณต้องการดัก'),
                    onChanged: (val) {
                      final p = double.tryParse(val);
                      if (p != null && p > 0) {
                        final dist = (p - aiSl).abs();
                        final newSl = isBull ? p - dist : p + dist;
                        final newTp = isBull ? p + dist * 2.5 : p - dist * 2.5;
                        _bpSlCtrl.text = _fmtPrice(newSl);
                        _bpTpCtrl.text = _fmtPrice(newTp);
                      }
                      setState(() {});
                    },
                  ),
                  const Divider(height: 16, color: Color(0xFF222B3D)),

                  // Stop Loss Field
                  _buildPriceInputField(
                    label: '🛑 จุดตัดขาดทุน (Stop Loss)',
                    controller: _bpSlCtrl,
                    color: AppColors.bearish,
                    isEditable: isCustom,
                    hintText: _fmtPrice(aiSl),
                    helperText:
                        'ความเสี่ยง: -${riskPct.toStringAsFixed(2)}% (-$_currSym${riskAmount.toStringAsFixed(2)})',
                    onChanged: (_) => setState(() {}),
                  ),
                  const Divider(height: 16, color: Color(0xFF222B3D)),

                  // Take Profit Field
                  _buildPriceInputField(
                    label: '🎯 เป้าหมายทำกำไร (Take Profit)',
                    controller: _bpTpCtrl,
                    color: AppColors.bullish,
                    isEditable: isCustom,
                    hintText: _fmtPrice(aiTp),
                    helperText:
                        'เป้าหมาย: +${gainPct.toStringAsFixed(2)}% (+$_currSym${rewardAmount.toStringAsFixed(2)}) • R:R 1:${calculatedRR.toStringAsFixed(2)}R',
                    onChanged: (_) => setState(() {}),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),

            // Dynamic Risk Sizer & Order Quantity Section
            Row(
              mainAxisAlignment: MainAxisAlignment.spaceBetween,
              children: [
                const Text('DYNAMIC RISK SIZER (% PORTFOLIO)',
                    style: TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.bold,
                        color: Color(0xFF00E5FF))),
                Text(
                  'พอร์ต: $_currSym${_accountCapital >= 1000 ? _accountCapital.toStringAsFixed(0) : _accountCapital.toStringAsFixed(2)}',
                  style: const TextStyle(
                      fontSize: 10,
                      color: Colors.white54,
                      fontWeight: FontWeight.bold),
                ),
              ],
            ),
            const SizedBox(height: 6),
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
                  // Risk % Selector Chips
                  Row(
                    children: [0.5, 1.0, 2.0, 3.0].map((rPct) {
                      final isSel = _selectedRiskPct == rPct;
                      final riskDollar = _accountCapital * (rPct / 100.0);
                      return Expanded(
                        child: Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 2),
                          child: InkWell(
                            onTap: () {
                              setState(() {
                                _selectedRiskPct = rPct;
                                _recalcRiskSize(rPct);
                              });
                            },
                            borderRadius: BorderRadius.circular(6),
                            child: Container(
                              padding: const EdgeInsets.symmetric(vertical: 6),
                              decoration: BoxDecoration(
                                color: isSel
                                    ? const Color(0xFF00E5FF)
                                        .withValues(alpha: 0.2)
                                    : const Color(0xFF1B202E),
                                borderRadius: BorderRadius.circular(6),
                                border: Border.all(
                                  color: isSel
                                      ? const Color(0xFF00E5FF)
                                      : const Color(0xFF2C3549),
                                  width: isSel ? 1.5 : 1,
                                ),
                              ),
                              child: Column(
                                mainAxisSize: MainAxisSize.min,
                                children: [
                                  Text(
                                    '$rPct%',
                                    style: TextStyle(
                                      fontSize: 12,
                                      fontWeight: FontWeight.bold,
                                      color: isSel
                                          ? const Color(0xFF00E5FF)
                                          : Colors.white70,
                                    ),
                                  ),
                                  Text(
                                    '$_currSym${riskDollar >= 1000 ? riskDollar.toStringAsFixed(0) : riskDollar.toStringAsFixed(1)}',
                                    style: TextStyle(
                                      fontSize: 9,
                                      color:
                                          isSel ? Colors.white : Colors.white38,
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 10),

                  // Quantity Controls (Minus / Input / Plus)
                  Row(
                    children: [
                      // Minus button
                      IconButton.filledTonal(
                        icon: const Icon(Icons.remove, size: 16),
                        style: IconButton.styleFrom(
                          backgroundColor: const Color(0xFF252540),
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.all(8),
                          minimumSize: const Size(36, 36),
                        ),
                        onPressed: () {
                          double cur = double.tryParse(_qtyCtrl.text) ?? 0.1;
                          double step = _selectedMarket == 'stock'
                              ? 1.0
                              : (_selectedMarket == 'forex' ? 0.01 : 0.05);
                          double next = (cur - step).clamp(step, 1000.0);
                          setState(() {
                            _qtyCtrl.text = _selectedMarket == 'stock'
                                ? next.toInt().toString()
                                : next.toStringAsFixed(2);
                          });
                        },
                      ),
                      const SizedBox(width: 8),
                      // Editable TextField
                      Expanded(
                        child: Container(
                          height: 40,
                          decoration: BoxDecoration(
                            color: const Color(0xFF131722),
                            borderRadius: BorderRadius.circular(6),
                            border: Border.all(color: const Color(0xFF2E384D)),
                          ),
                          child: TextField(
                            controller: _qtyCtrl,
                            keyboardType: const TextInputType.numberWithOptions(
                                decimal: true),
                            textAlign: TextAlign.center,
                            style: const TextStyle(
                                fontSize: 15,
                                fontWeight: FontWeight.bold,
                                color: Colors.white),
                            decoration: InputDecoration(
                              contentPadding: const EdgeInsets.symmetric(
                                  horizontal: 8, vertical: 8),
                              border: InputBorder.none,
                              suffixText: _selectedMarket == 'stock'
                                  ? 'Shares'
                                  : (_selectedMarket == 'forex'
                                      ? 'Lots'
                                      : _selectedSymbol.split('/').first),
                              suffixStyle: const TextStyle(
                                  fontSize: 11,
                                  color: Colors.white54,
                                  fontWeight: FontWeight.bold),
                            ),
                            onChanged: (_) => setState(() {}),
                          ),
                        ),
                      ),
                      const SizedBox(width: 8),
                      // Plus button
                      IconButton.filledTonal(
                        icon: const Icon(Icons.add, size: 16),
                        style: IconButton.styleFrom(
                          backgroundColor: const Color(0xFF252540),
                          foregroundColor: Colors.white,
                          padding: const EdgeInsets.all(8),
                          minimumSize: const Size(36, 36),
                        ),
                        onPressed: () {
                          double cur = double.tryParse(_qtyCtrl.text) ?? 0.1;
                          double step = _selectedMarket == 'stock'
                              ? 1.0
                              : (_selectedMarket == 'forex' ? 0.01 : 0.05);
                          double next = cur + step;
                          setState(() {
                            _qtyCtrl.text = _selectedMarket == 'stock'
                                ? next.toInt().toString()
                                : next.toStringAsFixed(2);
                          });
                        },
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),

                  // Quick Preset Chips
                  Wrap(
                    spacing: 6,
                    runSpacing: 6,
                    children: (_selectedMarket == 'stock'
                            ? ['1', '5', '10', '25', '50', '100']
                            : (_selectedMarket == 'forex'
                                ? [
                                    '0.01',
                                    '0.05',
                                    '0.10',
                                    '0.25',
                                    '0.50',
                                    '1.00'
                                  ]
                                : [
                                    '0.05',
                                    '0.10',
                                    '0.25',
                                    '0.50',
                                    '1.00',
                                    '10.0',
                                    '100.0'
                                  ]))
                        .map((preset) {
                      final isSelected = _qtyCtrl.text.trim() == preset;
                      return InkWell(
                        onTap: () => setState(() => _qtyCtrl.text = preset),
                        borderRadius: BorderRadius.circular(4),
                        child: Container(
                          padding: const EdgeInsets.symmetric(
                              horizontal: 10, vertical: 4),
                          decoration: BoxDecoration(
                            color: isSelected
                                ? const Color(0xFF2E82FE)
                                    .withValues(alpha: 0.25)
                                : const Color(0xFF1F2433),
                            borderRadius: BorderRadius.circular(4),
                            border: Border.all(
                              color: isSelected
                                  ? const Color(0xFF2E82FE)
                                  : const Color(0xFF323B4F),
                              width: 1,
                            ),
                          ),
                          child: Text(
                            preset,
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: isSelected
                                  ? FontWeight.bold
                                  : FontWeight.normal,
                              color: isSelected ? Colors.white : Colors.white70,
                            ),
                          ),
                        ),
                      );
                    }).toList(),
                  ),
                  const SizedBox(height: 10),

                  // Live Estimated Value & Risk
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
                    decoration: BoxDecoration(
                      color: const Color(0xFF10141D),
                      borderRadius: BorderRadius.circular(6),
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.spaceBetween,
                      children: [
                        Text(
                            'มูลค่าไม้: $_currSym${posValue >= 1000 ? posValue.toStringAsFixed(1) : posValue.toStringAsFixed(2)}',
                            style: const TextStyle(
                                fontSize: 11, color: Colors.white70)),
                        Text(
                            'เสี่ยง: -$_currSym${riskAmount.toStringAsFixed(2)}',
                            style: const TextStyle(
                                fontSize: 11,
                                color: AppColors.bearish,
                                fontWeight: FontWeight.bold)),
                        Text(
                            'เป้า TP: +$_currSym${rewardAmount.toStringAsFixed(2)}',
                            style: const TextStyle(
                                fontSize: 11,
                                color: AppColors.bullish,
                                fontWeight: FontWeight.bold)),
                      ],
                    ),
                  ),
                  const SizedBox(height: 10),

                  // Institutional Trade Automation Switches
                  Container(
                    padding: const EdgeInsets.all(8),
                    decoration: BoxDecoration(
                      color: const Color(0xFF0F121A),
                      borderRadius: BorderRadius.circular(6),
                      border: Border.all(color: const Color(0xFF1E2638)),
                    ),
                    child: Column(
                      children: [
                        // Auto-BE Switch
                        InkWell(
                          onTap: () =>
                              setState(() => _autoBeEnabled = !_autoBeEnabled),
                          child: Row(
                            children: [
                              Icon(Icons.shield,
                                  size: 16,
                                  color: _autoBeEnabled
                                      ? const Color(0xFF00E5FF)
                                      : Colors.white30),
                              const SizedBox(width: 8),
                              const Expanded(
                                child: Text(
                                    '🛡️ Auto-Breakeven Shield (เลื่อน SL บังทุนที่ +1.0R)',
                                    style: TextStyle(
                                        fontSize: 11, color: Colors.white)),
                              ),
                              Switch(
                                value: _autoBeEnabled,
                                activeThumbColor: const Color(0xFF00E5FF),
                                onChanged: (v) =>
                                    setState(() => _autoBeEnabled = v),
                                materialTapTargetSize:
                                    MaterialTapTargetSize.shrinkWrap,
                              ),
                            ],
                          ),
                        ),
                        const Divider(height: 8, color: Color(0xFF1E2638)),
                        // Dynamic Trailing Switch
                        InkWell(
                          onTap: () => setState(() =>
                              _trailingStopEnabled = !_trailingStopEnabled),
                          child: Row(
                            children: [
                              Icon(Icons.auto_awesome,
                                  size: 16,
                                  color: _trailingStopEnabled
                                      ? AppColors.bullish
                                      : Colors.white30),
                              const SizedBox(width: 8),
                              const Expanded(
                                child: Text(
                                    '🚀 Multi-Tier Trailing Stop (ล็อกกำไรที่ +1.5R, +2.0R, +2.5R+)',
                                    style: TextStyle(
                                        fontSize: 11, color: Colors.white)),
                              ),
                              Switch(
                                value: _trailingStopEnabled,
                                activeThumbColor: AppColors.bullish,
                                onChanged: (v) =>
                                    setState(() => _trailingStopEnabled = v),
                                materialTapTargetSize:
                                    MaterialTapTargetSize.shrinkWrap,
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
            const SizedBox(height: 14),

            // Execution Action Buttons (Direct Order Execution)
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: canExecuteLong
                        ? () {
                            _executeOrder(
                              'long',
                              customEntry: currentEntry,
                              customSl: currentSl,
                              customTp: currentTp,
                            );
                          }
                        : null,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.bullish,
                      foregroundColor: Colors.black,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                      elevation: 2,
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                            isPending
                                ? Icons.hourglass_top
                                : Icons.arrow_upward,
                            size: 16,
                            color: Colors.black),
                        const SizedBox(width: 4),
                        Text(
                          isPending
                              ? 'BUY LIMIT ($_currSym${_fmtPrice(currentEntry)})'
                              : 'BUY / LONG (${_qtyCtrl.text.trim()})',
                          style: const TextStyle(
                              fontWeight: FontWeight.bold, fontSize: 13),
                        ),
                      ],
                    ),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: ElevatedButton(
                    onPressed: canExecuteShort
                        ? () {
                            _executeOrder(
                              'short',
                              customEntry: currentEntry,
                              customSl: currentSl,
                              customTp: currentTp,
                            );
                          }
                        : null,
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.bearish,
                      foregroundColor: Colors.white,
                      padding: const EdgeInsets.symmetric(vertical: 14),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                      elevation: 2,
                    ),
                    child: Row(
                      mainAxisAlignment: MainAxisAlignment.center,
                      children: [
                        Icon(
                            isPending
                                ? Icons.hourglass_bottom
                                : Icons.arrow_downward,
                            size: 16,
                            color: Colors.white),
                        const SizedBox(width: 4),
                        Text(
                          isPending
                              ? 'SELL LIMIT ($_currSym${_fmtPrice(currentEntry)})'
                              : 'SELL / SHORT (${_qtyCtrl.text.trim()})',
                          style: const TextStyle(
                              fontWeight: FontWeight.bold, fontSize: 13),
                        ),
                      ],
                    ),
                  ),
                ),
              ],
            ),
            if (isPending) ...[
              const SizedBox(height: 6),
              const Center(
                child: Text(
                  '* คำสั่งจะถูกนำไปตั้งในแท็บ ⏳ Pending Orders เพื่อรอดักราคาอัตโนมัติ',
                  style: TextStyle(fontSize: 10, color: Color(0xFFFFD54F)),
                ),
              ),
            ],
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
                      Icon(Icons.shield_outlined,
                          size: 14, color: Color(0xFF00E5FF)),
                      SizedBox(width: 6),
                      Text('INSTITUTIONAL RISK CONTROL',
                          style: TextStyle(
                              fontSize: 10,
                              fontWeight: FontWeight.bold,
                              color: Color(0xFF00E5FF))),
                    ],
                  ),
                  const SizedBox(height: 8),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text('Selected Risk / Trade:',
                          style:
                              TextStyle(fontSize: 11, color: Colors.white54)),
                      Text(
                          '${_selectedRiskPct.toStringAsFixed(1)}% ($_currSym${(_accountCapital * _selectedRiskPct / 100).toStringAsFixed(2)})',
                          style: const TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                              color: Colors.white)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  const Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      Text('Portfolio Limits:',
                          style:
                              TextStyle(fontSize: 11, color: Colors.white54)),
                      Text('Validated by backend',
                          style: TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.bold,
                              color: AppColors.neutral)),
                    ],
                  ),
                  const SizedBox(height: 4),
                  Row(
                    mainAxisAlignment: MainAxisAlignment.spaceBetween,
                    children: [
                      const Text('TP/SL Monitor:',
                          style:
                              TextStyle(fontSize: 11, color: Colors.white54)),
                      Row(
                        children: [
                          Container(
                              width: 6,
                              height: 6,
                              decoration: const BoxDecoration(
                                  color: AppColors.bullish,
                                  shape: BoxShape.circle)),
                          const SizedBox(width: 4),
                          const Text('BACKEND MANAGED',
                              style: TextStyle(
                                  fontSize: 10,
                                  fontWeight: FontWeight.bold,
                                  color: AppColors.bullish)),
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

  Widget _buildBpModeButton({
    required String mode,
    required String title,
    required String subtitle,
    required bool selected,
    required Color color,
    required VoidCallback? onTap,
  }) {
    return Expanded(
      child: InkWell(
        onTap: onTap,
        borderRadius: BorderRadius.circular(8),
        child: AnimatedContainer(
          duration: const Duration(milliseconds: 180),
          padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 4),
          decoration: BoxDecoration(
            color: selected
                ? color.withValues(alpha: 0.22)
                : const Color(0xFF181E2B),
            borderRadius: BorderRadius.circular(8),
            border: Border.all(
              color: selected ? color : const Color(0xFF2E384D),
              width: selected ? 1.6 : 1.0,
            ),
          ),
          child: Column(
            children: [
              Text(
                title,
                style: TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.bold,
                  color: selected ? color : Colors.white70,
                ),
                textAlign: TextAlign.center,
              ),
              const SizedBox(height: 2),
              Text(
                subtitle,
                style: TextStyle(
                  fontSize: 11.5,
                  fontWeight: FontWeight.bold,
                  color: selected ? Colors.white : Colors.white54,
                  fontFamily: 'monospace',
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

  Widget _buildPriceInputField({
    required String label,
    required TextEditingController controller,
    required Color color,
    required bool isEditable,
    required String hintText,
    required String helperText,
    required ValueChanged<String> onChanged,
  }) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(
              label,
              style: TextStyle(
                  fontSize: 11, fontWeight: FontWeight.bold, color: color),
            ),
            if (!isEditable)
              Container(
                padding: const EdgeInsets.symmetric(horizontal: 4, vertical: 1),
                decoration: BoxDecoration(
                  color: Colors.white10,
                  borderRadius: BorderRadius.circular(3),
                ),
                child: const Text('AUTO',
                    style: TextStyle(
                        fontSize: 8.5,
                        color: Colors.white60,
                        fontWeight: FontWeight.bold)),
              ),
          ],
        ),
        const SizedBox(height: 4),
        Container(
          height: 38,
          decoration: BoxDecoration(
            color:
                isEditable ? const Color(0xFF131722) : const Color(0xFF1A2234),
            borderRadius: BorderRadius.circular(6),
            border: Border.all(
              color: isEditable
                  ? const Color(0xFFFFB300).withValues(alpha: 0.6)
                  : const Color(0xFF2E384D),
              width: isEditable ? 1.2 : 0.8,
            ),
          ),
          child: TextField(
            controller: controller,
            enabled: isEditable,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            style: TextStyle(
              fontSize: 14,
              fontWeight: FontWeight.bold,
              color: color,
              fontFamily: 'monospace',
            ),
            decoration: InputDecoration(
              contentPadding:
                  const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
              border: InputBorder.none,
              prefixText: '$_currSym ',
              prefixStyle: TextStyle(
                  fontSize: 13, fontWeight: FontWeight.bold, color: color),
              hintText: hintText,
              hintStyle: const TextStyle(color: Colors.white24, fontSize: 13),
            ),
            onChanged: onChanged,
          ),
        ),
        const SizedBox(height: 3),
        Text(
          helperText,
          style: const TextStyle(fontSize: 9.5, color: Colors.white54),
        ),
      ],
    );
  }

  Widget _checkChip(String label, String value, bool valid) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(label,
            style: const TextStyle(fontSize: 9, color: AppColors.textMuted)),
        const SizedBox(height: 2),
        Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(valid ? Icons.check_circle : Icons.cancel,
                size: 11,
                color: valid ? AppColors.bullish : AppColors.textMuted),
            const SizedBox(width: 3),
            Text(value,
                style: const TextStyle(
                    fontSize: 10,
                    fontWeight: FontWeight.bold,
                    color: Colors.white)),
          ],
        ),
      ],
    );
  }

  // --------------------------------------------------------------------------
  // Bottom Status Bar
  // --------------------------------------------------------------------------
  Widget _buildBottomStatusBar() {
    final settings = ref.watch(settingsProvider);
    final isLiveMode = !settings.isPaperMode;
    final isThb = _selectedSymbol.toUpperCase().contains('THB');
    final feedName = isThb ? 'InnovestX OpenAPI' : '$_selectedExchange.com';
    final modeLabel = isLiveMode
        ? (isThb ? 'Mode: Live Trading (฿)' : 'Mode: Live Trading (\$)')
        : 'Mode: Paper Trading (\$)';

    return Container(
      color: AppColors.surface,
      padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            Icon(isLiveMode ? Icons.verified : Icons.science_outlined,
                size: 14,
                color: isLiveMode
                    ? const Color(0xFF9B59B6)
                    : const Color(0xFF00E5FF)),
            const SizedBox(width: 6),
            Text(
              'Feed: $feedName  •  Latency: ${isLiveMode ? "15ms" : "28ms"}  •  $modeLabel',
              style: TextStyle(
                fontSize: 11,
                color: isLiveMode
                    ? const Color(0xFFC39BD3)
                    : const Color(0xFF00E5FF).withValues(alpha: 0.8),
                fontWeight: isLiveMode ? FontWeight.bold : FontWeight.normal,
              ),
            ),
            const SizedBox(width: 16),
            Text(
              'Last updated: ${DateTime.now().toLocal().toString().split('.').first}',
              style: const TextStyle(fontSize: 11, color: AppColors.textMuted),
            ),
          ],
        ),
      ),
    );
  }
}
