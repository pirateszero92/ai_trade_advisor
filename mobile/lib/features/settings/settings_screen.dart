import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';
import '../../core/api/api_client.dart';

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

final settingsProvider = StateNotifierProvider<SettingsNotifier, SettingsState>((ref) {
  return SettingsNotifier();
});

class SettingsNotifier extends StateNotifier<SettingsState> {
  static const _storage = FlutterSecureStorage();

  SettingsNotifier() : super(const SettingsState()) {
    _load();
  }

  Future<void> _load() async {
    final prefs = await SharedPreferences.getInstance();
    state = state.copyWith(
      apiBaseUrl: prefs.getString('api_base_url') ?? state.apiBaseUrl,
      aiProvider: prefs.getString('ai_provider') ?? state.aiProvider,
      lmStudioEndpoint: prefs.getString('lm_studio_endpoint') ?? state.lmStudioEndpoint,
      lmStudioModel: prefs.getString('lm_studio_model') ?? state.lmStudioModel,
      geminiModel: prefs.getString('gemini_model') ?? state.geminiModel,
      openRouterModel: prefs.getString('openrouter_model') ?? state.openRouterModel,
      riskPerTrade: prefs.getDouble('risk_per_trade') ?? state.riskPerTrade,
      maxDailyLoss: prefs.getDouble('max_daily_loss') ?? state.maxDailyLoss,
      maxPositions: prefs.getInt('max_positions') ?? state.maxPositions,
      isPaperMode: prefs.getBool('is_paper_mode') ?? state.isPaperMode,
      fcmEnabled: prefs.getBool('fcm_enabled') ?? state.fcmEnabled,
      telegramToken: prefs.getString('telegram_token') ?? state.telegramToken,
      telegramChatId: prefs.getString('telegram_chat_id') ?? state.telegramChatId,
      lineToken: prefs.getString('line_token') ?? state.lineToken,
      entryMode: prefs.getString('entry_mode') ?? state.entryMode,
      autoSlTp: prefs.getBool('auto_sl_tp') ?? state.autoSlTp,
      autoInvalidation: prefs.getBool('auto_invalidation') ?? state.autoInvalidation,
      geminiKey: await _storage.read(key: 'gemini_key') ?? '',
      openRouterKey: await _storage.read(key: 'openrouter_key') ?? '',
    );
  }

