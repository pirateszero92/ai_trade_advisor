import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ApiConfig {
  static const _storage = FlutterSecureStorage();
  static const _defaultBaseUrl = kIsWeb ? '' : 'http://10.0.2.2:8000'; // Relative on web, 10.0.2.2 on Android

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
  static String get baseUrl {
    if (kIsWeb) return '';
    return 'http://10.0.2.2:8000';
  }

  static String url(String path) {
    final clean = path.startsWith('/') ? path : '/$path';
    if (kIsWeb) {
      return clean;
    }
    return '$baseUrl$clean';
  }
}

