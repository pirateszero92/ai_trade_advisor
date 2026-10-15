import 'package:flutter/foundation.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Web-safe wrapper around FlutterSecureStorage and SharedPreferences.
///
/// On Flutter Web served over HTTP (non-localhost), browser cryptography APIs
/// (window.crypto.subtle) throw exceptions. On other platforms without a system
/// keychain, FlutterSecureStorage can also fail.
/// SafeStorage gracefully attempts secure storage and falls back to
/// SharedPreferences (with a 'sec_' prefix) so saving credentials/settings
/// never crashes the app.
class SafeStorage {
  static const FlutterSecureStorage _secure = FlutterSecureStorage();
  static const String _prefix = 'sec_';

  static Future<void> write({required String key, required String value}) async {
    try {
      await _secure.write(key: key, value: value);
    } catch (e) {
      debugPrint('[SafeStorage] SecureStorage.write failed ($key): $e');
    }
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('$_prefix$key', value);
    } catch (e) {
      debugPrint('[SafeStorage] SharedPreferences.setString failed ($key): $e');
    }
  }

  static Future<String?> read({required String key}) async {
    try {
      final val = await _secure.read(key: key);
      if (val != null && val.isNotEmpty) {
        return val;
      }
    } catch (e) {
      debugPrint('[SafeStorage] SecureStorage.read failed ($key): $e');
    }
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getString('$_prefix$key') ?? prefs.getString(key);
    } catch (e) {
      debugPrint('[SafeStorage] SharedPreferences.read failed ($key): $e');
      return null;
    }
  }

  static Future<void> delete({required String key}) async {
    try {
      await _secure.delete(key: key);
    } catch (e) {
      debugPrint('[SafeStorage] SecureStorage.delete failed ($key): $e');
    }
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.remove('$_prefix$key');
      await prefs.remove(key);
    } catch (e) {
      debugPrint('[SafeStorage] SharedPreferences.remove failed ($key): $e');
    }
  }
}
