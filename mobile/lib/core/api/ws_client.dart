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

  final _connectionStateController = StreamController<WsConnectionState>.broadcast();
  final _priceStreamController = StreamController<Map<String, dynamic>>.broadcast();
  final _tradeStreamController = StreamController<Map<String, dynamic>>.broadcast();
  final _signalStreamController = StreamController<Map<String, dynamic>>.broadcast();

  Stream<WsConnectionState> get connectionStateStream => _connectionStateController.stream;
  Stream<Map<String, dynamic>> get priceStream => _priceStreamController.stream;
  Stream<Map<String, dynamic>> get tradeStream => _tradeStreamController.stream;
  Stream<Map<String, dynamic>> get signalStream => _signalStreamController.stream;

  WsConnectionState _currentState = WsConnectionState.disconnected;
  WsConnectionState get currentState => _currentState;
  bool get isConnected => _currentState == WsConnectionState.connected;

  final Map<String, dynamic> _latestPrices = {};
  Map<String, dynamic> get latestPrices => Map.unmodifiable(_latestPrices);

  void init() {
    _disposed = false;
    connect();
  }

  void connect() {
    if (_disposed || _currentState == WsConnectionState.connecting) return;
    _setConnectionState(WsConnectionState.connecting);

    try {
      final wsUri = Uri.parse(AppApi.wsUrl('/ws/stream?api_key=dev'));
      debugPrint('[WS-Client] Connecting to $wsUri...');
      _channel = WebSocketChannel.connect(wsUri);

      _channel!.stream.listen(
        _onMessage,
        onError: (err) {
          debugPrint('[WS-Client] Connection error: $err');
          _handleDisconnect();
        },
        onDone: () {
          debugPrint('[WS-Client] Connection closed cleanly');
          _handleDisconnect();
        },
        cancelOnError: true,
      );

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
    _channel = null;
    _pingTimer?.cancel();
    _setConnectionState(WsConnectionState.disconnected);
    _scheduleReconnect();
  }

  void _scheduleReconnect() {
    if (_disposed) return;
    _reconnectTimer?.cancel();
    _reconnectTimer = Timer(const Duration(seconds: 3), () {
      if (!_disposed && !isConnected) {
        connect();
      }
    });
  }

  void _setConnectionState(WsConnectionState state) {
    _currentState = state;
    if (!_connectionStateController.isClosed) {
      _connectionStateController.add(state);
    }
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