  Future<void> save(SettingsState newState) async {
    state = newState;
    if (newState.apiBaseUrl.isNotEmpty) {
      AppApi.setBaseUrl(newState.apiBaseUrl);
    }
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('api_base_url', newState.apiBaseUrl);
    await prefs.setString('ai_provider', newState.aiProvider);
    await prefs.setString('lm_studio_endpoint', newState.lmStudioEndpoint);
    await prefs.setString('lm_studio_model', newState.lmStudioModel);
    await prefs.setString('gemini_model', newState.geminiModel);
    await prefs.setString('openrouter_model', newState.openRouterModel);
    await prefs.setDouble('risk_per_trade', newState.riskPerTrade);
    await prefs.setDouble('max_daily_loss', newState.maxDailyLoss);
    await prefs.setInt('max_positions', newState.maxPositions);
    await prefs.setBool('is_paper_mode', newState.isPaperMode);
    await prefs.setBool('fcm_enabled', newState.fcmEnabled);
    await prefs.setString('telegram_token', newState.telegramToken);
    await prefs.setString('telegram_chat_id', newState.telegramChatId);
    await prefs.setString('line_token', newState.lineToken);
    await prefs.setString('entry_mode', newState.entryMode);
    await prefs.setBool('auto_sl_tp', newState.autoSlTp);
    await prefs.setBool('auto_invalidation', newState.autoInvalidation);
    await _storage.write(key: 'gemini_key', value: newState.geminiKey);
    await _storage.write(key: 'openrouter_key', value: newState.openRouterKey);
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
  late TabController _aiTabController;
  late TabController _brokerTabController;

  // Text controllers
  late TextEditingController _apiUrlCtrl;
  late TextEditingController _lmEndpointCtrl;
  late TextEditingController _lmModelCtrl;
  late TextEditingController _geminiKeyCtrl;
  late TextEditingController _geminiModelCtrl;
  late TextEditingController _openRouterKeyCtrl;
  late TextEditingController _openRouterModelCtrl;
  late TextEditingController _telegramTokenCtrl;
  late TextEditingController _telegramChatIdCtrl;
  late TextEditingController _lineTokenCtrl;

  // Broker & Exchange controllers
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

  bool _initialized = false;

  List<Map<String, dynamic>> _watchlist = [];

  @override
  void initState() {
    super.initState();
    _aiTabController = TabController(length: 3, vsync: this);
    _brokerTabController = TabController(length: 3, vsync: this);
    _apiUrlCtrl = TextEditingController();
    _lmEndpointCtrl = TextEditingController();
    _lmModelCtrl = TextEditingController();
    _geminiKeyCtrl = TextEditingController();
    _geminiModelCtrl = TextEditingController();
    _openRouterKeyCtrl = TextEditingController();
    _openRouterModelCtrl = TextEditingController();
    _telegramTokenCtrl = TextEditingController();
    _telegramChatIdCtrl = TextEditingController();
    _lineTokenCtrl = TextEditingController();

    _mt5LoginCtrl = TextEditingController();
    _mt5PasswordCtrl = TextEditingController();
    _mt5ServerCtrl = TextEditingController();
    _mt5PathCtrl = TextEditingController(text: r'C:/Program Files/MetaTrader 5/terminal64.exe');
    _binanceKeyCtrl = TextEditingController();
    _binanceSecretCtrl = TextEditingController();
    _bybitKeyCtrl = TextEditingController();
    _bybitSecretCtrl = TextEditingController();
    _alpacaKeyCtrl = TextEditingController();
    _alpacaSecretCtrl = TextEditingController();
    _alpacaBaseUrlCtrl = TextEditingController(text: 'https://paper-api.alpaca.markets');

    _loadAllSettings();
    _fetchWatchlist();
  }

  Future<void> _loadAllSettings() async {
    final prefs = await SharedPreferences.getInstance();
    const storage = FlutterSecureStorage();

    // 1. Preload local SharedPreferences & SecureStorage
    final savedApiUrl = prefs.getString('api_base_url');
    if (savedApiUrl != null && savedApiUrl.trim().isNotEmpty) {
      _apiUrlCtrl.text = savedApiUrl.trim();
      AppApi.setBaseUrl(savedApiUrl.trim());
    } else {
      final defaultUrl = kIsWeb
          ? (Uri.base.origin.isNotEmpty && !Uri.base.origin.startsWith('null') ? Uri.base.origin : 'http://192.168.22.84:8000')
          : 'http://192.168.22.84:8000';
      _apiUrlCtrl.text = defaultUrl;
    }

    _lmEndpointCtrl.text = prefs.getString('lm_studio_endpoint') ?? 'http://home3.netbird.cloud:11434';
    _lmModelCtrl.text = prefs.getString('lm_studio_model') ?? 'gpt-oss:120b-cloud';
    _geminiModelCtrl.text = prefs.getString('gemini_model') ?? 'gemini-2.0-flash';
    _openRouterModelCtrl.text = prefs.getString('openrouter_model') ?? 'anthropic/claude-3.5-sonnet';
    _telegramTokenCtrl.text = prefs.getString('telegram_token') ?? '';
    _telegramChatIdCtrl.text = prefs.getString('telegram_chat_id') ?? '';
    _lineTokenCtrl.text = prefs.getString('line_token') ?? '';
    _geminiKeyCtrl.text = await storage.read(key: 'gemini_key') ?? '';
    _openRouterKeyCtrl.text = await storage.read(key: 'openrouter_key') ?? '';

    final savedProvider = prefs.getString('ai_provider') ?? 'local';
    final tabIndex = {'local': 0, 'lmstudio': 0, 'openai': 0, 'gemini': 1, 'openrouter': 2}[savedProvider] ?? 0;
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

          if (_lmEndpointCtrl.text.isEmpty && ep != null && ep.isNotEmpty) _lmEndpointCtrl.text = ep;
          if (_lmModelCtrl.text.isEmpty && m != null && m.isNotEmpty) _lmModelCtrl.text = m;
          if (_geminiKeyCtrl.text.isEmpty && gk != null && gk.isNotEmpty && !gk.contains('*')) _geminiKeyCtrl.text = gk;
          if (_geminiModelCtrl.text.isEmpty && gm != null && gm.isNotEmpty) _geminiModelCtrl.text = gm;
          if (_openRouterKeyCtrl.text.isEmpty && ok != null && ok.isNotEmpty && !ok.contains('*')) _openRouterKeyCtrl.text = ok;
          if (_openRouterModelCtrl.text.isEmpty && om != null && om.isNotEmpty) _openRouterModelCtrl.text = om;
        });
      }

      // Fetch Broker configuration
      final bResp = await dio.get(AppApi.url('/api/v1/settings/brokers/config'));
      final bData = bResp.data as Map<String, dynamic>;
      if (mounted) {
        setState(() {
          if (_mt5LoginCtrl.text.isEmpty && bData['mt5_login'] != null && bData['mt5_login'] != 0) _mt5LoginCtrl.text = bData['mt5_login'].toString();
          if (_mt5PasswordCtrl.text.isEmpty && bData['mt5_password'] != null) _mt5PasswordCtrl.text = bData['mt5_password'].toString();
          if (_mt5ServerCtrl.text.isEmpty && bData['mt5_server'] != null) _mt5ServerCtrl.text = bData['mt5_server'].toString();
          if (_mt5PathCtrl.text.isEmpty && bData['mt5_path'] != null) _mt5PathCtrl.text = bData['mt5_path'].toString();
          if (_binanceKeyCtrl.text.isEmpty && bData['binance_api_key'] != null && !bData['binance_api_key'].toString().contains('*')) _binanceKeyCtrl.text = bData['binance_api_key'].toString();
          if (_binanceSecretCtrl.text.isEmpty && bData['binance_api_secret'] != null && !bData['binance_api_secret'].toString().contains('*')) _binanceSecretCtrl.text = bData['binance_api_secret'].toString();
          if (_bybitKeyCtrl.text.isEmpty && bData['bybit_api_key'] != null && !bData['bybit_api_key'].toString().contains('*')) _bybitKeyCtrl.text = bData['bybit_api_key'].toString();
          if (_bybitSecretCtrl.text.isEmpty && bData['bybit_api_secret'] != null && !bData['bybit_api_secret'].toString().contains('*')) _bybitSecretCtrl.text = bData['bybit_api_secret'].toString();
          if (_alpacaKeyCtrl.text.isEmpty && bData['alpaca_api_key'] != null && !bData['alpaca_api_key'].toString().contains('*')) _alpacaKeyCtrl.text = bData['alpaca_api_key'].toString();
          if (_alpacaSecretCtrl.text.isEmpty && bData['alpaca_api_secret'] != null && !bData['alpaca_api_secret'].toString().contains('*')) _alpacaSecretCtrl.text = bData['alpaca_api_secret'].toString();
          if (_alpacaBaseUrlCtrl.text.isEmpty && bData['alpaca_base_url'] != null) _alpacaBaseUrlCtrl.text = bData['alpaca_base_url'].toString();
        });
      }
    } catch (_) {}
  }

  Future<void> _fetchWatchlist() async {
    try {
      final dio = Dio();
      final resp = await dio.get(AppApi.url('/api/v1/settings/watchlist'));
      final List<dynamic> list = resp.data['watchlist'] ?? [];
      setState(() {
        _watchlist = list.map((e) => Map<String, dynamic>.from(e as Map)).toList();
      });
    } catch (_) {}
  }

  Future<void> _addWatchlistItem(String symbol, String marketType, String tf) async {
    try {
      final dio = Dio();
      await dio.post(
        AppApi.url('/api/v1/settings/watchlist'),
        data: {
          'symbol': symbol.trim().toUpperCase(),
          'market_type': marketType.toLowerCase(),
          'timeframe': tf.toLowerCase(),
          'htf_timeframe': tf == '1d' ? '1w' : '4h',
          'exchange': marketType == 'crypto' ? 'binance' : (marketType == 'forex' ? 'mt5' : 'alpaca'),
        },
      );
      _fetchWatchlist();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text('✅ Added $symbol to Proactive Scanner!', style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(backgroundColor: AppColors.bearish, content: Text('Failed to add asset: $e')),
        );
      }
    }
  }

  Future<void> _removeWatchlistItem(String symbol) async {
    try {
      final dio = Dio();
      await dio.delete(AppApi.url('/api/v1/settings/watchlist/$symbol'));
      _fetchWatchlist();
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            backgroundColor: AppColors.bullish,
            content: Text('🗑️ Removed $symbol from Scanner.', style: const TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(backgroundColor: AppColors.bearish, content: Text('Failed to remove asset: $e')),
        );
      }
    }
  }

  void _showAddAssetDialog() {
    final symCtrl = TextEditingController();
    String selectedType = 'crypto';
    String selectedTf = '1h';

    showDialog(
      context: context,
      builder: (ctx) => StatefulBuilder(
        builder: (ctx, setDlgState) => AlertDialog(
          backgroundColor: AppColors.surface,
          title: const Row(
            children: [
              Icon(Icons.add_chart, color: AppColors.bullish, size: 20),
              SizedBox(width: 8),
              Text('เพิ่มคู่เหรียญ / สินทรัพย์สแกน', style: TextStyle(color: Colors.white, fontSize: 16)),
            ],
          ),
          content: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              TextField(
                controller: symCtrl,
                style: const TextStyle(color: Colors.white),
                decoration: const InputDecoration(
                  labelText: 'สัญลักษณ์ (เช่น BNB/USDT, GBPUSD, META)',
                  hintText: 'SOL/USDT',
                ),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                value: selectedType,
                dropdownColor: AppColors.surface,
                style: const TextStyle(color: Colors.white),
                decoration: const InputDecoration(labelText: 'ประเภทตลาด (Market Type)'),
                items: const [
                  DropdownMenuItem(value: 'crypto', child: Text('🪙 Crypto (Binance/Bybit)')),
                  DropdownMenuItem(value: 'forex', child: Text('💱 Forex & Gold (MT5)')),
                  DropdownMenuItem(value: 'stock', child: Text('📈 Stocks (US Equities)')),
                ],
                onChanged: (v) => setDlgState(() => selectedType = v ?? 'crypto'),
              ),
              const SizedBox(height: 12),
              DropdownButtonFormField<String>(
                value: selectedTf,
                dropdownColor: AppColors.surface,
                style: const TextStyle(color: Colors.white),
                decoration: const InputDecoration(labelText: 'Timeframe สแกน'),
                items: const [
                  DropdownMenuItem(value: '15m', child: Text('15 Minutes (15M)')),
                  DropdownMenuItem(value: '1h', child: Text('1 Hour (1H)')),
                  DropdownMenuItem(value: '4h', child: Text('4 Hours (4H)')),
                  DropdownMenuItem(value: '1d', child: Text('1 Day (1D)')),
                ],
                onChanged: (v) => setDlgState(() => selectedTf = v ?? '1h'),
              ),
            ],
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(ctx),
              child: const Text('ยกเลิก', style: TextStyle(color: Colors.white54)),
            ),
            ElevatedButton(
              onPressed: () {
                final s = symCtrl.text.trim();
                if (s.isNotEmpty) {
                  _addWatchlistItem(s, selectedType, selectedTf);
                  Navigator.pop(ctx);
                }
              },
              style: ElevatedButton.styleFrom(backgroundColor: AppColors.bullish),
              child: const Text('บันทึกคู่เหรียญ', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
            ),
          ],
        ),
      ),
    );
  }

  @override
  void dispose() {
    _aiTabController.dispose();
    _brokerTabController.dispose();
    _apiUrlCtrl.dispose();
    _lmEndpointCtrl.dispose();
    _lmModelCtrl.dispose();
    _geminiKeyCtrl.dispose();
    _geminiModelCtrl.dispose();
    _openRouterKeyCtrl.dispose();
    _openRouterModelCtrl.dispose();
    _telegramTokenCtrl.dispose();
    _telegramChatIdCtrl.dispose();
    _lineTokenCtrl.dispose();

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
    final providers = ['lmstudio', 'gemini', 'openrouter'];
    final selectedProvider = providers[_aiTabController.index];
    final lmEndpoint = _lmEndpointCtrl.text.trim();
    final lmModel = _lmModelCtrl.text.trim();
    final geminiKey = _geminiKeyCtrl.text.trim();
    final geminiModel = _geminiModelCtrl.text.trim();
    final openRouterKey = _openRouterKeyCtrl.text.trim();
    final openRouterModel = _openRouterModelCtrl.text.trim();
    final tgToken = _telegramTokenCtrl.text.trim();
    final tgChatId = _telegramChatIdCtrl.text.trim();
    final lineToken = _lineTokenCtrl.text.trim();

    // 1. Immediately apply base URL to runtime API client
    if (apiUrl.isNotEmpty) {
      AppApi.setBaseUrl(apiUrl);
    }

    final notifier = ref.read(settingsProvider.notifier);
    final current = ref.read(settingsProvider);

    await notifier.save(current.copyWith(
      apiBaseUrl: apiUrl,
      aiProvider: selectedProvider,
      lmStudioEndpoint: lmEndpoint,
      lmStudioModel: lmModel,
      geminiKey: geminiKey,
      geminiModel: geminiModel,
      openRouterKey: openRouterKey,
      openRouterModel: openRouterModel,
      telegramToken: tgToken,
      telegramChatId: tgChatId,
      lineToken: lineToken,
    ));

    // 2. Synchronize active configurations with FastAPI Backend runtime & storage
    try {
      final dio = AppApi.dio;
      await dio.post(
        AppApi.url('/api/v1/settings/llm/config'),
        data: {
          'provider': selectedProvider,
          'local_endpoint': lmEndpoint,
          'local_model': lmModel,
          'gemini_key': geminiKey,
          'gemini_model': geminiModel,
          'openrouter_key': openRouterKey,
          'openrouter_model': openRouterModel,
        },
      );

      // Save Broker & Exchange settings
      await dio.post(
        AppApi.url('/api/v1/settings/brokers/config'),
        data: {
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
        },
      );
    } catch (_) {}

    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text('✅ บันทึกการตั้งค่าทั้งหมดเรียบร้อยแล้ว (${apiUrl.isNotEmpty ? apiUrl : AppApi.baseUrl})'),
          backgroundColor: AppColors.bullish,
          duration: const Duration(seconds: 3),
        ),
      );
    }
  }

  Future<void> _testConnection(String label) async {
    final isTg = label.toLowerCase().contains('telegram');
    final isLine = label.toLowerCase().contains('line');
    final isApi = label.toLowerCase().contains('backend') || label.toLowerCase().contains('api');
    final isLLM = !isTg && !isLine && !isApi;

    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Testing $label connection...'),
        duration: const Duration(seconds: 2),
      ),
    );

    try {
      final dio = Dio();
      if (isApi) {
        final resp = await dio.get(AppApi.url('/health'));
        final ok = resp.data['status'] == 'ok';
        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              backgroundColor: ok ? AppColors.bullish : AppColors.bearish,
              content: Text(
                ok ? '✅ Backend API Connected! (v${resp.data['version']})' : '⚠️ Connection failed',
                style: TextStyle(color: ok ? Colors.black : Colors.white, fontWeight: FontWeight.bold),
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
                style: TextStyle(color: ok ? Colors.black : Colors.white, fontWeight: FontWeight.bold),
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
        final ok = isTg ? (results['telegram'] == true) : (isLine ? (results['line'] == true) : true);

        if (mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            SnackBar(
              backgroundColor: ok ? AppColors.bullish : AppColors.bearish,
              content: Text(
                ok
                    ? '✅ $label test alert sent successfully!'
                    : '⚠️ $label test failed. Please check your token/chat ID.',
                style: TextStyle(color: ok ? Colors.black : Colors.white, fontWeight: FontWeight.bold),
              ),
            ),
          );
        }
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(backgroundColor: AppColors.bearish, content: Text('Test request failed: $e')),
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
      final dio = Dio();
      final resp = await dio.post(
        AppApi.url('/api/v1/settings/brokers/test'),
        data: {
          'broker_type': brokerType,
          'login': int.tryParse(_mt5LoginCtrl.text.trim()),
          'server': _mt5ServerCtrl.text.trim(),
          'password': _mt5PasswordCtrl.text.trim(),
          'api_key': brokerType == 'binance'
              ? _binanceKeyCtrl.text.trim()
              : (brokerType == 'bybit' ? _bybitKeyCtrl.text.trim() : _alpacaKeyCtrl.text.trim()),
          'api_secret': brokerType == 'binance'
              ? _binanceSecretCtrl.text.trim()
              : (brokerType == 'bybit' ? _bybitSecretCtrl.text.trim() : _alpacaSecretCtrl.text.trim()),
          'base_url': _alpacaBaseUrlCtrl.text.trim(),
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
              style: TextStyle(color: ok ? Colors.black : Colors.white, fontWeight: FontWeight.bold),
            ),
            duration: const Duration(seconds: 4),
          ),
        );
      }
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(backgroundColor: AppColors.bearish, content: Text('Connection test failed: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(settingsProvider);
    return Scaffold(
      backgroundColor: AppColors.background,
      appBar: AppBar(
        title: const Text('Settings'),
        backgroundColor: AppColors.surface,
        actions: [
          TextButton.icon(
            onPressed: _save,
            icon: const Icon(Icons.save, color: AppColors.bullish),
            label: const Text('Save', style: TextStyle(color: AppColors.bullish)),
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // ---- Backend Connection ----
          _sectionHeader('🔌 Backend Connection'),
          _card([
            _textField('API Base URL', _apiUrlCtrl, hint: 'http://192.168.251.23:8000'),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton.icon(
                onPressed: () => _testConnection('Backend API'),
                icon: const Icon(Icons.wifi_tethering, size: 16),
                label: const Text('Test Connection'),
                style: OutlinedButton.styleFrom(foregroundColor: AppColors.bullish),
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
                        _textField('Endpoint (Ollama / LM Studio / OpenAI)', _lmEndpointCtrl, hint: 'http://10.0.2.2:11434 หรือ http://10.0.2.2:1234/v1'),
                        const SizedBox(height: 8),
                        _textField('Model Name', _lmModelCtrl, hint: 'gpt-oss:120b-cloud หรือ gpt-4o-mini'),
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
                        _textField('API Key', _geminiKeyCtrl, hint: 'AIza...', obscure: true),
                        const SizedBox(height: 8),
                        _textField('Model', _geminiModelCtrl, hint: 'gemini-1.5-pro'),
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
                        _textField('API Key', _openRouterKeyCtrl, hint: 'sk-or-...', obscure: true),
                        const SizedBox(height: 8),
                        _textField('Model', _openRouterModelCtrl, hint: 'google/gemini-pro-1.5'),
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
                const Icon(Icons.description_outlined, color: Colors.white54, size: 18),
                const SizedBox(width: 8),
                const Text('System Prompt', style: TextStyle(color: Colors.white70)),
                const Spacer(),
                Container(
                  padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 2),
                  decoration: BoxDecoration(
                    color: AppColors.orderBlock.withOpacity(0.2),
                    borderRadius: BorderRadius.circular(4),
                  ),
                  child: const Text('v1.0', style: TextStyle(fontSize: 11, color: AppColors.orderBlock)),
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
                    style: OutlinedButton.styleFrom(foregroundColor: Colors.white70),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _testPrompt,
                    icon: const Icon(Icons.play_arrow, size: 16),
                    label: const Text('Test Prompt'),
                    style: OutlinedButton.styleFrom(foregroundColor: AppColors.bullish),
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
              style: TextStyle(fontSize: 13, color: Colors.white70, fontWeight: FontWeight.bold),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: ChoiceChip(
                    label: const Center(
                      child: Text('Limit Zone (OB/FVG)', style: TextStyle(fontSize: 12)),
                    ),
                    selected: settings.entryMode == 'limit',
                    selectedColor: AppColors.bullish,
                    backgroundColor: const Color(0xFF252540),
                    onSelected: (selected) {
                      if (selected) {
                        ref.read(settingsProvider.notifier).save(settings.copyWith(entryMode: 'limit'));
                      }
                    },
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: ChoiceChip(
                    label: const Center(
                      child: Text('Market (Live Price)', style: TextStyle(fontSize: 12)),
                    ),
                    selected: settings.entryMode == 'market',
                    selectedColor: AppColors.bullish,
                    backgroundColor: const Color(0xFF252540),
                    onSelected: (selected) {
                      if (selected) {
                        ref.read(settingsProvider.notifier).save(settings.copyWith(entryMode: 'market'));
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
              title: const Text('Auto Stop Loss & Take Profit', style: TextStyle(fontSize: 14, color: Colors.white, fontWeight: FontWeight.w600)),
              subtitle: const Text('สั่งปิดออเดอร์ตัดขาดทุน/ทำกำไรอัตโนมัติเมื่อราคาแตะเส้น SL หรือ TP', style: TextStyle(fontSize: 11, color: Colors.white54)),
              value: settings.autoSlTp,
              activeColor: AppColors.bullish,
              onChanged: (v) => ref.read(settingsProvider.notifier).save(settings.copyWith(autoSlTp: v)),
            ),
            const Divider(color: Colors.white12, height: 16),
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Auto Invalidation Cut-Loss', style: TextStyle(fontSize: 14, color: Colors.white, fontWeight: FontWeight.w600)),
              subtitle: const Text('ตัดขาดทุนทันทีเมื่อโครงสร้างตลาดกลับตัวฝั่งตรงข้าม (เช่น เกิด CHoCH สวนทาง)', style: TextStyle(fontSize: 11, color: Colors.white54)),
              value: settings.autoInvalidation,
              activeColor: AppColors.bullish,
              onChanged: (v) => ref.read(settingsProvider.notifier).save(settings.copyWith(autoInvalidation: v)),
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
                      ref.read(settingsProvider.notifier).save(settings.copyWith(isPaperMode: true));
                      try {
                        final dio = Dio();
                        await dio.post(AppApi.url('/api/v1/settings/trading-mode'), data: {'mode': 'paper'});
                      } catch (_) {}
                      if (mounted) {
                        ScaffoldMessenger.of(context).showSnackBar(
                          const SnackBar(
                            backgroundColor: AppColors.neutral,
                            content: Text('🧪 Switched to Paper Trading Mode', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
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
                    onTap: () => _confirmLiveMode(settings),
                  ),
                ),
              ],
            ),
            if (!settings.isPaperMode)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Row(
                  children: [
                    Icon(Icons.warning_amber, color: AppColors.bearish, size: 14),
                    SizedBox(width: 4),
                    Text(
                      'LIVE MODE — real funds at risk',
                      style: TextStyle(fontSize: 11, color: AppColors.bearish),
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
              tabs: const [
                Tab(text: '💱 MetaTrader 5'),
                Tab(text: '🪙 Binance/Bybit'),
                Tab(text: '📈 Alpaca'),
              ],
            ),
            const SizedBox(height: 16),
            SizedBox(
              height: 290,
              child: TabBarView(
                controller: _brokerTabController,
                children: [
                  // 1. MetaTrader 5 Tab
                  SingleChildScrollView(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        const Row(
                          children: [
                            Icon(Icons.hub_outlined, color: Color(0xFF00E5FF), size: 16),
                            SizedBox(width: 6),
                            Text('MetaTrader 5 Direct Bridge (Forex & Gold)', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.white)),
                          ],
                        ),
                        const SizedBox(height: 10),
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
                                style: OutlinedButton.styleFrom(foregroundColor: AppColors.bullish),
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
                            Icon(Icons.currency_bitcoin, color: Color(0xFFF0B90B), size: 16),
                            SizedBox(width: 6),
                            Text('Binance / Bybit API Connection (Crypto)', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.white)),
                          ],
                        ),
                        const SizedBox(height: 10),
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
                                onPressed: () => _testBrokerConnection('binance'),
                                icon: const Icon(Icons.cable, size: 16),
                                label: const Text('Test Binance API'),
                                style: OutlinedButton.styleFrom(foregroundColor: AppColors.bullish),
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
                            Icon(Icons.trending_up, color: Color(0xFFFFD700), size: 16),
                            SizedBox(width: 6),
                            Text('Alpaca Markets (US Equities & Stocks)', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.white)),
                          ],
                        ),
                        const SizedBox(height: 10),
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
                                onPressed: () => _testBrokerConnection('alpaca'),
                                icon: const Icon(Icons.cable, size: 16),
                                label: const Text('Test Alpaca API'),
                                style: OutlinedButton.styleFrom(foregroundColor: AppColors.bullish),
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
              _sectionHeader('📊 Proactive Watchlist (คู่เหรียญ/สินทรัพย์ที่เฝ้าสแกน)'),
              IconButton(
                icon: const Icon(Icons.add_circle, color: AppColors.bullish, size: 20),
                tooltip: 'เพิ่มสินทรัพย์',
                onPressed: _showAddAssetDialog,
              ),
            ],
          ),
          _card([
            if (_watchlist.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 8),
                child: Text('กำลังโหลดรายการสินทรัพย์...', style: TextStyle(color: Colors.white38, fontSize: 12)),
              )
            else
              ..._watchlist.map((item) {
                final sym = item['symbol'] ?? '';
                final mType = (item['market_type'] ?? 'crypto').toString().toUpperCase();
                final tf = (item['timeframe'] ?? '1h').toString().toUpperCase();

                return Container(
                  margin: const EdgeInsets.only(bottom: 6),
                  padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                  decoration: BoxDecoration(
                    color: const Color(0xFF1B2333),
                    borderRadius: BorderRadius.circular(6),
                    border: Border.all(color: const Color(0xFF2E82FE).withOpacity(0.3)),
                  ),
                  child: Row(
                    children: [
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
                        decoration: BoxDecoration(
                          color: const Color(0xFF2E82FE).withOpacity(0.2),
                          borderRadius: BorderRadius.circular(4),
                        ),
                        child: Text(mType, style: const TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: Color(0xFF2E82FE))),
                      ),
                      const SizedBox(width: 8),
                      Text(sym, style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 13, color: Colors.white)),
                      const SizedBox(width: 6),
                      Text('• TF $tf', style: const TextStyle(fontSize: 11, color: Colors.white54)),
                      const Spacer(),
                      IconButton(
                        icon: const Icon(Icons.delete_outline, size: 18, color: AppColors.bearish),
                        tooltip: 'ลบสินทรัพย์',
                        onPressed: () => _removeWatchlistItem(sym),
                        padding: EdgeInsets.zero,
                        constraints: const BoxConstraints(),
                      ),
                    ],
                  ),
                );
              }),
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton.icon(
                onPressed: _showAddAssetDialog,
                icon: const Icon(Icons.add, size: 16, color: Colors.black),
                label: const Text('เพิ่มคู่เหรียญ / หุ้นสแกน (+ Add Asset)', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold, fontSize: 12)),
                style: ElevatedButton.styleFrom(
                  backgroundColor: AppColors.bullish,
                  padding: const EdgeInsets.symmetric(vertical: 8),
                ),
              ),
            ),
          ]),

          const SizedBox(height: 16),

          // ---- Notifications ----
          _sectionHeader('🔔 Notifications'),
          _card([
            // FCM
            SwitchListTile(
              contentPadding: EdgeInsets.zero,
              title: const Text('Push Notifications (FCM)', style: TextStyle(fontSize: 14, color: Colors.white70)),
              value: settings.fcmEnabled,
              activeColor: AppColors.bullish,
              onChanged: (v) => ref
                  .read(settingsProvider.notifier)
                  .save(settings.copyWith(fcmEnabled: v)),
            ),
            const Divider(color: Colors.white12),

            // Telegram
            const Text('Telegram', style: TextStyle(fontSize: 12, color: Colors.white38)),
            const SizedBox(height: 8),
            _textField('Bot Token', _telegramTokenCtrl, hint: '123456:ABC...'),
            const SizedBox(height: 6),
            _textField('Chat ID', _telegramChatIdCtrl, hint: '-100123456789'),
            const SizedBox(height: 8),
            _testBtn('Telegram'),
            const Divider(color: Colors.white12),

            // Line
            const Text('LINE Notify', style: TextStyle(fontSize: 12, color: Colors.white38)),
            const SizedBox(height: 8),
            _textField('LINE Token', _lineTokenCtrl, hint: 'your_line_token'),
            const SizedBox(height: 8),
            _testBtn('LINE Notify'),
          ]),

          const SizedBox(height: 32),
        ],
      ),
    );
  }

  // ---------- Helpers ----------

  Widget _sectionHeader(String title) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(title, style: const TextStyle(fontSize: 13, color: Colors.white38, fontWeight: FontWeight.w600)),
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
        contentPadding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
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
            Text(label, style: const TextStyle(fontSize: 13, color: Colors.white70)),
            Text('$display$suffix', style: const TextStyle(fontSize: 13, color: AppColors.bullish, fontWeight: FontWeight.bold)),
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
    required VoidCallback onTap,
  }) {
    return GestureDetector(
      onTap: onTap,
      child: AnimatedContainer(
        duration: const Duration(milliseconds: 200),
        padding: const EdgeInsets.symmetric(vertical: 12),
        decoration: BoxDecoration(
          color: selected ? color.withOpacity(0.15) : const Color(0xFF252540),
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
            Text(label, style: TextStyle(fontSize: 12, color: selected ? color : Colors.white38)),
          ],
        ),
      ),
    );
  }

  void _confirmLiveMode(SettingsState settings) {
    showDialog(
      context: context,
      builder: (dialogCtx) => AlertDialog(
        backgroundColor: AppColors.surface,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        title: const Row(
          children: [
            Icon(Icons.bolt, color: AppColors.bearish, size: 22),
            SizedBox(width: 8),
            Text('Enable Live Trading?', style: TextStyle(color: Colors.white, fontSize: 16, fontWeight: FontWeight.bold)),
          ],
        ),
        content: const Text(
          'This will enable LIVE trading mode using connected exchange accounts (Alpaca / Binance / MT5). Orders will be executed using real funds.\n\nAre you sure you want to proceed?',
          style: TextStyle(color: Colors.white70, fontSize: 13, height: 1.4),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(dialogCtx).pop(),
            child: const Text('Cancel', style: TextStyle(color: Colors.white54)),
          ),
          ElevatedButton(
            onPressed: () async {
              Navigator.of(dialogCtx).pop();
              await ref.read(settingsProvider.notifier).save(settings.copyWith(isPaperMode: false));
              try {
                final dio = Dio();
                await dio.post(AppApi.url('/api/v1/settings/trading-mode'), data: {'mode': 'live'});
              } catch (_) {}
              if (mounted) {
                ScaffoldMessenger.of(context).showSnackBar(
                  const SnackBar(
                    backgroundColor: AppColors.bearish,
                    content: Text('⚡ LIVE TRADING MODE ACTIVATED (Real Funds)', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
                    duration: Duration(seconds: 3),
                  ),
                );
              }
            },
            style: ElevatedButton.styleFrom(
              backgroundColor: AppColors.bearish,
              padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 8),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(6)),
            ),
            child: const Text('Enable Live Trading', style: TextStyle(color: Colors.white, fontWeight: FontWeight.bold)),
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

    showDialog(
      context: context,
      builder: (ctx) {
        return StatefulBuilder(
          builder: (context, setModalState) {
            if (!isFetched) {
              isFetched = true;
              Dio().get(AppApi.url('/api/v1/settings/prompts/active')).then((resp) {
                setModalState(() {
                  content = resp.data['content']?.toString() ?? '';
                  promptName = resp.data['name']?.toString() ?? 'advisor_v1.md';
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

            return Dialog(
              backgroundColor: const Color(0xFF141923),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12), side: const BorderSide(color: Color(0xFF252D3D))),
              insetPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 24),
              child: Container(
                width: 760,
                height: 620,
                padding: const EdgeInsets.all(18),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        const Icon(Icons.edit_note, color: Color(0xFF2E82FE), size: 24),
                        const SizedBox(width: 8),
                        Text('System Prompt Editor ($promptName)', style: const TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: Colors.white)),
                        const Spacer(),
                        Container(
                          padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
                          decoration: BoxDecoration(
                            color: AppColors.orderBlock.withOpacity(0.2),
                            borderRadius: BorderRadius.circular(4),
                          ),
                          child: Text('${textCtrl.text.length} chars', style: const TextStyle(fontSize: 11, color: AppColors.orderBlock)),
                        ),
                        const SizedBox(width: 8),
                        IconButton(
                          icon: const Icon(Icons.close, color: Colors.white54),
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
                              CircularProgressIndicator(color: AppColors.bullish),
                              SizedBox(height: 12),
                              Text('Loading active prompt...', style: TextStyle(color: Colors.white54)),
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
                            border: Border.all(color: const Color(0xFF252D3D)),
                          ),
                          child: TextField(
                            controller: textCtrl,
                            maxLines: null,
                            expands: true,
                            style: const TextStyle(color: Colors.white, fontSize: 13, fontFamily: 'monospace', height: 1.45),
                            decoration: const InputDecoration(
                              contentPadding: EdgeInsets.all(14),
                              border: InputBorder.none,
                              hintText: 'Enter AI trading advisor system prompt instructions...',
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
                              await Dio().post(AppApi.url('/api/v1/settings/prompts/reload'));
                              final resp = await Dio().get(AppApi.url('/api/v1/settings/prompts/active'));
                              setModalState(() {
                                content = resp.data['content']?.toString() ?? '';
                                textCtrl.text = content;
                              });
                              if (context.mounted) {
                                ScaffoldMessenger.of(context).showSnackBar(
                                  const SnackBar(backgroundColor: AppColors.bullish, content: Text('Prompt reloaded from disk!')),
                                );
                              }
                            } catch (_) {}
                          },
                          icon: const Icon(Icons.refresh, size: 16),
                          label: const Text('Reload from Disk'),
                          style: TextButton.styleFrom(foregroundColor: Colors.white60),
                        ),
                        const Spacer(),
                        TextButton(
                          onPressed: () => Navigator.pop(ctx),
                          child: const Text('Cancel', style: TextStyle(color: Colors.white54)),
                        ),
                        const SizedBox(width: 8),
                        ElevatedButton.icon(
                          onPressed: isLoading
                              ? null
                              : () async {
                                  try {
                                    final newContent = textCtrl.text;
                                    await Dio().post(
                                      AppApi.url('/api/v1/settings/prompts/save'),
                                      data: {
                                        'name': promptName,
                                        'content': newContent,
                                      },
                                    );
                                    if (ctx.mounted) {
                                      Navigator.pop(ctx);
                                      ScaffoldMessenger.of(context).showSnackBar(
                                        const SnackBar(
                                          backgroundColor: AppColors.bullish,
                                          content: Text('✅ System Prompt saved & active in AI Advisor!'),
                                        ),
                                      );
                                    }
                                  } catch (e) {
                                    if (ctx.mounted) {
                                      ScaffoldMessenger.of(context).showSnackBar(
                                        SnackBar(backgroundColor: AppColors.bearish, content: Text('Save failed: $e')),
                                      );
                                    }
                                  }
                                },
                          icon: const Icon(Icons.save, size: 16, color: Colors.black),
                          label: const Text('Save Changes', style: TextStyle(color: Colors.black, fontWeight: FontWeight.bold)),
                          style: ElevatedButton.styleFrom(backgroundColor: AppColors.bullish),
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
              Dio(BaseOptions(connectTimeout: const Duration(seconds: 90), receiveTimeout: const Duration(seconds: 90)))
                  .post(AppApi.url('/api/v1/settings/prompts/test'))
                  .then((resp) {
                setModalState(() {
                  isTesting = false;
                  responseText = resp.data['ai_response']?.toString() ?? 'No response returned.';
                });
              }).catchError((e) {
                setModalState(() {
                  isTesting = false;
                  responseText = 'Prompt Test Failed: $e\n\nPlease check that your configured AI provider is running and reachable.';
                });
              });
            }

            return Dialog(
              backgroundColor: const Color(0xFF141923),
              shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12), side: const BorderSide(color: Color(0xFF252D3D))),
              child: Container(
                width: 620,
                padding: const EdgeInsets.all(20),
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Row(
                      children: [
                        const Icon(Icons.smart_toy_outlined, color: AppColors.bullish, size: 24),
                        const SizedBox(width: 8),
                        const Text('AI Advisor Prompt Test', style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16, color: Colors.white)),
                        const Spacer(),
                        IconButton(icon: const Icon(Icons.close, color: Colors.white54), onPressed: () => Navigator.pop(ctx)),
                      ],
                    ),
                    const SizedBox(height: 14),
                    if (isTesting)
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 36),
                        child: Center(
                          child: Column(
                            children: [
                              CircularProgressIndicator(color: AppColors.bullish),
                              SizedBox(height: 16),
                              Text('Testing active system prompt with AI Engine...', style: TextStyle(color: Colors.white70)),
                            ],
                          ),
                        ),
                      )
                    else ...[
                      Container(
                        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
                        decoration: BoxDecoration(
                          color: const Color(0xFF1E2533),
                          borderRadius: BorderRadius.circular(6),
                          border: Border.all(color: const Color(0xFF2E82FE).withOpacity(0.4)),
                        ),
                        child: const Row(
                          children: [
                            Icon(Icons.input, size: 14, color: Color(0xFF2E82FE)),
                            SizedBox(width: 6),
                            Text('Test Input: BTC/USDT (LONG) Confluence 80/100', style: TextStyle(fontSize: 12, color: Color(0xFF2E82FE), fontWeight: FontWeight.bold)),
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
                            style: const TextStyle(fontSize: 13, color: Colors.white, height: 1.45),
                          ),
                        ),
                      ),
                      const SizedBox(height: 16),
                      Align(
                        alignment: Alignment.centerRight,
                        child: ElevatedButton(
                          onPressed: () => Navigator.pop(ctx),
                          style: ElevatedButton.styleFrom(backgroundColor: const Color(0xFF1E2533)),
                          child: const Text('Close', style: TextStyle(color: Colors.white)),
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
