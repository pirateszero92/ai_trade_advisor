import 'package:dio/dio.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ApiConfig {
  static const _storage = FlutterSecureStorage();
  static const _defaultBaseUrl = kIsWeb ? '' : 'http://192.168.251.23:8000'; // Default to PC LAN IP on mobile

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
}

class AppApi {
  static String? _customBaseUrl;

  static void setBaseUrl(String url) {
    final trimmed = url.trim().replaceAll(RegExp(r'/$'), '');
    _customBaseUrl = trimmed;
  }

  static String get baseUrl {
    if (kIsWeb) {
      try {
        final origin = Uri.base.origin;
        if (origin.isNotEmpty && !origin.startsWith('null')) {
          return origin;
        }
      } catch (_) {}
      return '';
    }
    if (_customBaseUrl != null && _customBaseUrl!.isNotEmpty) {
      return _customBaseUrl!;
    }
    return 'http://192.168.251.23:8000'; // Default to PC Wi-Fi IP for direct physical phone connection
  }

  static String url(String path) {
    final clean = path.startsWith('/') ? path : '/$path';
    final base = baseUrl;
    if (base.isEmpty) return clean;
    return '$base$clean';
  }

  static Dio? _dioInstance;

  static Dio get dio {
    _dioInstance ??= Dio(
      BaseOptions(
        connectTimeout: const Duration(seconds: 15),
        receiveTimeout: const Duration(seconds: 20),
        sendTimeout: const Duration(seconds: 15),
      ),
    );
    return _dioInstance!;
  }
}

