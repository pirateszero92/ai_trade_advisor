import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';
import '../../core/api/api_client.dart';
import '../../core/api/ws_client.dart';
import '../../core/constants/app_constants.dart';

// ---------------------------------------------------------------------------
// Settings state
// ---------------------------------------------------------------------------

class SettingsState {
  final String apiBaseUrl;
  final String aiProvider; // 'local' | 'gemini' | 'openrouter'
  final String lmStudioEndpoint;
  final String lmStudioModel;
  final String geminiKey;
  final String geminiModel;
  final String openRouterKey;
  final String openRouterModel;
  final double riskPerTrade;
  final double maxDailyLoss;
  final int maxPositions;
  final bool isPaperMode;
  final bool fcmEnabled;
  final String telegramToken;
  final String telegramChatId;
  final String lineToken;
  final String entryMode;
  final bool autoSlTp;
  final bool autoInvalidation;
  final double targetRr;
  final double defaultSlPct;

  const SettingsState({
    this.apiBaseUrl = '',
    this.aiProvider = 'local',
    this.lmStudioEndpoint = 'http://host.docker.internal:11434',
    this.lmStudioModel = 'gpt-oss:120b-cloud',
    this.geminiKey = '',
    this.geminiModel = 'gemini-2.0-flash',
    this.openRouterKey = '',
    this.openRouterModel = 'anthropic/claude-3.5-sonnet',
    this.riskPerTrade = 1.0,
    this.maxDailyLoss = 3.0,
    this.maxPositions = 3,
    this.targetRr = 2.0,
    this.defaultSlPct = 1.0,
    this.isPaperMode = true,
    this.fcmEnabled = true,
    this.telegramToken = '',
    this.telegramChatId = '',
    this.lineToken = '',
    this.entryMode = 'limit',
    this.autoSlTp = true,
    this.autoInvalidation = true,
  });

  SettingsState copyWith({
    String? apiBaseUrl,
    String? aiProvider,
    String? lmStudioEndpoint,
    String? lmStudioModel,
    String? geminiKey,
    String? geminiModel,
    String? openRouterKey,
    String? openRouterModel,
    double? riskPerTrade,
    double? maxDailyLoss,
    int? maxPositions,
    double? targetRr,
    double? defaultSlPct,
    bool? isPaperMode,
    bool? fcmEnabled,
    String? telegramToken,
    String? telegramChatId,
    String? lineToken,
    String? entryMode,
    bool? autoSlTp,
    bool? autoInvalidation,
  }) {
    return SettingsState(
      apiBaseUrl: apiBaseUrl ?? this.apiBaseUrl,
      aiProvider: aiProvider ?? this.aiProvider,
      lmStudioEndpoint: lmStudioEndpoint ?? this.lmStudioEndpoint,
      lmStudioModel: lmStudioModel ?? this.lmStudioModel,
      geminiKey: geminiKey ?? this.geminiKey,
      geminiModel: geminiModel ?? this.geminiModel,
      openRouterKey: openRouterKey ?? this.openRouterKey,
      openRouterModel: openRouterModel ?? this.openRouterModel,
      riskPerTrade: riskPerTrade ?? this.riskPerTrade,
      maxDailyLoss: maxDailyLoss ?? this.maxDailyLoss,
      maxPositions: maxPositions ?? this.maxPositions,
      targetRr: targetRr ?? this.targetRr,
      defaultSlPct: defaultSlPct ?? this.defaultSlPct,
      isPaperMode: isPaperMode ?? this.isPaperMode,
      fcmEnabled: fcmEnabled ?? this.fcmEnabled,
      telegramToken: telegramToken ?? this.telegramToken,
      telegramChatId: telegramChatId ?? this.telegramChatId,
      lineToken: lineToken ?? this.lineToken,
      entryMode: entryMode ?? this.entryMode,
      autoSlTp: autoSlTp ?? this.autoSlTp,
      autoInvalidation: autoInvalidation ?? this.autoInvalidation,
    );
  }
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

final settingsProvider =
    StateNotifierProvider<SettingsNotifier, SettingsState>((ref) {
  return SettingsNotifier();
});

class SettingsNotifier extends StateNotifier<SettingsState> {
  static const _storage = FlutterSecureStorage();
  Timer? _liveSessionExpiryTimer;
  Timer? _liveSessionHeartbeatTimer;

  SettingsNotifier() : super(const SettingsState()) {
    AppApi.clearLiveSession();
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    final telegramToken = await _storage.read(key: 'telegram_token') ??
        prefs.getString('telegram_token') ??
        '';
    final telegramChatId = await _storage.read(key: 'telegram_chat_id') ??
        prefs.getString('telegram_chat_id') ??
        '';
    final lineToken = await _storage.read(key: 'line_token') ??
        prefs.getString('line_token') ??
        '';
    // A previous app version persisted this switch. Remove it permanently:
    // every process starts in Paper and Live requires a fresh backend session.
    await prefs.remove('is_paper_mode');
    state = state.copyWith(
      apiBaseUrl: prefs.getString('api_base_url') ?? state.apiBaseUrl,
      aiProvider: prefs.getString('ai_provider') ?? state.aiProvider,
      lmStudioEndpoint:
          prefs.getString('lm_studio_endpoint') ?? state.lmStudioEndpoint,
      lmStudioModel: prefs.getString('lm_studio_model') ?? state.lmStudioModel,
      geminiModel: prefs.getString('gemini_model') ?? state.geminiModel,
      openRouterModel:
          prefs.getString('openrouter_model') ?? state.openRouterModel,
      riskPerTrade: prefs.getDouble('risk_per_trade') ?? state.riskPerTrade,
      maxDailyLoss: prefs.getDouble('max_daily_loss') ?? state.maxDailyLoss,
      maxPositions: prefs.getInt('max_positions') ?? state.maxPositions,
      targetRr: prefs.getDouble('target_rr') ?? state.targetRr,
      defaultSlPct: prefs.getDouble('default_sl_pct') ?? state.defaultSlPct,
      isPaperMode: !AppApi.hasActiveLiveSession,
      fcmEnabled: prefs.getBool('fcm_enabled') ?? state.fcmEnabled,
      telegramToken: telegramToken,
      telegramChatId: telegramChatId,
      lineToken: lineToken,
      entryMode: prefs.getString('entry_mode') ?? state.entryMode,
      autoSlTp: prefs.getBool('auto_sl_tp') ?? state.autoSlTp,
      autoInvalidation:
          prefs.getBool('auto_invalidation') ?? state.autoInvalidation,
      geminiKey: await _storage.read(key: 'gemini_key') ?? '',
      openRouterKey: await _storage.read(key: 'openrouter_key') ?? '',
    );
  }

  Future<void> save(SettingsState newState) async {
    // General settings saves are not allowed to unlock Live mode.
    final safeState = newState.copyWith(isPaperMode: state.isPaperMode);
    state = safeState;
    if (safeState.apiBaseUrl.isNotEmpty) {
      AppApi.setBaseUrl(safeState.apiBaseUrl);
    }
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('api_base_url', safeState.apiBaseUrl);
    await prefs.setString('ai_provider', safeState.aiProvider);
    await prefs.setString('lm_studio_endpoint', safeState.lmStudioEndpoint);
    await prefs.setString('lm_studio_model', safeState.lmStudioModel);
    await prefs.setString('gemini_model', safeState.geminiModel);
    await prefs.setString('openrouter_model', safeState.openRouterModel);
    await prefs.setDouble('risk_per_trade', safeState.riskPerTrade);
    await prefs.setDouble('max_daily_loss', safeState.maxDailyLoss);
    await prefs.setInt('max_positions', safeState.maxPositions);
    await prefs.setDouble('target_rr', safeState.targetRr);
    await prefs.setDouble('default_sl_pct', safeState.defaultSlPct);
    await prefs.remove('is_paper_mode');
    await prefs.setBool('fcm_enabled', safeState.fcmEnabled);
    await _storage.write(key: 'telegram_token', value: safeState.telegramToken);
    await _storage.write(
        key: 'telegram_chat_id', value: safeState.telegramChatId);
    await _storage.write(key: 'line_token', value: safeState.lineToken);
    await prefs.remove('telegram_token');
    await prefs.remove('telegram_chat_id');
    await prefs.remove('line_token');
    await prefs.setString('entry_mode', safeState.entryMode);
    await prefs.setBool('auto_sl_tp', safeState.autoSlTp);
    await prefs.setBool('auto_invalidation', safeState.autoInvalidation);
    await _storage.write(key: 'gemini_key', value: safeState.geminiKey);
    await _storage.write(key: 'openrouter_key', value: safeState.openRouterKey);
  }

  Future<DateTime> activateLiveMode({
    String broker = 'innovestx',
    int ttlMinutes = 15,
  }) async {
    final response = await AppApi.dio.post(
      AppApi.url('/api/v1/live/session'),
      data: {
        'broker': broker,
        'confirmation': 'ENABLE_LIVE_TRADING',
        'ttl_minutes': ttlMinutes,
      },
    );
    final token = response.data['session_token']?.toString() ?? '';
    final expiresAt =
        DateTime.tryParse(response.data['expires_at']?.toString() ?? '');
    if (token.isEmpty || expiresAt == null) {
      throw StateError('Backend did not return a valid Live Session');
    }
    AppApi.setLiveSession(token: token, expiresAt: expiresAt);
    _liveSessionExpiryTimer?.cancel();
    final remaining = expiresAt.toUtc().difference(DateTime.now().toUtc());
    _liveSessionExpiryTimer = Timer(remaining, _expireLiveSession);
    _liveSessionHeartbeatTimer?.cancel();
    _liveSessionHeartbeatTimer = Timer.periodic(
      const Duration(seconds: 30),
      (_) => _verifyLiveSession(),
    );
    state = state.copyWith(isPaperMode: false);
    return expiresAt.toLocal();
  }

  Future<void> _verifyLiveSession() async {
    if (!AppApi.hasActiveLiveSession) {
      _expireLiveSession();
      return;
    }
    try {
      final response = await AppApi.dio.get(AppApi.url('/api/v1/live/session'));
      if (response.data['mode']?.toString() != 'live') {
        _expireLiveSession();
      }
    } on DioException catch (error) {
      if (error.response?.statusCode == 401 || !AppApi.hasActiveLiveSession) {
        _expireLiveSession();
      }
    }
  }

  Future<void> deactivateLiveMode() async {
    try {
      if (AppApi.hasActiveLiveSession) {
        await AppApi.dio.delete(AppApi.url('/api/v1/live/session'));
      }
    } catch (_) {
      // Local fail-safe still returns to Paper even if the backend is offline.
    } finally {
      _expireLiveSession();
    }
  }

  Future<void> activateLiveKillSwitch() async {
    try {
      await AppApi.dio.post(
        AppApi.url('/api/v1/live/kill-switch'),
        data: {'confirmation': 'DISABLE_LIVE_TRADING'},
      );
    } finally {
      _expireLiveSession();
    }
  }

  void _expireLiveSession() {
    _liveSessionExpiryTimer?.cancel();
    _liveSessionExpiryTimer = null;
    _liveSessionHeartbeatTimer?.cancel();
    _liveSessionHeartbeatTimer = null;
    AppApi.clearLiveSession();
    if (mounted) {
      state = state.copyWith(isPaperMode: true);
    }
  }

  @override
  void dispose() {
    _liveSessionExpiryTimer?.cancel();
    _liveSessionHeartbeatTimer?.cancel();
    AppApi.clearLiveSession();
    super.dispose();
  }
}

// ---------------------------------------------------------------------------
// Settings Screen
// ---------------------------------------------------------------------------

class SettingsScreen extends ConsumerStatefulWidget {
  const SettingsScreen({super.key});

