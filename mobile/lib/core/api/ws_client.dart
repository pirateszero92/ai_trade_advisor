import 'dart:async';
import 'dart:convert';
import 'package:flutter/foundation.dart';
import 'package:web_socket_channel/web_socket_channel.dart';
import 'api_client.dart';

enum WsConnectionState { disconnected, connecting, connected, error }

class AppWebSocketClient {
  static final AppWebSocketClient instance = AppWebSocketClient._internal();
  AppWebSocketClient._internal();

  WebSocketChannel? _channel;
  Timer? _reconnectTimer;
  Timer? _pingTimer;
  bool _disposed = false;
  int _reconnectAttempts = 0;

  final _connectionStateController =
      StreamController<WsConnectionState>.broadcast();
  final _priceStreamController =
      StreamController<Map<String, dynamic>>.broadcast();
  final _tradeStreamController =
      StreamController<Map<String, dynamic>>.broadcast();
  final _signalStreamController =
      StreamController<Map<String, dynamic>>.broadcast();

  Stream<WsConnectionState> get connectionStateStream =>
      _connectionStateController.stream;
  Stream<Map<String, dynamic>> get priceStream => _priceStreamController.stream;
  Stream<Map<String, dynamic>> get tradeStream => _tradeStreamController.stream;
  Stream<Map<String, dynamic>> get signalStream =>
      _signalStreamController.stream;

  WsConnectionState _currentState = WsConnectionState.disconnected;
  WsConnectionState get currentState => _currentState;
  bool get isConnected => _currentState == WsConnectionState.connected;

  final Map<String, dynamic> _latestPrices = {};
  Map<String, dynamic> get latestPrices => Map.unmodifiable(_latestPrices);

  void init() {
    _disposed = false;
    unawaited(connect());
  }

  Future<void> connect() async {
    if (_disposed ||
        _currentState == WsConnectionState.connecting ||
        isConnected) {
      return;
    }
    _setConnectionState(WsConnectionState.connecting);

    try {
      final wsUri = Uri.parse(AppApi.wsUrl('/ws/stream'));
      final apiKey = await ApiConfig.getApiKey();
      if (apiKey == null || apiKey.isEmpty) {
        throw StateError('API key is not configured');
      }
      final encodedKey =
          base64Url.encode(utf8.encode(apiKey)).replaceAll('=', '');
      final protocol = 'api-key.$encodedKey';
      debugPrint('[WS-Client] Connecting to $wsUri...');
      final channel = WebSocketChannel.connect(wsUri, protocols: [protocol]);
      _channel = channel;
      await channel.ready.timeout(const Duration(seconds: 10));

      channel.stream.listen(
        _onMessage,
        onError: (err) {
          debugPrint('[WS-Client] Connection error: $err');
          if (identical(_channel, channel)) _handleDisconnect();
        },
        onDone: () {
          debugPrint('[WS-Client] Connection closed cleanly');
          if (identical(_channel, channel)) _handleDisconnect();
        },
        cancelOnError: true,
      );

      _reconnectAttempts = 0;
      _setConnectionState(WsConnectionState.connected);
      _startPingHeartbeat();
      _subscribeChannels();
    } catch (e) {
      debugPrint('[WS-Client] Connect exception: $e');
      _handleDisconnect();
    }
  }

  void _onMessage(dynamic raw) {
    try {
      final String text = raw is List<int> ? utf8.decode(raw) : raw.toString();
      final data = json.decode(text) as Map<String, dynamic>;
      final type = data['type']?.toString();

      if (type == 'price_tick' || type == 'initial_snapshot') {
        final payload = data['data'] as Map<String, dynamic>? ?? {};
        _latestPrices.addAll(payload);
        _priceStreamController.add(payload);
      } else if (type == 'trade_updated' || type == 'trade_closed') {
        _tradeStreamController.add(data);
      } else if (type == 'signal') {
        _signalStreamController.add(data);
      }
    } catch (e) {
      debugPrint('[WS-Client] Parse error: $e');
    }
  }

  void _subscribeChannels() {
    sendMessage({
      'action': 'subscribe',
      'channels': ['tickers', 'trades', 'signals'],
    });
  }

  void _startPingHeartbeat() {
    _pingTimer?.cancel();
    _pingTimer = Timer.periodic(const Duration(seconds: 15), (_) {
      if (isConnected) {
        sendMessage({'action': 'ping'});
      }
    });
  }

  void sendMessage(Map<String, dynamic> msg) {
    if (_channel != null && isConnected) {
      try {
        _channel!.sink.add(json.encode(msg));
      } catch (_) {}
    }
  }

  void _handleDisconnect() {
    if (_currentState == WsConnectionState.disconnected &&
        _reconnectTimer?.isActive == true) {
      return;
    }
    _channel = null;
    _pingTimer?.cancel();
    _setConnectionState(WsConnectionState.disconnected);
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_disposed) return;
    _reconnectTimer?.cancel();
    _reconnectAttempts++;
    final exponent = _reconnectAttempts.clamp(1, 6);
    final delaySeconds = (1 << exponent).clamp(2, 60);
    _reconnectTimer = Timer(Duration(seconds: delaySeconds), () {
      if (!_disposed && !isConnected) {
        unawaited(connect());
      }
    });
  }

  void _setConnectionState(WsConnectionState state) {
    _currentState = state;
    if (!_connectionStateController.isClosed) {
      _connectionStateController.add(state);
    }
  }

  Future<void> reconnect() async {
    if (_disposed) return;
    _reconnectTimer?.cancel();
    _pingTimer?.cancel();
    final previous = _channel;
    _channel = null;
    _reconnectAttempts = 0;
    _setConnectionState(WsConnectionState.disconnected);
    try {
      await previous?.sink.close();
    } catch (_) {}
    await connect();
  }

  void dispose() {
    _disposed = true;
    _reconnectTimer?.cancel();
    _pingTimer?.cancel();
    _channel?.sink.close();
    _connectionStateController.close();
    _priceStreamController.close();
    _tradeStreamController.close();
    _signalStreamController.close();
  }
}
