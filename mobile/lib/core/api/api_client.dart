import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ApiConfig {
  static const _storage = FlutterSecureStorage();
  static const _defaultBaseUrl = kIsWeb ? '' : 'http://192.168.1.40:8000'; // Default to Pi LAN IP on mobile

  static Future<String> getBaseUrl() async {
    if (kIsWeb) return '';
    final prefs = await SharedPreferences.getInstance();
    return prefs.getString('api_base_url') ?? _defaultBaseUrl;
  }

  static Future<void> setBaseUrl(String url) async {
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('api_base_url', url);
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

  static void setBaseUrl(String url) {
    final trimmed = url.trim().replaceAll(RegExp(r'/$'), '');
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
    // 3. Mobile default IP
    return 'http://192.168.1.40:8000';
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
      return 'ws://192.168.1.40:8000$clean';
    }
    final wsBase = base
        .replaceFirst(RegExp(r'^https://', caseSensitive: false), 'wss://')
        .replaceFirst(RegExp(r'^http://', caseSensitive: false), 'ws://');
    return '$wsBase$clean';
  }

  static Dio? _dioInstance;

  static Dio get dio {
    if (_dioInstance == null) {
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
              final key = await ApiConfig.getApiKey();
              if (key != null && key.isNotEmpty) {
                options.headers['X-API-Key'] = key;
              }
            } catch (_) {}
            return handler.next(options);
          },
        ),
      );
      _dioInstance = d;
    }
    return _dioInstance!;
  }
}