  @override
  ConsumerState<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends ConsumerState<SettingsScreen>
    with TickerProviderStateMixin {
  static const _secureStorage = FlutterSecureStorage();

  late TabController _aiTabController;
  late TabController _brokerTabController;

  // Text controllers
  late TextEditingController _apiUrlCtrl;
  late TextEditingController _backendApiKeyCtrl;
  late TextEditingController _lmEndpointCtrl;
  late TextEditingController _lmModelCtrl;
  late TextEditingController _geminiKeyCtrl;
  late TextEditingController _geminiModelCtrl;
  late TextEditingController _openRouterKeyCtrl;
  late TextEditingController _openRouterModelCtrl;
  late TextEditingController _telegramTokenCtrl;
  late TextEditingController _telegramChatIdCtrl;
  late TextEditingController _lineTokenCtrl;

  late TextEditingController _innovestxKeyCtrl;
  late TextEditingController _innovestxSecretCtrl;
  late TextEditingController _watchlistFilterCtrl;
  late TextEditingController _mt5LoginCtrl;
  late TextEditingController _mt5PasswordCtrl;
  late TextEditingController _mt5ServerCtrl;
  late TextEditingController _mt5PathCtrl;
  late TextEditingController _binanceKeyCtrl;
  late TextEditingController _binanceSecretCtrl;
  late TextEditingController _bybitKeyCtrl;
  late TextEditingController _bybitSecretCtrl;
  late TextEditingController _alpacaKeyCtrl;
  late TextEditingController _alpacaSecretCtrl;
  late TextEditingController _alpacaBaseUrlCtrl;

  List<Map<String, dynamic>> _watchlist = [];
  Map<String, dynamic> _brokerConfig = {};

  @override
  void initState() {
    super.initState();
    _aiTabController = TabController(length: 3, vsync: this);
    _brokerTabController = TabController(length: 4, vsync: this);
    _apiUrlCtrl = TextEditingController();
    _backendApiKeyCtrl = TextEditingController();
    _lmEndpointCtrl = TextEditingController();
    _lmModelCtrl = TextEditingController();
    _geminiKeyCtrl = TextEditingController();
    _geminiModelCtrl = TextEditingController();
    _openRouterKeyCtrl = TextEditingController();
    _openRouterModelCtrl = TextEditingController();
    _telegramTokenCtrl = TextEditingController();
    _telegramChatIdCtrl = TextEditingController();
    _lineTokenCtrl = TextEditingController();

    _innovestxKeyCtrl = TextEditingController();
    _innovestxSecretCtrl = TextEditingController();
    _watchlistFilterCtrl = TextEditingController();
    _mt5LoginCtrl = TextEditingController();
    _mt5PasswordCtrl = TextEditingController();
    _mt5ServerCtrl = TextEditingController();
    _mt5PathCtrl = TextEditingController(
        text: r'C:/Program Files/MetaTrader 5/terminal64.exe');
    _binanceKeyCtrl = TextEditingController();
    _binanceSecretCtrl = TextEditingController();
    _bybitKeyCtrl = TextEditingController();
    _bybitSecretCtrl = TextEditingController();
    _alpacaKeyCtrl = TextEditingController();
    _alpacaSecretCtrl = TextEditingController();
    _alpacaBaseUrlCtrl =
        TextEditingController(text: 'https://paper-api.alpaca.markets');

    _loadAllSettings();
    _fetchWatchlist();
  }

  Future<void> _loadAllSettings() async {
    final prefs = await SharedPreferences.getInstance();
    // 1. Preload local SharedPreferences & SecureStorage
    final savedApiUrl = prefs.getString('api_base_url');
    if (savedApiUrl != null && savedApiUrl.trim().isNotEmpty) {
      _apiUrlCtrl.text = savedApiUrl.trim();
      AppApi.setBaseUrl(savedApiUrl.trim());
    } else {
      final defaultUrl = AppApi.baseUrl;
      _apiUrlCtrl.text = defaultUrl;
    }
    _backendApiKeyCtrl.text = await ApiConfig.getApiKey() ?? '';

    _lmEndpointCtrl.text = prefs.getString('lm_studio_endpoint') ??
        'http://home3.netbird.cloud:11434';
    _lmModelCtrl.text =
        prefs.getString('lm_studio_model') ?? 'gpt-oss:120b-cloud';
    _geminiModelCtrl.text =
        prefs.getString('gemini_model') ?? 'gemini-2.0-flash';
    _openRouterModelCtrl.text =
        prefs.getString('openrouter_model') ?? 'anthropic/claude-3.5-sonnet';
    _telegramTokenCtrl.text =
        await _secureStorage.read(key: 'telegram_token') ??
            prefs.getString('telegram_token') ??
            '';
    _telegramChatIdCtrl.text =
        await _secureStorage.read(key: 'telegram_chat_id') ??
            prefs.getString('telegram_chat_id') ??
            '';
    _lineTokenCtrl.text = await _secureStorage.read(key: 'line_token') ??
        prefs.getString('line_token') ??
        '';
    _geminiKeyCtrl.text = await _secureStorage.read(key: 'gemini_key') ?? '';
    _openRouterKeyCtrl.text =
        await _secureStorage.read(key: 'openrouter_key') ?? '';
    await _loadBrokerCredentials();

    final savedProvider = prefs.getString('ai_provider') ?? 'local';
    final tabIndex = {
          'local': 0,
          'lmstudio': 0,
          'openai': 0,
          'gemini': 1,
          'openrouter': 2
        }[savedProvider] ??
        0;
    _aiTabController.animateTo(tabIndex);

    // 2. Fetch live backend configuration to sync (only populate if controller is currently empty)
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/settings/llm/config'));
      final data = resp.data as Map<String, dynamic>;
      if (mounted) {
        setState(() {
          final ep = data['local_endpoint'] as String?;
          final m = data['local_model'] as String?;
          final gk = data['gemini_key'] as String?;
          final gm = data['gemini_model'] as String?;
          final ok = data['openrouter_key'] as String?;
          final om = data['openrouter_model'] as String?;

          if (_lmEndpointCtrl.text.isEmpty && ep != null && ep.isNotEmpty) {
            _lmEndpointCtrl.text = ep;
          }
          if (_lmModelCtrl.text.isEmpty && m != null && m.isNotEmpty) {
            _lmModelCtrl.text = m;
          }
          if (_geminiKeyCtrl.text.isEmpty &&
              gk != null &&
              gk.isNotEmpty &&
              !gk.contains('*')) {
            _geminiKeyCtrl.text = gk;
          }
          if (_geminiModelCtrl.text.isEmpty && gm != null && gm.isNotEmpty) {
            _geminiModelCtrl.text = gm;
          }
          if (_openRouterKeyCtrl.text.isEmpty &&
              ok != null &&
              ok.isNotEmpty &&
              !ok.contains('*')) {
            _openRouterKeyCtrl.text = ok;
          }
          if (_openRouterModelCtrl.text.isEmpty &&
              om != null &&
              om.isNotEmpty) {
            _openRouterModelCtrl.text = om;
          }
        });
      }

      // Fetch Broker configuration
      final bResp =
          await dio.get(AppApi.url('/api/v1/settings/brokers/config'));
      final bData = bResp.data as Map<String, dynamic>;
      if (mounted) {
        setState(() {
          _brokerConfig = Map<String, dynamic>.from(bData);
          if (_innovestxKeyCtrl.text.isEmpty &&
              bData['innovestx_api_key'] != null &&
              !bData['innovestx_api_key'].toString().contains('*')) {
            _innovestxKeyCtrl.text = bData['innovestx_api_key'].toString();
          }
          if (_innovestxSecretCtrl.text.isEmpty &&
              bData['innovestx_api_secret'] != null &&
              !bData['innovestx_api_secret'].toString().contains('*')) {
            _innovestxSecretCtrl.text =
                bData['innovestx_api_secret'].toString();
          }
          if (_mt5LoginCtrl.text.isEmpty &&
              bData['mt5_login'] != null &&
              bData['mt5_login'] != 0) {
            _mt5LoginCtrl.text = bData['mt5_login'].toString();
          }
          if (_mt5PasswordCtrl.text.isEmpty &&
              bData['mt5_password'] != null &&
              !bData['mt5_password'].toString().contains('*')) {
            _mt5PasswordCtrl.text = bData['mt5_password'].toString();
          }
          if (_mt5ServerCtrl.text.isEmpty && bData['mt5_server'] != null) {
            _mt5ServerCtrl.text = bData['mt5_server'].toString();
          }
          if (_mt5PathCtrl.text.isEmpty && bData['mt5_path'] != null) {
            _mt5PathCtrl.text = bData['mt5_path'].toString();
          }
          if (_binanceKeyCtrl.text.isEmpty &&
              bData['binance_api_key'] != null &&
              !bData['binance_api_key'].toString().contains('*')) {
            _binanceKeyCtrl.text = bData['binance_api_key'].toString();
          }
          if (_binanceSecretCtrl.text.isEmpty &&
              bData['binance_api_secret'] != null &&
              !bData['binance_api_secret'].toString().contains('*')) {
            _binanceSecretCtrl.text = bData['binance_api_secret'].toString();
          }
          if (_bybitKeyCtrl.text.isEmpty &&
              bData['bybit_api_key'] != null &&
              !bData['bybit_api_key'].toString().contains('*')) {
            _bybitKeyCtrl.text = bData['bybit_api_key'].toString();
          }
          if (_bybitSecretCtrl.text.isEmpty &&
              bData['bybit_api_secret'] != null &&
              !bData['bybit_api_secret'].toString().contains('*')) {
            _bybitSecretCtrl.text = bData['bybit_api_secret'].toString();
          }
          if (_alpacaKeyCtrl.text.isEmpty &&
              bData['alpaca_api_key'] != null &&
              !bData['alpaca_api_key'].toString().contains('*')) {
            _alpacaKeyCtrl.text = bData['alpaca_api_key'].toString();
          }
          if (_alpacaSecretCtrl.text.isEmpty &&
              bData['alpaca_api_secret'] != null &&
              !bData['alpaca_api_secret'].toString().contains('*')) {
            _alpacaSecretCtrl.text = bData['alpaca_api_secret'].toString();
          }
          if (_alpacaBaseUrlCtrl.text.isEmpty &&
              bData['alpaca_base_url'] != null) {
            _alpacaBaseUrlCtrl.text = bData['alpaca_base_url'].toString();
          }
        });
      }
    } catch (_) {}
  }

  Future<void> _loadBrokerCredentials() async {
    final values = await Future.wait([
      _secureStorage.read(key: 'broker_innovestx_key'),
      _secureStorage.read(key: 'broker_innovestx_secret'),
      _secureStorage.read(key: 'broker_mt5_login'),
      _secureStorage.read(key: 'broker_mt5_password'),
      _secureStorage.read(key: 'broker_mt5_server'),
      _secureStorage.read(key: 'broker_mt5_path'),
      _secureStorage.read(key: 'broker_binance_key'),
      _secureStorage.read(key: 'broker_binance_secret'),
      _secureStorage.read(key: 'broker_bybit_key'),
      _secureStorage.read(key: 'broker_bybit_secret'),
      _secureStorage.read(key: 'broker_alpaca_key'),
      _secureStorage.read(key: 'broker_alpaca_secret'),
      _secureStorage.read(key: 'broker_alpaca_base_url'),
    ]);
    if (!mounted) return;
    _innovestxKeyCtrl.text = values[0] ?? '';
    _innovestxSecretCtrl.text = values[1] ?? '';
    _mt5LoginCtrl.text = values[2] ?? '';
    _mt5PasswordCtrl.text = values[3] ?? '';
    _mt5ServerCtrl.text = values[4] ?? '';
    _mt5PathCtrl.text = values[5] ?? '';
    _binanceKeyCtrl.text = values[6] ?? '';
    _binanceSecretCtrl.text = values[7] ?? '';
    _bybitKeyCtrl.text = values[8] ?? '';
    _bybitSecretCtrl.text = values[9] ?? '';
    _alpacaKeyCtrl.text = values[10] ?? '';
    _alpacaSecretCtrl.text = values[11] ?? '';
    if ((values[12] ?? '').isNotEmpty) {
      _alpacaBaseUrlCtrl.text = values[12]!;
    }
  }

  Future<void> _saveBrokerCredentials() async {
    await Future.wait([
      _secureStorage.write(
          key: 'broker_innovestx_key', value: _innovestxKeyCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_innovestx_secret',
          value: _innovestxSecretCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_mt5_login', value: _mt5LoginCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_mt5_password', value: _mt5PasswordCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_mt5_server', value: _mt5ServerCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_mt5_path', value: _mt5PathCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_binance_key', value: _binanceKeyCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_binance_secret', value: _binanceSecretCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_bybit_key', value: _bybitKeyCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_bybit_secret', value: _bybitSecretCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_alpaca_key', value: _alpacaKeyCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_alpaca_secret', value: _alpacaSecretCtrl.text.trim()),
      _secureStorage.write(
          key: 'broker_alpaca_base_url', value: _alpacaBaseUrlCtrl.text.trim()),
    ]);
  }

  String _requestErrorMessage(Object error) {
    if (error is DioException) {
      final data = error.response?.data;
      final detail = data is Map ? data['detail'] : null;
      if (detail != null && detail.toString().trim().isNotEmpty) {
        return detail.toString().trim();
      }
      final status = error.response?.statusCode;
      return status == null
          ? 'ไม่สามารถติดต่อ backend ได้'
          : 'backend ตอบกลับ HTTP $status';
    }
    return 'เกิดข้อผิดพลาดระหว่างบันทึกข้อมูล';
  }

  Future<void> _fetchWatchlist() async {
    try {
      final dio = AppApi.dio;
      final resp = await dio.get(AppApi.url('/api/v1/settings/watchlist'));
      final List<dynamic> list = resp.data['watchlist'] ?? [];
      if (!mounted) return;
      setState(() {
        _watchlist =
            list.map((e) => Map<String, dynamic>.from(e as Map)).toList();
      });
    } catch (_) {}
  }

