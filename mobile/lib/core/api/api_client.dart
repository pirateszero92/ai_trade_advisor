import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ApiConfig {
  static const _storage = FlutterSecureStorage();
  static const _defaultBaseUrl = kIsWeb
      ? ''
      : 'http://10.0.2.2:8000'; // Default to Android emulator / local host on mobile

  static Future<String> getBaseUrl() async {
    if (kIsWeb) return '';
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString('api_base_url') ?? _defaultBaseUrl;
  }

  static Future<void> setBaseUrl(String url) async {
    _validateBaseUrl(url);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString(
        'api_base_url', url.trim().replaceAll(RegExp(r'/$'), ''));
  }

  static void _validateBaseUrl(String value) {
    final uri = Uri.tryParse(value.trim());
    if (uri == null ||
        !uri.hasAuthority ||
        !{'http', 'https'}.contains(uri.scheme) ||
        uri.host.isEmpty ||
        uri.userInfo.isNotEmpty) {
      throw const FormatException(
          'API Base URL must be a valid http(s) URL without credentials');
    }
  }

  static Future<String?> getApiKey() async {
    return _storage.read(key: 'api_key');
  }

  static Future<void> setApiKey(String key) async {
    await _storage.write(key: 'api_key', value: key);
  }

  static Future<String?> getInnovestxKey() async {
    return _storage.read(key: 'innovestx_key');
  }

  static Future<void> setInnovestxKey(String key) async {
    await _storage.write(key: 'innovestx_key', value: key);
  }

  static Future<String?> getInnovestxSecret() async {
    return _storage.read(key: 'innovestx_secret');
  }

  static Future<void> setInnovestxSecret(String secret) async {
    await _storage.write(key: 'innovestx_secret', value: secret);
  }
}

class AppApi {
  static String? _customBaseUrl;
  // Live authorization is intentionally process-memory only. It must never be
  // written to SharedPreferences or secure storage; app restart means Paper.
  static String? _liveSessionToken;
  static DateTime? _liveSessionExpiresAt;

  static bool get hasActiveLiveSession {
    final token = _liveSessionToken;
    final expiresAt = _liveSessionExpiresAt;
    if (token == null || token.isEmpty || expiresAt == null) return false;
    if (!DateTime.now().toUtc().isBefore(expiresAt)) {
      clearLiveSession();
      return false;
    }
    return true;
  }

  static DateTime? get liveSessionExpiresAt =>
      hasActiveLiveSession ? _liveSessionExpiresAt : null;

  static void setLiveSession({
    required String token,
    required DateTime expiresAt,
  }) {
    if (token.trim().isEmpty ||
        !DateTime.now().toUtc().isBefore(expiresAt.toUtc())) {
      throw const FormatException('Invalid or expired Live Session');
    }
    _liveSessionToken = token.trim();
    _liveSessionExpiresAt = expiresAt.toUtc();
  }

  static void clearLiveSession() {
    _liveSessionToken = null;
    _liveSessionExpiresAt = null;
  }

  static void setBaseUrl(String url) {
    final trimmed = url.trim().replaceAll(RegExp(r'/$'), '');
    final uri = Uri.tryParse(trimmed);
    if (trimmed.isNotEmpty &&
        (uri == null ||
            !uri.hasAuthority ||
            !{'http', 'https'}.contains(uri.scheme) ||
            uri.host.isEmpty ||
            uri.userInfo.isNotEmpty)) {
      throw const FormatException('Invalid API Base URL');
    }
    _customBaseUrl = trimmed;
  }

  static String get baseUrl {
    // 1. User-configured custom URL takes top priority
    if (_customBaseUrl != null && _customBaseUrl!.isNotEmpty) {
      return _customBaseUrl!;
    }
    // 2. Web browser origin fallback
    if (kIsWeb) {
      try {
        final origin = Uri.base.origin;
        if (origin.isNotEmpty && !origin.startsWith('null')) {
          return origin;
        }
      } catch (_) {}
      return '';
    }
    // 3. Mobile default IP (Android emulator loopback)
    return 'http://10.0.2.2:8000';
  }

  static String url(String path) {
    final clean = path.startsWith('/') ? path : '/$path';
    final base = baseUrl;
    if (base.isEmpty) return clean;
    return '$base$clean';
  }

  static String wsUrl(String path) {
    final clean = path.startsWith('/') ? path : '/$path';
    final base = baseUrl;
    if (base.isEmpty) {
      if (kIsWeb) {
        final loc = Uri.base;
        final scheme = loc.scheme == 'https' ? 'wss' : 'ws';
        final host = loc.host;
        final port = loc.hasPort ? ':${loc.port}' : '';
        return '$scheme://$host$port$clean';
      }
      return 'ws://10.0.2.2:8000$clean';
    }
    final wsBase = base
        .replaceFirst(RegExp(r'^https://', caseSensitive: false), 'wss://')
        .replaceFirst(RegExp(r'^http://', caseSensitive: false), 'ws://');
    return '$wsBase$clean';
  }

  static String? _cachedApiKey;

  static void clearApiKeyCache() {
    _cachedApiKey = null;
  }

  static final Dio _dioInstance = _createDio();

  static Dio get dio => _dioInstance;

  static Dio _createDio() {
    final d = Dio(
      BaseOptions(
        connectTimeout: const Duration(seconds: 25),
        receiveTimeout: const Duration(seconds: 30),
        sendTimeout: const Duration(seconds: 25),
      ),
    );
    d.interceptors.add(
      InterceptorsWrapper(
        onRequest: (options, handler) async {
          try {
            _cachedApiKey ??= await ApiConfig.getApiKey();
            final key = _cachedApiKey;
            if (key != null && key.isNotEmpty) {
              options.headers['X-API-Key'] = key;
            }
            if (hasActiveLiveSession) {
              options.headers['X-Live-Session-Token'] = _liveSessionToken;
            }
          } catch (e) {
            debugPrint('[API] Error retrieving API key: $e');
          }
          return handler.next(options);
        },
        onError: (error, handler) {
          if (error.response?.statusCode == 401 &&
              error.requestOptions.headers
                  .containsKey('X-Live-Session-Token')) {
            clearLiveSession();
          }
          return handler.next(error);
        },
      ),
    );
    return d;
  }
}