  Future<void> _addWatchlistItem(
      String symbol, String marketType, String tf) async {
    final isTHB = symbol.trim().toUpperCase().contains('THB');
    final ex = isTHB
        ? 'innovestx'
        : (marketType == 'crypto'
            ? 'binance'
            : (marketType == 'forex' ? 'mt5' : 'alpaca'));

    try {
      final dio = AppApi.dio;
      await dio.post(
        AppApi.url('/api/v1/settings/watchlist'),
        data: {
          'symbol': symbol.trim().toUpperCase(),
          'market_type': marketType.toLowerCase(),
          'timeframe': tf.toLowerCase(),
          'htf_timeframe': tf == '1d' ? '1w' : '4h',
          'exchange': ex,
        },
      );
      _fetchWatchlist();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text('✅ เพิ่ม $symbol เข้า Proactive Watchlist แล้ว!',
                style: const TextStyle(
                    color: Colors.black, fontWeight: FontWeight.bold)),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              backgroundColor: AppColors.bearish,
              content: Text('เพิ่มไม่สำเร็จ: $e')),
        );
      }
    }
  }

  Future<void> _addBatchWatchlistItems(List<Map<String, dynamic>> items) async {
    if (items.isEmpty) return;
    try {
      final dio = AppApi.dio;
      final resp = await dio.post(
        AppApi.url('/api/v1/settings/watchlist/batch'),
        data: {'items': items},
      );
      await _fetchWatchlist();
      if (mounted) {
        final count = resp.data['added_count'] ?? items.length;
        final total = resp.data['total_count'] ?? _watchlist.length;
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text(
              '✅ เพิ่ม $count สินทรัพย์เข้า Proactive Watchlist สำเร็จ! (รวม $total รายการ)',
              style: const TextStyle(
                  color: Colors.black, fontWeight: FontWeight.bold),
            ),
            duration: const Duration(seconds: 3),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              backgroundColor: AppColors.bearish,
              content: Text('เพิ่มสินทรัพย์ไม่สำเร็จ: $e')),
        );
      }
    }
  }

  Future<void> _removeWatchlistItem(String symbol) async {
    // Optimistic UI removal
    setState(() {
      _watchlist.removeWhere((item) =>
          (item['symbol'] ?? '').toString().trim().toUpperCase() ==
          symbol.trim().toUpperCase());
    });
    try {
      final dio = AppApi.dio;
      final encoded = Uri.encodeComponent(symbol);
      await dio.delete(
        AppApi.url('/api/v1/settings/watchlist/$encoded'),
        queryParameters: {'symbol': symbol},
      );
      _fetchWatchlist();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text('🗑️ ลบ $symbol ออกจากรายการสำเร็จ',
                style: const TextStyle(
                    color: Colors.black, fontWeight: FontWeight.bold)),
            duration: const Duration(seconds: 2),
          ),
        );
      }
    } catch (e) {
      _fetchWatchlist();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              backgroundColor: AppColors.bearish,
              content: Text('ลบไม่สำเร็จ: $e')),
        );
      }
    }
  }

  Future<void> _resetDefaultWatchlist() async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF1E2533),
        title: const Text('รีเซ็ต Watchlist เป็นค่าเริ่มต้น?',
            style: TextStyle(
                color: Colors.white,
                fontSize: 16,
                fontWeight: FontWeight.bold)),
        content: const Text(
            'ต้องการรีเซ็ตรายการเฝ้าสแกนกลับเป็น 8 สินทรัพย์หลัก (BTC, ETH, SOL, XAUUSD, EURUSD, AAPL, TSLA, NVDA) ใช่หรือไม่?',
            style: TextStyle(color: Colors.white70, fontSize: 13)),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('ยกเลิก',
                  style: TextStyle(color: Colors.white54))),
          ElevatedButton(
            style: ElevatedButton.styleFrom(
                backgroundColor: const Color(0xFF2E82FE)),
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('รีเซ็ตค่า',
                style: TextStyle(
                    color: Colors.white, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
    if (confirm != true) return;

    try {
      final dio = AppApi.dio;
      await dio.post(AppApi.url('/api/v1/settings/watchlist/reset-default'));
      await _fetchWatchlist();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text('🔄 รีเซ็ต Watchlist เป็น 8 สินทรัพย์หลักเรียบร้อย',
                style: TextStyle(
                    color: Colors.black, fontWeight: FontWeight.bold)),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              backgroundColor: AppColors.bearish,
              content: Text('เกิดข้อผิดพลาด: $e')),
        );
      }
    }
  }

  void _showAddAssetDialog() {
    final customSymCtrl = TextEditingController();
    final searchCtrl = TextEditingController();
    String activeCategory = 'innovestx_thb';
    String selectedTf = '1h';
    String customMarketType = 'crypto';

    final selectedItems = <Map<String, dynamic>>{};
    bool isLoadingCatalog = true;
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
          if (isLoadingCatalog) {
            AppApi.dio
                .get(AppApi.url('/api/v1/settings/assets/catalog'))
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

          final existingNormSymbols = _watchlist
              .map((e) => (e['symbol'] ?? '')
                  .toString()
                  .replaceAll('/', '')
                  .replaceAll('-', '')
                  .toUpperCase())
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
                          'เลือกสินทรัพย์เข้า Watchlist',
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
                      _catChip(
                          '🟣 InnovestX (THB)',
                          'innovestx_thb',
                          activeCategory,
                          (cat) => setModalState(() => activeCategory = cat)),
                      const SizedBox(width: 6),
                      _catChip(
                          '🌐 Crypto (USDT)',
                          'crypto_global',
                          activeCategory,
                          (cat) => setModalState(() => activeCategory = cat)),
                      const SizedBox(width: 6),
                      _catChip(
                          '💱 Forex & Gold',
                          'forex_metals',
                          activeCategory,
                          (cat) => setModalState(() => activeCategory = cat)),
                      const SizedBox(width: 6),
                      _catChip('📈 US Stocks', 'stocks', activeCategory,
                          (cat) => setModalState(() => activeCategory = cat)),
                      const SizedBox(width: 6),
                      _catChip('✍️ กำหนดเอง (Custom)', 'custom', activeCategory,
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

                  // Bottom Action Button
                  const SizedBox(height: 8),
                  SizedBox(
                    width: double.infinity,
                    child: ElevatedButton.icon(
                      onPressed: selectedItems.isEmpty
                          ? null
                          : () {
                              final listToSubmit = selectedItems
                                  .map((it) => {
                                        ...it,
                                        'timeframe': selectedTf,
                                        'htf_timeframe':
                                            selectedTf == '1d' ? '1w' : '4h',
                                      })
                                  .toList();
                              Navigator.pop(ctx);
                              _addBatchWatchlistItems(listToSubmit);
                            },
                      icon: const Icon(Icons.add_task, size: 18),
                      label: Text(
                        selectedItems.isEmpty
                            ? 'แตะเลือกเหรียญที่ต้องการเพิ่ม'
                            : '➕ เพิ่มสินทรัพย์ที่เลือก (${selectedItems.length} รายการ - TF $selectedTf)',
                        style: const TextStyle(
                            fontWeight: FontWeight.bold, fontSize: 13),
                      ),
                      style: ElevatedButton.styleFrom(
                        backgroundColor: AppColors.bullish,
                        foregroundColor: Colors.black,
                        disabledBackgroundColor: Colors.white10,
                        disabledForegroundColor: Colors.white30,
                        padding: const EdgeInsets.symmetric(vertical: 12),
                        shape: RoundedRectangleBorder(
                            borderRadius: BorderRadius.circular(8)),
                      ),
                    ),
                  ),
                ] else ...[
                  // Custom input tab
                  Expanded(
                    child: SingleChildScrollView(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const SizedBox(height: 8),
                          TextField(
                            controller: customSymCtrl,
                            textCapitalization: TextCapitalization.characters,
                            style: const TextStyle(
                                color: Colors.white,
                                fontWeight: FontWeight.bold),
                            decoration: const InputDecoration(
                              labelText:
                                  'สัญลักษณ์คู่สินทรัพย์ (Symbol / Ticker)',
                              hintText: 'เช่น KUB/THB, SOL/USDT, GBPJPY, AMD',
                              prefixIcon: Icon(Icons.edit,
                                  color: Colors.white54, size: 18),
                            ),
                          ),
                          const SizedBox(height: 14),
                          DropdownButtonFormField<String>(
                            initialValue: customMarketType,
                            dropdownColor: const Color(0xFF1B2333),
                            style: const TextStyle(color: Colors.white),
                            decoration: const InputDecoration(
                                labelText: 'ประเภทตลาด (Market Type)'),
                            items: const [
                              DropdownMenuItem(
                                  value: 'crypto',
                                  child: Text(
                                      '🟣 InnovestX / Crypto (THB & USDT)')),
                              DropdownMenuItem(
                                  value: 'forex',
                                  child: Text('💱 Forex & Gold (MT5)')),
                              DropdownMenuItem(
                                  value: 'stock',
                                  child: Text('📈 Stocks (US Equities)')),
                            ],
                            onChanged: (v) => setModalState(
                                () => customMarketType = v ?? 'crypto'),
                          ),
                          const SizedBox(height: 14),
                          DropdownButtonFormField<String>(
                            initialValue: selectedTf,
                            dropdownColor: const Color(0xFF1B2333),
                            style: const TextStyle(color: Colors.white),
                            decoration: const InputDecoration(
                                labelText: 'Timeframe สแกน'),
                            items: const [
                              DropdownMenuItem(
                                  value: '15m',
                                  child: Text('15 Minutes (15M)')),
                              DropdownMenuItem(
                                  value: '1h', child: Text('1 Hour (1H)')),
                              DropdownMenuItem(
                                  value: '4h', child: Text('4 Hours (4H)')),
                              DropdownMenuItem(
                                  value: '1d', child: Text('1 Day (1D)')),
                            ],
                            onChanged: (v) =>
                                setModalState(() => selectedTf = v ?? '1h'),
                          ),
                          const SizedBox(height: 24),
                          SizedBox(
                            width: double.infinity,
                            child: ElevatedButton.icon(
                              onPressed: () {
                                final s =
                                    customSymCtrl.text.trim().toUpperCase();
                                if (s.isNotEmpty) {
                                  Navigator.pop(ctx);
                                  _addWatchlistItem(
                                      s, customMarketType, selectedTf);
                                }
                              },
                              icon: const Icon(Icons.check_circle, size: 18),
                              label: const Text(
                                  'บันทึกคู่เหรียญนี้เข้า Watchlist',
                                  style:
                                      TextStyle(fontWeight: FontWeight.bold)),
                              style: ElevatedButton.styleFrom(
                                backgroundColor: AppColors.bullish,
                                foregroundColor: Colors.black,
                                padding:
                                    const EdgeInsets.symmetric(vertical: 12),
                                shape: RoundedRectangleBorder(
                                    borderRadius: BorderRadius.circular(8)),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ],
            ),
          );
        },
      ),
    );
  }

  Widget _catChip(
      String label, String cat, String activeCat, Function(String) onSelect) {
    final isSel = activeCat == cat;
    return GestureDetector(
      onTap: () => onSelect(cat),
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
        decoration: BoxDecoration(
          color: isSel
              ? const Color(0xFF2E82FE).withValues(alpha: 0.25)
              : const Color(0xFF1E2533),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: isSel ? const Color(0xFF2E82FE) : const Color(0xFF232A38),
            width: isSel ? 1.2 : 0.8,
          ),
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
    );
  }

  @override
  void dispose() {
    _aiTabController.dispose();
    _brokerTabController.dispose();
    _apiUrlCtrl.dispose();
    _backendApiKeyCtrl.dispose();
    _lmEndpointCtrl.dispose();
    _lmModelCtrl.dispose();
    _geminiKeyCtrl.dispose();
    _geminiModelCtrl.dispose();
    _openRouterKeyCtrl.dispose();
    _openRouterModelCtrl.dispose();
    _telegramTokenCtrl.dispose();
    _telegramChatIdCtrl.dispose();
    _lineTokenCtrl.dispose();
    _innovestxKeyCtrl.dispose();
    _innovestxSecretCtrl.dispose();
    _watchlistFilterCtrl.dispose();

    _mt5LoginCtrl.dispose();
    _mt5PasswordCtrl.dispose();
    _mt5ServerCtrl.dispose();
    _mt5PathCtrl.dispose();
    _binanceKeyCtrl.dispose();
    _binanceSecretCtrl.dispose();
    _bybitKeyCtrl.dispose();
    _bybitSecretCtrl.dispose();
    _alpacaKeyCtrl.dispose();
    _alpacaSecretCtrl.dispose();
    _alpacaBaseUrlCtrl.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final apiUrl = _apiUrlCtrl.text.trim();
    final providers = ['local', 'gemini', 'openrouter'];
    final selectedProvider = providers[_aiTabController.index];

    final current = ref.read(settingsProvider);
    final newState = current.copyWith(
      apiBaseUrl: apiUrl,
      aiProvider: selectedProvider,
      lmStudioEndpoint: _lmEndpointCtrl.text.trim(),
      lmStudioModel: _lmModelCtrl.text.trim(),
      geminiKey: _geminiKeyCtrl.text.trim(),
      geminiModel: _geminiModelCtrl.text.trim(),
      openRouterKey: _openRouterKeyCtrl.text.trim(),
      openRouterModel: _openRouterModelCtrl.text.trim(),
      telegramToken: _telegramTokenCtrl.text.trim(),
      telegramChatId: _telegramChatIdCtrl.text.trim(),
      lineToken: _lineTokenCtrl.text.trim(),
    );

    final backendApiKey = _backendApiKeyCtrl.text.trim();
    if (backendApiKey.length < 32) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            backgroundColor: AppColors.bearish,
            content: Text('Backend API Key ต้องยาวอย่างน้อย 32 ตัวอักษร'),
          ),
        );
      }
      return;
    }
    try {
      await ref.read(settingsProvider.notifier).save(newState);
      await ApiConfig.setApiKey(backendApiKey);
      await _saveBrokerCredentials();
      AppApi.clearApiKeyCache();
      await AppWebSocketClient.instance.reconnect();

      final dio = AppApi.dio;

      // Save LLM configuration
      await dio.post(
        AppApi.url('/api/v1/settings/llm/config'),
        data: {
          'provider': selectedProvider,
          'local_endpoint': _lmEndpointCtrl.text.trim(),
          'local_model': _lmModelCtrl.text.trim(),
          'gemini_key': _geminiKeyCtrl.text.trim(),
          'gemini_api_key': _geminiKeyCtrl.text.trim(),
          'gemini_model': _geminiModelCtrl.text.trim(),
          'openrouter_key': _openRouterKeyCtrl.text.trim(),
          'openrouter_api_key': _openRouterKeyCtrl.text.trim(),
          'openrouter_model': _openRouterModelCtrl.text.trim(),
        },
      );

      // Save Broker configuration
      await dio.post(
        AppApi.url('/api/v1/settings/brokers/config'),
        data: {
          'innovestx_api_key': _innovestxKeyCtrl.text.trim(),
          'innovestx_api_secret': _innovestxSecretCtrl.text.trim(),
          'mt5_login': int.tryParse(_mt5LoginCtrl.text.trim()) ?? 0,
          'mt5_password': _mt5PasswordCtrl.text.trim(),
          'mt5_server': _mt5ServerCtrl.text.trim(),
          'mt5_path': _mt5PathCtrl.text.trim(),
          'binance_api_key': _binanceKeyCtrl.text.trim(),
          'binance_api_secret': _binanceSecretCtrl.text.trim(),
          'bybit_api_key': _bybitKeyCtrl.text.trim(),
          'bybit_api_secret': _bybitSecretCtrl.text.trim(),
          'alpaca_api_key': _alpacaKeyCtrl.text.trim(),
          'alpaca_api_secret': _alpacaSecretCtrl.text.trim(),
          'alpaca_base_url': _alpacaBaseUrlCtrl.text.trim(),
        },
      );

      // Save Position & Risk settings
      await dio.post(
        AppApi.url('/api/v1/settings/risk/config'),
        data: {
          'entry_mode': current.entryMode,
          'auto_sl_tp': current.autoSlTp,
          'auto_invalidation': current.autoInvalidation,
          'risk_per_trade': current.riskPerTrade,
          'max_daily_loss': current.maxDailyLoss,
          'max_open_positions': current.maxPositions,
          'target_rr': current.targetRr,
          'default_sl_pct': current.defaultSlPct,
        },
      );
    } catch (error) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bearish,
            content: Text(
              'บันทึกการตั้งค่าไม่ครบ: ${_requestErrorMessage(error)} กรุณาตรวจสอบแล้วกดบันทึกอีกครั้ง',
            ),
          ),
        );
      }
      return;
    }

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
              '✅ บันทึกการตั้งค่าทั้งหมดเรียบร้อยแล้ว (${apiUrl.isNotEmpty ? apiUrl : AppApi.baseUrl})'),
          backgroundColor: AppColors.bullish,
          duration: const Duration(seconds: 3),
        ),
      );
    }
  }

  Future<void> _testConnection(String label) async {
    final isTg = label.toLowerCase().contains('telegram');
    final isLine = label.toLowerCase().contains('line');
    final isApi = label.toLowerCase().contains('backend') ||
        label.toLowerCase().contains('api');
    final isLLM = !isTg && !isLine && !isApi;

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Testing $label connection...'),
        duration: const Duration(seconds: 2),
      ),
    );

    try {
      final dio = AppApi.dio;
      if (isApi) {
        final targetUrl = _apiUrlCtrl.text.trim();
        final apiKey = _backendApiKeyCtrl.text.trim();
        if (apiKey.length < 32) {
          throw const FormatException(
              'Backend API Key ต้องยาวอย่างน้อย 32 ตัวอักษร');
        }
        if (targetUrl.isNotEmpty) {
          AppApi.setBaseUrl(targetUrl);
        }
        final resp = await dio.get(AppApi.url('/health'));
        final authResp = await dio.get(
          AppApi.url('/api/v1/settings/llm/config'),
          options: Options(headers: {'X-API-Key': apiKey}),
        );
        final ok = resp.data['status'] == 'ok' && authResp.statusCode == 200;
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              backgroundColor: ok ? AppColors.bullish : AppColors.bearish,
              content: Text(
                ok
                    ? '✅ Backend API Connected! (v${resp.data['version']})'
                    : '⚠️ Connection failed',
                style: TextStyle(
                    color: ok ? Colors.black : Colors.white,
                    fontWeight: FontWeight.bold),
              ),
            ),
          );
        }
      } else if (isLLM) {
        String provider = 'local';
        String? endpoint = _lmEndpointCtrl.text.trim();
        String? model = _lmModelCtrl.text.trim();
        String? apiKey;

        if (label.toLowerCase().contains('gemini')) {
          provider = 'gemini';
          endpoint = null;
          model = _geminiModelCtrl.text.trim();
          apiKey = _geminiKeyCtrl.text.trim();
        } else if (label.toLowerCase().contains('openrouter')) {
          provider = 'openrouter';
          endpoint = null;
          model = _openRouterModelCtrl.text.trim();
          apiKey = _openRouterKeyCtrl.text.trim();
        }

        final resp = await dio.post(
          AppApi.url('/api/v1/settings/llm/test'),
          data: {
            'provider': provider,
            'endpoint': endpoint,
            'model': model,
            'api_key': apiKey,
          },
        );

        final ok = resp.data['ok'] == true;
        final latency = resp.data['latency_ms'] ?? 0;
        final error = resp.data['error'] ?? 'Connection failed';
        final usedModel = resp.data['model'] ?? model;

        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              backgroundColor: ok ? AppColors.bullish : AppColors.bearish,
              content: Text(
                ok
                    ? '✅ $label Connected! Model: $usedModel (${latency}ms)'
                    : '⚠️ $label Test Failed: $error',
                style: TextStyle(
                    color: ok ? Colors.black : Colors.white,
                    fontWeight: FontWeight.bold),
              ),
              duration: const Duration(seconds: 4),
            ),
          );
        }
      } else {
        final resp = await dio.post(
          AppApi.url('/api/v1/settings/notifications/test'),
          data: {
            'telegram_bot_token': _telegramTokenCtrl.text.trim(),
            'telegram_chat_id': _telegramChatIdCtrl.text.trim(),
            'line_notify_token': _lineTokenCtrl.text.trim(),
          },
        );

        final results = resp.data['results'] as Map<String, dynamic>? ?? {};
        final ok = isTg
            ? (results['telegram'] == true)
            : (isLine ? (results['line'] == true) : true);

        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              backgroundColor: ok ? AppColors.bullish : AppColors.bearish,
              content: Text(
                ok
                    ? '✅ $label test alert sent successfully!'
                    : '⚠️ $label test failed. Please check your token/chat ID.',
                style: TextStyle(
                    color: ok ? Colors.black : Colors.white,
                    fontWeight: FontWeight.bold),
              ),
            ),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bearish,
            content: Text(
              'ทดสอบการเชื่อมต่อไม่สำเร็จ: ${_requestErrorMessage(e)}',
              style: const TextStyle(
                  color: Colors.white, fontWeight: FontWeight.bold),
            ),
            duration: const Duration(seconds: 5),
          ),
        );
      }
    }
  }

  Future<void> _testBrokerConnection(String brokerType) async {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Testing ${brokerType.toUpperCase()} connection...'),
        duration: const Duration(seconds: 2),
      ),
    );

    try {
      final dio = AppApi.dio;
      final resp = await dio.post(
        AppApi.url('/api/v1/settings/brokers/test'),
        data: {
          'broker_type': brokerType,
          'login': int.tryParse(_mt5LoginCtrl.text.trim()),
          'server': _mt5ServerCtrl.text.trim(),
          'password': _mt5PasswordCtrl.text.trim(),
          'api_key': brokerType == 'innovestx'
              ? _innovestxKeyCtrl.text.trim()
              : (brokerType == 'binance'
                  ? _binanceKeyCtrl.text.trim()
                  : (brokerType == 'bybit'
                      ? _bybitKeyCtrl.text.trim()
                      : _alpacaKeyCtrl.text.trim())),
          'api_secret': brokerType == 'innovestx'
              ? _innovestxSecretCtrl.text.trim()
              : (brokerType == 'binance'
                  ? _binanceSecretCtrl.text.trim()
                  : (brokerType == 'bybit'
                      ? _bybitSecretCtrl.text.trim()
                      : _alpacaSecretCtrl.text.trim())),
          'base_url':
              brokerType == 'alpaca' ? _alpacaBaseUrlCtrl.text.trim() : null,
        },
      );

      final ok = resp.data['status'] == 'ok';
      final msg = resp.data['message']?.toString() ?? 'Tested';

      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: ok ? AppColors.bullish : AppColors.bearish,
            content: Text(
              msg,
              style: TextStyle(
                  color: ok ? Colors.black : Colors.white,
                  fontWeight: FontWeight.bold),
            ),
            duration: const Duration(seconds: 4),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              backgroundColor: AppColors.bearish,
              content: Text('Connection test failed: $e')),
        );
      }
    }
  }

  Future<void> _checkInnovestxBalances() async {
    if (!AppApi.hasActiveLiveSession) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          backgroundColor: AppColors.bearish,
          content: Text(
              'ยอดเงินจริงดูได้เฉพาะระหว่าง Live Session กรุณากด Live และผ่าน preflight ก่อน'),
        ),
      );
      return;
    }
    final key = _innovestxKeyCtrl.text.trim();
    final secret = _innovestxSecretCtrl.text.trim();

    showDialog(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: const Color(0xFF1A1A2E),
        title: const Row(
          children: [
            Icon(Icons.account_balance_wallet, color: Color(0xFF9B59B6)),
            SizedBox(width: 8),
            Text('InnovestX Balances',
                style: TextStyle(color: Colors.white, fontSize: 16)),
          ],
        ),
        content: FutureBuilder(
          future: AppApi.dio.post(
            AppApi.url('/api/v1/trades/broker/innovestx/balances'),
            data: {
              if (key.isNotEmpty) 'api_key': key,
              if (secret.isNotEmpty) 'api_secret': secret,
            },
          ),
          builder: (ctx, snap) {
            if (snap.connectionState == ConnectionState.waiting) {
              return const SizedBox(
                height: 100,
                child: Center(
                    child: CircularProgressIndicator(color: Color(0xFF9B59B6))),
              );
            }
            if (snap.hasError || snap.data?.statusCode != 200) {
              return Text(
                  'Failed to load balances: ${snap.error ?? snap.data?.statusMessage}',
                  style: const TextStyle(color: AppColors.bearish));
            }
            final data = snap.data?.data?['data'] as List<dynamic>? ?? [];
            if (data.isEmpty) {
              return const Padding(
                padding: EdgeInsets.all(16.0),
                child: Text('No assets or zero balance.',
                    style: TextStyle(color: Colors.white70)),
              );
            }
            return SizedBox(
              width: double.maxFinite,
              height: 300,
              child: ListView.builder(
                shrinkWrap: true,
                itemCount: data.length,
                itemBuilder: (c, i) {
                  final item = data[i];
                  final product = item['product'] ?? '';
                  final amount =
                      double.tryParse(item['amount']?.toString() ?? '0') ?? 0.0;
                  final hold =
                      double.tryParse(item['hold']?.toString() ?? '0') ?? 0.0;
                  return ListTile(
                    dense: true,
                    title: Text(product,
                        style: const TextStyle(
                            fontWeight: FontWeight.bold, color: Colors.white)),
                    subtitle: Text('Hold: $hold',
                        style: const TextStyle(
                            fontSize: 11, color: Colors.white54)),
                    trailing: Text(
                      amount.toStringAsFixed(product == 'THB' ? 2 : 4),
                      style: const TextStyle(
                          color: AppColors.bullish,
                          fontWeight: FontWeight.bold,
                          fontSize: 14),
                    ),
                  );
                },
              ),
            );
          },
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child:
                const Text('Close', style: TextStyle(color: AppColors.bullish)),
          ),
        ],
      ),
    );
  }

  Future<void> _clearBrokerCredentials(String broker) async {
    final confirm = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        backgroundColor: AppColors.surface,
        title: Row(
          children: [
            const Icon(Icons.warning_amber_rounded,
                color: AppColors.bearish, size: 22),
            const SizedBox(width: 8),
            Text('ล้างการเชื่อมต่อ $broker?',
                style: const TextStyle(
                    color: Colors.white,
                    fontSize: 16,
                    fontWeight: FontWeight.bold)),
          ],
        ),
        content: Text(
          'คุณต้องการลบ API Key และ Secret ของ $broker ออกจากระบบหรือไม่?\n\nสถานะการเชื่อมต่อในหน้า Journal จะถูกตัดการเชื่อมต่อทันที',
          style:
              const TextStyle(color: Colors.white70, fontSize: 13, height: 1.4),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child:
                const Text('ยกเลิก', style: TextStyle(color: Colors.white60)),
          ),
          ElevatedButton(
            onPressed: () => Navigator.pop(ctx, true),
            style: ElevatedButton.styleFrom(
                backgroundColor: AppColors.bearish,
                foregroundColor: Colors.white),
            child: const Text('ล้างข้อมูล / ตัดการเชื่อมต่อ'),
          ),
        ],
      ),
    );

    if (confirm != true) return;

    try {
      final dio = AppApi.dio;
      await dio.delete(AppApi.url('/api/v1/settings/brokers/config/$broker'));
      if (broker == 'innovestx') {
        await _secureStorage.delete(key: 'broker_innovestx_key');
        await _secureStorage.delete(key: 'broker_innovestx_secret');
        _innovestxKeyCtrl.clear();
        _innovestxSecretCtrl.clear();
      } else if (broker == 'binance') {
        await _secureStorage.delete(key: 'broker_binance_key');
        await _secureStorage.delete(key: 'broker_binance_secret');
        _binanceKeyCtrl.clear();
        _binanceSecretCtrl.clear();
      } else if (broker == 'bybit') {
        await _secureStorage.delete(key: 'broker_bybit_key');
        await _secureStorage.delete(key: 'broker_bybit_secret');
        _bybitKeyCtrl.clear();
        _bybitSecretCtrl.clear();
      } else if (broker == 'mt5') {
        await _secureStorage.delete(key: 'broker_mt5_login');
        await _secureStorage.delete(key: 'broker_mt5_password');
        await _secureStorage.delete(key: 'broker_mt5_server');
        await _secureStorage.delete(key: 'broker_mt5_path');
        _mt5LoginCtrl.clear();
        _mt5PasswordCtrl.clear();
        _mt5ServerCtrl.clear();
        _mt5PathCtrl.clear();
      } else if (broker == 'alpaca') {
        await _secureStorage.delete(key: 'broker_alpaca_key');
        await _secureStorage.delete(key: 'broker_alpaca_secret');
        await _secureStorage.delete(key: 'broker_alpaca_base_url');
        _alpacaKeyCtrl.clear();
        _alpacaSecretCtrl.clear();
        _alpacaBaseUrlCtrl.text = 'https://paper-api.alpaca.markets';
      }
      await _loadAllSettings();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text(
                '✅ ล้างข้อมูลการเชื่อมต่อ $broker เรียบร้อยแล้ว (ตัดการเชื่อมต่อ)',
                style: const TextStyle(
                    color: Colors.black, fontWeight: FontWeight.bold)),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
              backgroundColor: AppColors.bearish,
              content: Text('❌ เกิดข้อผิดพลาด: $e')),
        );
      }
    }
  }

  Widget _buildBrokerStatusBanner({
    required String brokerTitle,
    required String brokerKey,
    required bool isConfigured,
    String? maskedKey,
    required Color color,
  }) {
    if (isConfigured) {
      return Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 8),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.12),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: color.withValues(alpha: 0.4), width: 1),
        ),
        child: Row(
          children: [
            Icon(Icons.verified, color: color, size: 18),
            const SizedBox(width: 8),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('สถานะ: บันทึกข้อมูล $brokerTitle ในระบบแล้ว',
                      style: TextStyle(
                          color: color,
                          fontWeight: FontWeight.bold,
                          fontSize: 11)),
                  if (maskedKey != null && maskedKey.isNotEmpty)
                    Text(
                      'ID: $maskedKey',
                      style: const TextStyle(
                          color: Colors.white70,
                          fontSize: 10,
                          fontFamily: 'monospace'),
                    ),
                ],
              ),
            ),
            InkWell(
              onTap: () => _clearBrokerCredentials(brokerKey),
              borderRadius: BorderRadius.circular(4),
              child: Container(
                padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                decoration: BoxDecoration(
                  color: AppColors.bearish.withValues(alpha: 0.2),
                  borderRadius: BorderRadius.circular(4),
                  border: Border.all(
                      color: AppColors.bearish.withValues(alpha: 0.4),
                      width: 0.8),
                ),
                child: const Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Icon(Icons.delete_outline,
                        size: 12, color: AppColors.bearish),
                    SizedBox(width: 2),
                    Text('ล้าง Key',
                        style: TextStyle(
                            color: AppColors.bearish,
                            fontSize: 10,
                            fontWeight: FontWeight.bold)),
                  ],
                ),
              ),
            ),
          ],
        ),
      );
    } else {
      return Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 7),
        decoration: BoxDecoration(
          color: const Color(0xFF1E2230),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(color: const Color(0xFF2E384D)),
        ),
        child: const Row(
          children: [
            Icon(Icons.link_off, color: Colors.white38, size: 14),
            SizedBox(width: 6),
            Expanded(
              child: Text(
                '⚪ ยังไม่ได้เชื่อมต่อ API (กรอกข้อมูลด้านล่างเพื่อเชื่อมต่อ)',
                style: TextStyle(color: Colors.white54, fontSize: 10),
              ),
            ),
          ],
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(settingsProvider);
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: Row(
          children: [
            const Text('Settings'),
            const SizedBox(width: 10),
            Container(
              padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
              decoration: BoxDecoration(
                color: AppColors.bullish.withValues(alpha: 0.15),
                borderRadius: BorderRadius.circular(6),
                border: Border.all(
                    color: AppColors.bullish.withValues(alpha: 0.4), width: 1),
              ),
              child: const Text(
                AppConstants.fullVersion,
                style: TextStyle(
                  fontSize: 11,
                  color: AppColors.bullish,
                  fontWeight: FontWeight.bold,
                  letterSpacing: 0.3,
                ),
              ),
            ),
          ],
        ),
        backgroundColor: AppColors.surface,
        actions: [
          TextButton.icon(
            onPressed: _save,
            icon: const Icon(Icons.save, color: AppColors.bullish),
            label:
                const Text('Save', style: TextStyle(color: AppColors.bullish)),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // ---- Backend Connection ----
          _sectionHeader('🔌 Backend Connection'),
          _card([
            _textField('API Base URL', _apiUrlCtrl,
                hint: 'http://192.168.1.40:8000'),
            const SizedBox(height: 8),
            _textField('Backend API Key', _backendApiKeyCtrl,
                hint: 'คีย์เดียวกับ APP_SECRET_KEY ใน backend/.env',
                obscure: true),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: () => _testConnection('Backend API'),
                icon: const Icon(Icons.wifi_tethering, size: 16),
                label: const Text('Test Connection'),
                style: OutlinedButton.styleFrom(
                    foregroundColor: AppColors.bullish),
              ),
            ),
          ]),

          const SizedBox(height: 16),

          // ---- AI Provider ----
          _sectionHeader('🤖 AI Provider'),
          _card([
            TabBar(
              controller: _aiTabController,
              tabs: const [
                Tab(text: 'OpenAI'),
                Tab(text: 'Gemini'),
                Tab(text: 'OpenRouter'),
              ],
              labelColor: AppColors.bullish,
              unselectedLabelColor: Colors.white54,
              indicatorColor: AppColors.bullish,
            ),
            SizedBox(
              height: 200,
              child: TabBarView(
                controller: _aiTabController,
                children: [
                  // OpenAI tab (Ollama / LM Studio / OpenAI)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Column(
                      children: [
                        _textField('Endpoint (Ollama / LM Studio / OpenAI)',
                            _lmEndpointCtrl,
                            hint:
                                'http://10.0.2.2:11434 หรือ http://10.0.2.2:1234/v1'),
                        const SizedBox(height: 8),
                        _textField('Model Name', _lmModelCtrl,
                            hint: 'gpt-oss:120b-cloud หรือ gpt-4o-mini'),
                        const SizedBox(height: 8),
                        _testBtn('OpenAI'),
                      ],
                    ),
                  ),
                  // Gemini tab
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Column(
                      children: [
                        _textField('API Key', _geminiKeyCtrl,
                            hint: 'AIza...', obscure: true),
                        const SizedBox(height: 8),
                        _textField('Model', _geminiModelCtrl,
                            hint: 'gemini-1.5-pro'),
                        const SizedBox(height: 8),
                        _testBtn('Gemini'),
                      ],
                    ),
                  ),
                  // OpenRouter tab
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Column(
                      children: [
                        _textField('API Key', _openRouterKeyCtrl,
                            hint: 'sk-or-...', obscure: true),
                        const SizedBox(height: 8),
                        _textField('Model', _openRouterModelCtrl,
                            hint: 'google/gemini-pro-1.5'),
                        const SizedBox(height: 8),
                        _testBtn('OpenRouter'),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ]),

          const SizedBox(height: 16),

          // ---- Prompt Management ----
          _sectionHeader('📝 Prompt Management'),
          _card([
            Row(
              children: [
                const Icon(Icons.description_outlined,
                    color: Colors.white54, size: 18),
                const SizedBox(width: 8),
                const Text('System Prompt',
                    style: TextStyle(color: Colors.white70)),
                const Spacer(),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: AppColors.orderBlock.withValues(alpha: 0.2),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: const Text('v1.0',
                      style:
                          TextStyle(fontSize: 11, color: AppColors.orderBlock)),
                ),
              ],
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _openPromptEditor,
                    icon: const Icon(Icons.edit, size: 16),
                    label: const Text('Edit Prompt'),
                    style: OutlinedButton.styleFrom(
                        foregroundColor: Colors.white70),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _testPrompt,
                    icon: const Icon(Icons.play_arrow, size: 16),
                    label: const Text('Test Prompt'),
                    style: OutlinedButton.styleFrom(
                        foregroundColor: AppColors.bullish),
                  ),
                ),
              ],
            ),
          ]),

          const SizedBox(height: 16),

          // ---- Risk & Position Management ----
          _sectionHeader('🛡️ Position & Risk Management'),
          _card([
            const Text(
              'รูปแบบจุดเข้า (SMC Entry Style):',
              style: TextStyle(
                  fontSize: 13,
                  color: Colors.white70,
                  fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: ChoiceChip(
                    label: const Center(
                      child: Text('Limit Zone (OB/FVG)',
                          style: TextStyle(fontSize: 12)),
                    ),
                    selected: settings.entryMode == 'limit',
                    selectedColor: AppColors.bullish,
                    backgroundColor: const Color(0xFF252540),
                    onSelected: (selected) {
                      if (selected) {
                        ref
                            .read(settingsProvider.notifier)
                            .save(settings.copyWith(entryMode: 'limit'));
                      }
                    },
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: ChoiceChip(
                    label: const Center(
                      child: Text('Market (Live Price)',
                          style: TextStyle(fontSize: 12)),
                    ),
                    selected: settings.entryMode == 'market',
                    selectedColor: AppColors.bullish,
                    backgroundColor: const Color(0xFF252540),
                    onSelected: (selected) {
                      if (selected) {
                        ref
                            .read(settingsProvider.notifier)
                            .save(settings.copyWith(entryMode: 'market'));
                      }
                    },
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              settings.entryMode == 'limit'
                  ? '💡 โหมด Limit: ตรึงราคาเข้าไว้ที่โซน Order Block / FVG (ราคาคงที่ไม่เปลี่ยนตาม Tick)'
                  : '⚡ โหมด Market: จุดเข้าอิงตามราคาตลาดปัจจุบัน เข้าทันทีเมื่อยืนยันสัญญาณ',
              style: const TextStyle(fontSize: 11, color: Colors.white38),
            ),
            const Divider(color: Colors.white12, height: 24),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Auto Stop Loss & Take Profit',
                  style: TextStyle(
                      fontSize: 14,
                      color: Colors.white,
                      fontWeight: FontWeight.w600)),
              subtitle: const Text(
                  'สั่งปิดออเดอร์ตัดขาดทุน/ทำกำไรอัตโนมัติเมื่อราคาแตะเส้น SL หรือ TP',
                  style: TextStyle(fontSize: 11, color: Colors.white54)),
              value: settings.autoSlTp,
              activeThumbColor: AppColors.bullish,
              onChanged: (v) async {
                ref
                    .read(settingsProvider.notifier)
                    .save(settings.copyWith(autoSlTp: v));
                try {
                  await AppApi.dio.post(AppApi.url('/api/v1/settings/runtime'),
                      data: {'auto_sl_tp': v});
                } catch (_) {}
              },
            ),
            const Divider(color: Colors.white12, height: 16),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Auto Invalidation Cut-Loss',
                  style: TextStyle(
                      fontSize: 14,
                      color: Colors.white,
                      fontWeight: FontWeight.w600)),
              subtitle: const Text(
                  'ตัดขาดทุนทันทีเมื่อโครงสร้างตลาดกลับตัวฝั่งตรงข้าม (เช่น เกิด CHoCH สวนทาง)',
                  style: TextStyle(fontSize: 11, color: Colors.white54)),
              value: settings.autoInvalidation,
              activeThumbColor: AppColors.bullish,
              onChanged: (v) async {
                ref
                    .read(settingsProvider.notifier)
                    .save(settings.copyWith(autoInvalidation: v));
                try {
                  await AppApi.dio.post(AppApi.url('/api/v1/settings/runtime'),
                      data: {'auto_invalidation': v});
                } catch (_) {}
              },
            ),
            const Divider(color: Colors.white12, height: 16),
            _riskSlider(
              label: 'Risk per Trade',
              value: settings.riskPerTrade,
              min: 0.5,
              max: 5.0,
              divisions: 9,
              onChanged: (v) => ref.read(settingsProvider.notifier).save(
                    settings.copyWith(riskPerTrade: v),
                  ),
              suffix: '%',
            ),
            _riskSlider(
              label: 'Target Risk:Reward (R:R)',
              value: settings.targetRr,
              min: 1.0,
              max: 4.0,
              divisions: 6,
              onChanged: (v) => ref.read(settingsProvider.notifier).save(
                    settings.copyWith(targetRr: v),
                  ),
              suffix: 'R',
            ),
            _riskSlider(
              label: 'Default SL Distance',
              value: settings.defaultSlPct,
              min: 0.5,
              max: 3.0,
              divisions: 5,
              onChanged: (v) => ref.read(settingsProvider.notifier).save(
                    settings.copyWith(defaultSlPct: v),
                  ),
              suffix: '%',
            ),
            _riskSlider(
              label: 'Max Daily Loss',
              value: settings.maxDailyLoss,
              min: 1.0,
              max: 10.0,
              divisions: 9,
              onChanged: (v) => ref.read(settingsProvider.notifier).save(
                    settings.copyWith(maxDailyLoss: v),
                  ),
              suffix: '%',
            ),
            _riskSlider(
              label: 'Max Positions',
              value: settings.maxPositions.toDouble(),
              min: 1,
              max: 10,
              divisions: 9,
              onChanged: (v) => ref.read(settingsProvider.notifier).save(
                    settings.copyWith(maxPositions: v.toInt()),
                  ),
              suffix: '',
              isInt: true,
            ),
          ]),

          const SizedBox(height: 16),

          // ---- Trading Mode ----
          _sectionHeader('🎯 Trading Mode'),
          _card([
            Row(
              children: [
                Expanded(
                  child: _modeButton(
                    label: 'Paper Trading',
                    icon: Icons.science_outlined,
                    selected: settings.isPaperMode,
                    color: AppColors.neutral,
                    onTap: () async {
                      await ref
                          .read(settingsProvider.notifier)
                          .deactivateLiveMode();
                      if (context.mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(
                            backgroundColor: AppColors.neutral,
                            content: Text('🧪 Switched to Paper Trading Mode',
                                style: TextStyle(
                                    color: Colors.black,
                                    fontWeight: FontWeight.bold)),
                            duration: Duration(seconds: 2),
                          ),
                        );
                      }
                    },
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: _modeButton(
                    label: 'Live Trading',
                    icon: Icons.bolt,
                    selected: !settings.isPaperMode,
                    color: AppColors.bearish,
                    onTap: settings.isPaperMode ? _confirmLiveMode : null,
                  ),
                ),
              ],
            ),
            if (!settings.isPaperMode)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Row(
                  children: [
                    const Icon(Icons.warning_amber,
                        color: AppColors.bearish, size: 14),
                    const SizedBox(width: 4),
                    const Expanded(
                      child: Text(
                        'LIVE MODE — real funds at risk',
                        style:
                            TextStyle(fontSize: 11, color: AppColors.bearish),
                      ),
                    ),
                    TextButton.icon(
                      onPressed: () async {
                        await ref
                            .read(settingsProvider.notifier)
                            .activateLiveKillSwitch();
                        if (context.mounted) {
                          ScaffoldMessenger.of(context).showSnackBar(
                            const SnackBar(
                              backgroundColor: AppColors.bearish,
                              content: Text(
                                  '🛑 Live Kill Switch ทำงานแล้ว — ทุก Live Session ถูกยกเลิก'),
                            ),
                          );
                        }
                      },
                      icon: const Icon(Icons.stop_circle_outlined, size: 15),
                      label: const Text('KILL SWITCH'),
                      style: TextButton.styleFrom(
                        foregroundColor: AppColors.bearish,
                      ),
                    ),
                  ],
                ),
              ),
          ]),

          const SizedBox(height: 16),

          // ---- Broker & Exchange Accounts ----
          _sectionHeader('🏢 Broker & Exchange Accounts (เชื่อมต่อโบรกเกอร์)'),
          _card([
            TabBar(
              controller: _brokerTabController,
              indicatorColor: AppColors.bullish,
              labelColor: AppColors.bullish,
              unselectedLabelColor: Colors.white38,
              isScrollable: true,
              tabs: const [
                Tab(text: '🟣 InnovestX (TH)'),
                Tab(text: '💱 MetaTrader 5'),
                Tab(text: '🪙 Binance/Bybit'),
                Tab(text: '📈 Alpaca'),
              ],
            ),
            const SizedBox(height: 16),
            SizedBox(
              height: 380,
              child: TabBarView(
                controller: _brokerTabController,
                children: [
                  // 0. InnovestX (Thailand Digital Asset Exchange)
                  SingleChildScrollView(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.verified,
                                color: Color(0xFF9B59B6), size: 16),
                            SizedBox(width: 6),
                            Text(
                                'InnovestX (SCBX) Digital Asset Exchange (THB)',
                                style: TextStyle(
                                    fontWeight: FontWeight.bold,
                                    fontSize: 13,
                                    color: Colors.white)),
                          ],
                        ),
                        const SizedBox(height: 8),
                        _buildBrokerStatusBanner(
                          brokerTitle: 'InnovestX',
                          brokerKey: 'innovestx',
                          isConfigured:
                              _brokerConfig['innovestx_configured'] == true ||
                                  _innovestxKeyCtrl.text.isNotEmpty,
                          maskedKey:
                              _brokerConfig['innovestx_api_key']?.toString(),
                          color: const Color(0xFF9B59B6),
                        ),
                        _textField(
                          'InnovestX API Key (64 chars)',
                          _innovestxKeyCtrl,
                          hint: '3d593da38986... or API Key',
                        ),
                        const SizedBox(height: 10),
                        _textField(
                          'API Secret Key',
                          _innovestxSecretCtrl,
                          hint: '••••••••',
                          obscure: true,
                        ),
                        const SizedBox(height: 12),
                        Row(
                          children: [
                            Expanded(
                              child: OutlinedButton.icon(
                                onPressed: () =>
                                    _testBrokerConnection('innovestx'),
                                icon: const Icon(Icons.cable, size: 16),
                                label: const Text('Test InnovestX'),
                                style: OutlinedButton.styleFrom(
                                    foregroundColor: AppColors.bullish),
                              ),
                            ),
                            const SizedBox(width: 8),
                            Expanded(
                              child: ElevatedButton.icon(
                                onPressed: _checkInnovestxBalances,
                                icon: const Icon(Icons.account_balance_wallet,
                                    size: 16),
                                label: const Text('Check Balance'),
                                style: ElevatedButton.styleFrom(
                                  backgroundColor: const Color(0xFF9B59B6),
                                  foregroundColor: Colors.white,
                                ),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                  // 1. MetaTrader 5 Tab
                  SingleChildScrollView(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.hub_outlined,
                                color: Color(0xFF00E5FF), size: 16),
                            SizedBox(width: 6),
                            Text('MetaTrader 5 Direct Bridge (Forex & Gold)',
                                style: TextStyle(
                                    fontWeight: FontWeight.bold,
                                    fontSize: 13,
                                    color: Colors.white)),
                          ],
                        ),
                        const SizedBox(height: 8),
                        _buildBrokerStatusBanner(
                          brokerTitle: 'MetaTrader 5',
                          brokerKey: 'mt5',
                          isConfigured:
                              _brokerConfig['mt5_configured'] == true ||
                                  _mt5LoginCtrl.text.isNotEmpty,
                          maskedKey: _brokerConfig['mt5_login'] != null &&
                                  _brokerConfig['mt5_login'] != 0
                              ? 'MT5 #${_brokerConfig['mt5_login']} (${_brokerConfig['mt5_server'] ?? ""})'
                              : null,
                          color: const Color(0xFF00E5FF),
                        ),
                        _textField(
                          'MT5 Login / Account ID',
                          _mt5LoginCtrl,
                          hint: 'e.g. 5123984',
                        ),
                        const SizedBox(height: 10),
                        _textField(
                          'MT5 Password',
                          _mt5PasswordCtrl,
                          hint: '••••••••',
                          obscure: true,
                        ),
                        const SizedBox(height: 10),
                        _textField(
                          'MT5 Server Name (Broker)',
                          _mt5ServerCtrl,
                          hint: 'e.g. ICMarketsSC-Demo หรือ Exness-Real',
                        ),
                        const SizedBox(height: 12),
                        Row(
                          children: [
                            Expanded(
                              child: OutlinedButton.icon(
                                onPressed: () => _testBrokerConnection('mt5'),
                                icon: const Icon(Icons.cable, size: 16),
                                label: const Text('Test MT5 Bridge'),
                                style: OutlinedButton.styleFrom(
                                    foregroundColor: AppColors.bullish),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),

                  // 2. Binance / Bybit Tab
                  SingleChildScrollView(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.currency_bitcoin,
                                color: Color(0xFFF0B90B), size: 16),
                            SizedBox(width: 6),
                            Text('Binance / Bybit API Connection (Crypto)',
                                style: TextStyle(
                                    fontWeight: FontWeight.bold,
                                    fontSize: 13,
                                    color: Colors.white)),
                          ],
                        ),
                        const SizedBox(height: 8),
                        _buildBrokerStatusBanner(
                          brokerTitle: 'Binance / Bybit',
                          brokerKey: 'binance',
                          isConfigured:
                              _brokerConfig['binance_configured'] == true ||
                                  _binanceKeyCtrl.text.isNotEmpty,
                          maskedKey:
                              _brokerConfig['binance_api_key']?.toString(),
                          color: const Color(0xFFF0B90B),
                        ),
                        _textField(
                          'Binance / Bybit API Key',
                          _binanceKeyCtrl,
                          hint: 'vmPU... or API Key',
                        ),
                        const SizedBox(height: 10),
                        _textField(
                          'API Secret Key',
                          _binanceSecretCtrl,
                          hint: '••••••••',
                          obscure: true,
                        ),
                        const SizedBox(height: 12),
                        Row(
                          children: [
                            Expanded(
                              child: OutlinedButton.icon(
                                onPressed: () =>
                                    _testBrokerConnection('binance'),
                                icon: const Icon(Icons.cable, size: 16),
                                label: const Text('Test Binance API'),
                                style: OutlinedButton.styleFrom(
                                    foregroundColor: AppColors.bullish),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),

                  // 3. Alpaca Tab
                  SingleChildScrollView(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.trending_up,
                                color: Color(0xFFFFD700), size: 16),
                            SizedBox(width: 6),
                            Text('Alpaca Markets (US Equities & Stocks)',
                                style: TextStyle(
                                    fontWeight: FontWeight.bold,
                                    fontSize: 13,
                                    color: Colors.white)),
                          ],
                        ),
                        const SizedBox(height: 8),
                        _buildBrokerStatusBanner(
                          brokerTitle: 'Alpaca Markets',
                          brokerKey: 'alpaca',
                          isConfigured:
                              _brokerConfig['alpaca_configured'] == true ||
                                  _alpacaKeyCtrl.text.isNotEmpty,
                          maskedKey:
                              _brokerConfig['alpaca_api_key']?.toString(),
                          color: const Color(0xFFFFD700),
                        ),
                        _textField(
                          'Alpaca API Key ID',
                          _alpacaKeyCtrl,
                          hint: 'PK...',
                        ),
                        const SizedBox(height: 10),
                        _textField(
                          'Alpaca Secret Key',
                          _alpacaSecretCtrl,
                          hint: '••••••••',
                          obscure: true,
                        ),
                        const SizedBox(height: 10),
                        _textField(
                          'Alpaca Base URL',
                          _alpacaBaseUrlCtrl,
                          hint: 'https://paper-api.alpaca.markets',
                        ),
                        const SizedBox(height: 12),
                        Row(
                          children: [
                            Expanded(
                              child: OutlinedButton.icon(
                                onPressed: () =>
                                    _testBrokerConnection('alpaca'),
                                icon: const Icon(Icons.cable, size: 16),
                                label: const Text('Test Alpaca API'),
                                style: OutlinedButton.styleFrom(
                                    foregroundColor: AppColors.bullish),
                              ),
                            ),
                          ],
                        ),
                      ],
                    ),
                  ),
                ],
              ),
            ),
          ]),

          const SizedBox(height: 16),

          // ---- Watchlist Management ----
          Row(
            mainAxisAlignment: MainAxisAlignment.spaceBetween,
            children: [
              Row(
                children: [
                  _sectionHeader('📊 Proactive Watchlist'),
                  const SizedBox(width: 8),
                  Container(
                    padding:
                        const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                    decoration: BoxDecoration(
                      color: AppColors.bullish.withValues(alpha: 0.2),
                      borderRadius: BorderRadius.circular(10),
                      border: Border.all(
                          color: AppColors.bullish.withValues(alpha: 0.4)),
                    ),
                    child: Text(
                      '${_watchlist.length} สินทรัพย์',
                      style: const TextStyle(
                          color: AppColors.bullish,
                          fontSize: 11,
                          fontWeight: FontWeight.bold),
                    ),
                  ),
                ],
              ),
              IconButton(
                icon: const Icon(Icons.add_circle,
                    color: AppColors.bullish, size: 22),
                tooltip: 'เพิ่มสินทรัพย์',
                onPressed: _showAddAssetDialog,
              ),
            ],
          ),
          _card([
            // Search / Filter Field
            Container(
              margin: const EdgeInsets.only(bottom: 10),
              padding: const EdgeInsets.symmetric(horizontal: 10),
              decoration: BoxDecoration(
                color: const Color(0xFF1B2333),
                borderRadius: BorderRadius.circular(8),
                border: Border.all(
                    color: const Color(0xFF2E82FE).withValues(alpha: 0.3)),
              ),
              child: TextField(
                controller: _watchlistFilterCtrl,
                onChanged: (_) => setState(() {}),
                style: const TextStyle(color: Colors.white, fontSize: 13),
                decoration: InputDecoration(
                  icon:
                      const Icon(Icons.search, size: 18, color: Colors.white54),
                  hintText: 'ค้นหาเหรียญเพื่อลบ / ดู... (เช่น DOGE, THB, XAU)',
                  hintStyle:
                      const TextStyle(color: Colors.white30, fontSize: 12),
                  border: InputBorder.none,
                  suffixIcon: _watchlistFilterCtrl.text.isNotEmpty
                      ? IconButton(
                          icon: const Icon(Icons.clear,
                              size: 16, color: Colors.white54),
                          onPressed: () =>
                              setState(() => _watchlistFilterCtrl.clear()),
                        )
                      : null,
                ),
              ),
            ),

            if (_watchlist.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text(
                    'ยังไม่มีสินทรัพย์ใน Watchlist — Scanner จะไม่สแกนจนกว่าจะเพิ่มรายการ',
                    style: TextStyle(color: Colors.white38, fontSize: 12)),
              )
            else ...[
              () {
                final query = _watchlistFilterCtrl.text.trim().toUpperCase();
                final filtered = query.isEmpty
                    ? _watchlist
                    : _watchlist.where((item) {
                        final sym =
                            (item['symbol'] ?? '').toString().toUpperCase();
                        final mType = (item['market_type'] ?? '')
                            .toString()
                            .toUpperCase();
                        return sym.contains(query) || mType.contains(query);
                      }).toList();

                if (filtered.isEmpty) {
                  return Padding(
                    padding: const EdgeInsets.symmetric(vertical: 12),
                    child: Center(
                      child: Text('ไม่พบสินทรัพย์ที่ค้นหา "$query"',
                          style: const TextStyle(
                              color: Colors.white38, fontSize: 12)),
                    ),
                  );
                }

                return ConstrainedBox(
                  constraints: const BoxConstraints(maxHeight: 320),
                  child: ListView.builder(
                    shrinkWrap: true,
                    itemCount: filtered.length,
                    itemBuilder: (ctx, i) {
                      final item = filtered[i];
                      final sym = item['symbol'] ?? '';
                      final mType = (item['market_type'] ?? 'crypto')
                          .toString()
                          .toUpperCase();
                      final tf =
                          (item['timeframe'] ?? '1h').toString().toUpperCase();
                      final isTHB =
                          sym.toString().toUpperCase().endsWith('/THB');

                      return Container(
                        margin: const EdgeInsets.only(bottom: 6),
                        padding: const EdgeInsets.symmetric(
                            horizontal: 10, vertical: 6),
                        decoration: BoxDecoration(
                          color: const Color(0xFF1B2333),
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(
                            color: isTHB
                                ? const Color(0xFF9B59B6).withValues(alpha: 0.4)
                                : const Color(0xFF2E82FE)
                                    .withValues(alpha: 0.3),
                          ),
                        ),
                        child: Row(
                          children: [
                            Container(
                              padding: const EdgeInsets.symmetric(
                                  horizontal: 6, vertical: 2),
                              decoration: BoxDecoration(
                                color: (isTHB
                                        ? const Color(0xFF9B59B6)
                                        : const Color(0xFF2E82FE))
                                    .withValues(alpha: 0.2),
                                borderRadius: BorderRadius.circular(4),
                              ),
                              child: Text(
                                isTHB ? 'THB' : mType,
                                style: TextStyle(
                                  fontSize: 10,
                                  fontWeight: FontWeight.bold,
                                  color: isTHB
                                      ? const Color(0xFFC39BD3)
                                      : const Color(0xFF2E82FE),
                                ),
                              ),
                            ),
                            const SizedBox(width: 8),
                            Text(sym,
                                style: const TextStyle(
                                    fontWeight: FontWeight.bold,
                                    fontSize: 13,
                                    color: Colors.white)),
                            const SizedBox(width: 6),
                            Text('• TF $tf',
                                style: const TextStyle(
                                    fontSize: 11, color: Colors.white54)),
                            const Spacer(),
                            IconButton(
                              icon: const Icon(Icons.delete_outline,
                                  size: 19, color: AppColors.bearish),
                              tooltip: 'ลบ $sym ออกจาก Watchlist',
                              onPressed: () => _removeWatchlistItem(sym),
                              padding: EdgeInsets.zero,
                              constraints: const BoxConstraints(),
                            ),
                          ],
                        ),
                      );
                    },
                  ),
                );
              }(),
            ],
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  flex: 3,
                  child: ElevatedButton.icon(
                    onPressed: _showAddAssetDialog,
                    icon: const Icon(Icons.add_circle,
                        size: 16, color: Colors.black),
                    label: const Text('➕ เพิ่มสินทรัพย์เข้า Watchlist',
                        style: TextStyle(
                            color: Colors.black,
                            fontWeight: FontWeight.bold,
                            fontSize: 12)),
                    style: ElevatedButton.styleFrom(
                      backgroundColor: AppColors.bullish,
                      padding: const EdgeInsets.symmetric(vertical: 10),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  flex: 2,
                  child: OutlinedButton.icon(
                    onPressed: _resetDefaultWatchlist,
                    icon: const Icon(Icons.refresh,
                        size: 15, color: Colors.white70),
                    label: const Text('🔄 ค่าเริ่มต้น',
                        style: TextStyle(
                            color: Colors.white70,
                            fontWeight: FontWeight.bold,
                            fontSize: 11)),
                    style: OutlinedButton.styleFrom(
                      side: const BorderSide(color: AppColors.border),
                      padding: const EdgeInsets.symmetric(vertical: 10),
                      shape: RoundedRectangleBorder(
                          borderRadius: BorderRadius.circular(8)),
                    ),
                  ),
                ),
              ],
            ),
          ]),

          const SizedBox(height: 16),

          // ---- Notifications ----
          _sectionHeader('🔔 Notifications'),
          _card([
            // FCM
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Push Notifications (FCM)',
                  style: TextStyle(fontSize: 14, color: Colors.white70)),
              value: settings.fcmEnabled,
              activeThumbColor: AppColors.bullish,
              onChanged: (v) => ref
                  .read(settingsProvider.notifier)
                  .save(settings.copyWith(fcmEnabled: v)),
            ),
            const Divider(color: Colors.white12),

            // Telegram
            const Text('Telegram',
                style: TextStyle(fontSize: 12, color: Colors.white38)),
            const SizedBox(height: 8),
            _textField('Bot Token', _telegramTokenCtrl, hint: '123456:ABC...'),
            const SizedBox(height: 6),
            _textField('Chat ID', _telegramChatIdCtrl, hint: '-100123456789'),
            const SizedBox(height: 8),
            _testBtn('Telegram'),
            const Divider(color: Colors.white12),

            // Line
            const Text('LINE Notify',
                style: TextStyle(fontSize: 12, color: Colors.white38)),
            const SizedBox(height: 8),
            _textField('LINE Token', _lineTokenCtrl, hint: 'your_line_token'),
            const SizedBox(height: 8),
            _testBtn('LINE Notify'),
          ]),

          const SizedBox(height: 24),

          // ---- App Version Footer ----
          Center(
            child: Column(
              children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: const Color(0xFF161B26),
                    borderRadius: BorderRadius.circular(10),
                    border:
                        Border.all(color: Colors.white.withValues(alpha: 0.06)),
                  ),
                  child: const Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      Icon(Icons.verified_outlined,
                          size: 14, color: AppColors.bullish),
                      SizedBox(width: 6),
                      Text(
                        '${AppConstants.appName} ${AppConstants.fullVersion}',
                        style: TextStyle(
                            fontSize: 11,
                            color: Colors.white70,
                            fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                ),
                const SizedBox(height: 6),
                const Text(
                  'Institutional SMC & Quantitative Trading Co-Pilot',
                  style: TextStyle(fontSize: 10, color: Colors.white24),
                ),
              ],
            ),
          ),

          const SizedBox(height: 32),
        ],
      ),
    );
  }

  // ---------- Helpers ----------

  Widget _sectionHeader(String title) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(title,
          style: const TextStyle(
              fontSize: 13,
              color: Colors.white38,
              fontWeight: FontWeight.w600)),
    );
  }

  Widget _card(List<Widget> children) {
    return Container(
      padding: const EdgeInsets.all(14),
      decoration: BoxDecoration(
        color: AppColors.surface,
        borderRadius: BorderRadius.circular(12),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: children,
      ),
    );
  }

  Widget _textField(String label, TextEditingController ctrl,
      {String hint = '', bool obscure = false}) {
    return TextField(
      controller: ctrl,
      obscureText: obscure,
      style: const TextStyle(color: Colors.white, fontSize: 13),
      decoration: InputDecoration(
        labelText: label,
        labelStyle: const TextStyle(color: Colors.white38, fontSize: 12),
        hintText: hint,
        hintStyle: const TextStyle(color: Colors.white24, fontSize: 12),
        filled: true,
        fillColor: const Color(0xFF252540),
        contentPadding:
            const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide.none,
        ),
      ),
    );
  }

  Widget _testBtn(String label) {
    return SizedBox(
      width: double.infinity,
      child: OutlinedButton.icon(
        onPressed: () => _testConnection(label),
        icon: const Icon(Icons.check_circle_outline, size: 16),
        label: const Text('Test Connection'),
        style: OutlinedButton.styleFrom(
          foregroundColor: AppColors.bullish,
          side: const BorderSide(color: AppColors.bullish, width: 0.5),
          padding: const EdgeInsets.symmetric(vertical: 8),
        ),
      ),
    );
  }

  Widget _riskSlider({
    required String label,
    required double value,
    required double min,
    required double max,
    required int divisions,
    required ValueChanged<double> onChanged,
    required String suffix,
    bool isInt = false,
  }) {
    final display = isInt ? value.toInt().toString() : value.toStringAsFixed(1);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            Text(label,
                style: const TextStyle(fontSize: 13, color: Colors.white70)),
            Text('$display$suffix',
                style: const TextStyle(
                    fontSize: 13,
                    color: AppColors.bullish,
                    fontWeight: FontWeight.bold)),
          ],
        ),
        Slider(
          value: value,
          min: min,
          max: max,
          divisions: divisions,
          activeColor: AppColors.bullish,
          inactiveColor: const Color(0xFF252540),
          onChanged: onChanged,
        ),
      ],
    );
  }

  Widget _modeButton({
    required String label,
    required IconData icon,
    required bool selected,
    required Color color,
    required VoidCallback? onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(vertical: 12),
        decoration: BoxDecoration(
          color: selected
              ? color.withValues(alpha: 0.15)
              : const Color(0xFF252540),
          borderRadius: BorderRadius.circular(8),
          border: Border.all(
            color: selected ? color : Colors.transparent,
            width: 1.5,
          ),
        ),
        child: Column(
          children: [
            Icon(icon, color: selected ? color : Colors.white38, size: 22),
            const SizedBox(height: 4),
            Text(label,
                style: TextStyle(
                    fontSize: 12, color: selected ? color : Colors.white38)),
          ],
        ),
      ),
    );
  }

  void _confirmLiveMode() {
    showDialog(
      context: context,
      builder: (dialogCtx) => AlertDialog(
        backgroundColor: AppColors.surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        title: const Row(
          children: [
            Icon(Icons.bolt, color: AppColors.bearish, size: 22),
            SizedBox(width: 8),
            Text('Enable Live Trading?',
                style: TextStyle(
                    color: Colors.white,
                    fontSize: 16,
                    fontWeight: FontWeight.bold)),
          ],
        ),
        content: const Text(
          'ระบบจะตรวจสอบการเชื่อมต่อ InnovestX และเปิด Live Session ชั่วคราว 15 นาที คำสั่งที่ส่งผ่าน Live API อาจใช้เงินจริง\n\nSession จะไม่ถูกบันทึก และเมื่อเปิดแอปหรือ Backend ใหม่ ระบบจะกลับเป็น Paper เสมอ',
          style: TextStyle(color: Colors.white70, fontSize: 13, height: 1.4),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogCtx).pop(),
            child:
                const Text('Cancel', style: TextStyle(color: Colors.white54)),
          ),
          ElevatedButton(
            onPressed: () async {
              Navigator.of(dialogCtx).pop();
              try {
                final expiresAt = await ref
                    .read(settingsProvider.notifier)
                    .activateLiveMode(broker: 'innovestx', ttlMinutes: 15);
                if (mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      backgroundColor: AppColors.bearish,
                      content: Text(
                          '⚡ LIVE SESSION ACTIVATED จนถึง ${expiresAt.hour.toString().padLeft(2, '0')}:${expiresAt.minute.toString().padLeft(2, '0')} (Real Funds)',
                          style: const TextStyle(
                              color: Colors.white,
                              fontWeight: FontWeight.bold)),
                      duration: const Duration(seconds: 4),
                    ),
                  );
                }
              } catch (error) {
                if (mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(
                    SnackBar(
                      backgroundColor: AppColors.bearish,
                      content: Text(
                          'เปิด Live Trading ไม่สำเร็จ: ${_requestErrorMessage(error)}'),
                    ),
                  );
                }
              }
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.bearish,
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(6)),
            ),
            child: const Text('Enable Live Trading',
                style: TextStyle(
                    color: Colors.white, fontWeight: FontWeight.bold)),
          ),
        ],
      ),
    );
  }

  Future<void> _openPromptEditor() async {
    String content = '';
    String promptName = 'advisor_v1.md';
    bool isLoading = true;
    bool isFetched = false;
    final textCtrl = TextEditingController();

    try {
      await showDialog(
        context: context,
        builder: (ctx) {
          return StatefulBuilder(
            builder: (context, setModalState) {
              if (!isFetched) {
                isFetched = true;
                AppApi.dio
                    .get(AppApi.url('/api/v1/settings/prompts/active'))
                    .then((resp) {
                  setModalState(() {
                    content = resp.data['content']?.toString() ?? '';
                    promptName =
                        resp.data['name']?.toString() ?? 'advisor_v1.md';
                    textCtrl.text = content;
                    isLoading = false;
                  });
                }).catchError((e) {
                  setModalState(() {
                    content = '# Error loading prompt: $e';
                    textCtrl.text = content;
                    isLoading = false;
                  });
                });
              }

              final maxH = MediaQuery.of(context).size.height * 0.85;

              return Dialog(
                backgroundColor: const Color(0xFF141923),
                shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(12),
                    side: const BorderSide(color: Color(0xFF252D3D))),
                insetPadding:
                    const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
                child: Container(
                  width: 760,
                  height: maxH.clamp(400.0, 680.0),
                  padding: const EdgeInsets.all(18),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          const Icon(Icons.edit_note,
                              color: Color(0xFF2E82FE), size: 24),
                          const SizedBox(width: 8),
                          Text('System Prompt Editor ($promptName)',
                              style: const TextStyle(
                                  fontWeight: FontWeight.bold,
                                  fontSize: 16,
                                  color: Colors.white)),
                          const Spacer(),
                          Container(
                            padding: const EdgeInsets.symmetric(
                                horizontal: 8, vertical: 3),
                            decoration: BoxDecoration(
                              color:
                                  AppColors.orderBlock.withValues(alpha: 0.2),
                              borderRadius: BorderRadius.circular(4),
                            ),
                            child: Text('${textCtrl.text.length} chars',
                                style: const TextStyle(
                                    fontSize: 11, color: AppColors.orderBlock)),
                          ),
                          const SizedBox(width: 8),
                          IconButton(
                            icon:
                                const Icon(Icons.close, color: Colors.white54),
                            onPressed: () => Navigator.pop(ctx),
                          ),
                        ],
                      ),
                      const SizedBox(height: 12),
                      if (isLoading)
                        const Expanded(
                          child: Center(
                            child: Column(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                CircularProgressIndicator(
                                    color: AppColors.bullish),
                                SizedBox(height: 12),
                                Text('Loading active prompt...',
                                    style: TextStyle(color: Colors.white54)),
                              ],
                            ),
                          ),
                        )
                      else
                        Expanded(
                          child: Container(
                            decoration: BoxDecoration(
                              color: const Color(0xFF0D111A),
                              borderRadius: BorderRadius.circular(8),
                              border:
                                  Border.all(color: const Color(0xFF252D3D)),
                            ),
                            child: TextField(
                              controller: textCtrl,
                              maxLines: null,
                              expands: true,
                              style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 13,
                                  fontFamily: 'monospace',
                                  height: 1.45),
                              decoration: const InputDecoration(
                                contentPadding: EdgeInsets.all(14),
                                border: InputBorder.none,
                                hintText:
                                    'Enter AI trading advisor system prompt instructions...',
                              ),
                            ),
                          ),
                        ),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          TextButton.icon(
                            onPressed: () async {
                              try {
                                await AppApi.dio.post(AppApi.url(
                                    '/api/v1/settings/prompts/reload'));
                                final resp = await AppApi.dio.get(AppApi.url(
                                    '/api/v1/settings/prompts/active'));
                                setModalState(() {
                                  content =
                                      resp.data['content']?.toString() ?? '';
                                  textCtrl.text = content;
                                });
                                if (context.mounted) {
                                  ScaffoldMessenger.of(context).showSnackBar(
                                    const SnackBar(
                                        backgroundColor: AppColors.bullish,
                                        content:
                                            Text('Prompt reloaded from disk!')),
                                  );
                                }
                              } catch (_) {}
                            },
                            icon: const Icon(Icons.refresh, size: 16),
                            label: const Text('Reload from Disk'),
                            style: TextButton.styleFrom(
                                foregroundColor: Colors.white60),
                          ),
                          const Spacer(),
                          TextButton(
                            onPressed: () => Navigator.pop(ctx),
                            child: const Text('Cancel',
                                style: TextStyle(color: Colors.white54)),
                          ),
                          const SizedBox(width: 8),
                          ElevatedButton.icon(
                            onPressed: isLoading
                                ? null
                                : () async {
                                    try {
                                      final newContent = textCtrl.text;
                                      await AppApi.dio.post(
                                        AppApi.url(
                                            '/api/v1/settings/prompts/save'),
                                        data: {
                                          'name': promptName,
                                          'content': newContent,
                                        },
                                      );
                                      if (ctx.mounted) {
                                        Navigator.pop(ctx);
                                        ScaffoldMessenger.of(context)
                                            .showSnackBar(
                                          const SnackBar(
                                            backgroundColor: AppColors.bullish,
                                            content: Text(
                                                '✅ System Prompt saved & active in AI Advisor!'),
                                          ),
                                        );
                                      }
                                    } catch (e) {
                                      if (ctx.mounted) {
                                        ScaffoldMessenger.of(context)
                                            .showSnackBar(
                                          SnackBar(
                                              backgroundColor:
                                                  AppColors.bearish,
                                              content: Text('Save failed: $e')),
                                        );
                                      }
                                    }
                                  },
                            icon: const Icon(Icons.save,
                                size: 16, color: Colors.black),
                            label: const Text('Save Changes',
                                style: TextStyle(
                                    color: Colors.black,
                                    fontWeight: FontWeight.bold)),
                            style: ElevatedButton.styleFrom(
                                backgroundColor: AppColors.bullish),
                          ),
                        ],
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
      textCtrl.dispose();
    }
  }

  Future<void> _testPrompt() async {
    bool isTesting = true;
    bool isTriggered = false;
    String responseText = '';

    showDialog(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (context, setModalState) {
            if (!isTriggered) {
              isTriggered = true;
              AppApi.dio
                  .post(
                AppApi.url('/api/v1/settings/prompts/test'),
                options: Options(
                  sendTimeout: const Duration(seconds: 90),
                  receiveTimeout: const Duration(seconds: 90),
                ),
              )
                  .then((resp) {
                setModalState(() {
                  isTesting = false;
                  responseText = resp.data['ai_response']?.toString() ??
                      'No response returned.';
                });
              }).catchError((e) {
                setModalState(() {
                  isTesting = false;
                  responseText =
                      'Prompt Test Failed: $e\n\nPlease check that your configured AI provider is running and reachable.';
                });
              });
            }

            return Dialog(
              backgroundColor: const Color(0xFF141923),
              shape: RoundedRectangleBorder(
                  borderRadius: BorderRadius.circular(12),
                  side: const BorderSide(color: Color(0xFF252D3D))),
              child: Container(
                width: 620,
                padding: const EdgeInsets.all(20),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        const Icon(Icons.smart_toy_outlined,
                            color: AppColors.bullish, size: 24),
                        const SizedBox(width: 8),
                        const Text('AI Advisor Prompt Test',
                            style: TextStyle(
                                fontWeight: FontWeight.bold,
                                fontSize: 16,
                                color: Colors.white)),
                        const Spacer(),
                        IconButton(
                            icon:
                                const Icon(Icons.close, color: Colors.white54),
                            onPressed: () => Navigator.pop(ctx)),
                      ],
                    ),
                    const SizedBox(height: 14),
                    if (isTesting)
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 36),
                        child: Center(
                          child: Column(
                            children: [
                              CircularProgressIndicator(
                                  color: AppColors.bullish),
                              SizedBox(height: 16),
                              Text(
                                  'Testing active system prompt with AI Engine...',
                                  style: TextStyle(color: Colors.white70)),
                            ],
                          ),
                        ),
                      )
                    else ...[
                      Container(
                        padding: const EdgeInsets.symmetric(
                            horizontal: 10, vertical: 6),
                        decoration: BoxDecoration(
                          color: const Color(0xFF1E2533),
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(
                              color: const Color(0xFF2E82FE)
                                  .withValues(alpha: 0.4)),
                        ),
                        child: const Row(
                          children: [
                            Icon(Icons.input,
                                size: 14, color: Color(0xFF2E82FE)),
                            SizedBox(width: 6),
                            Text(
                                'Test Input: BTC/USDT (LONG) Confluence 80/100',
                                style: TextStyle(
                                    fontSize: 12,
                                    color: Color(0xFF2E82FE),
                                    fontWeight: FontWeight.bold)),
                          ],
                        ),
                      ),
                      const SizedBox(height: 10),
                      Container(
                        width: double.infinity,
                        constraints: const BoxConstraints(maxHeight: 300),
                        padding: const EdgeInsets.all(12),
                        decoration: BoxDecoration(
                          color: const Color(0xFF0D111A),
                          borderRadius: BorderRadius.circular(8),
                          border: Border.all(color: const Color(0xFF252D3D)),
                        ),
                        child: SingleChildScrollView(
                          child: Text(
                            responseText,
                            style: const TextStyle(
                                fontSize: 13,
                                color: Colors.white,
                                height: 1.45),
                          ),
                        ),
                      ),
                      const SizedBox(height: 16),
                      Align(
                        alignment: Alignment.centerRight,
                        child: ElevatedButton(
                          onPressed: () => Navigator.pop(ctx),
                          style: ElevatedButton.styleFrom(
                              backgroundColor: const Color(0xFF1E2533)),
                          child: const Text('Close',
                              style: TextStyle(color: Colors.white)),
                        ),
                      ),
                    ],
                  ],
                ),
              ),
            );
          },
        );
      },
    );
  }
}
