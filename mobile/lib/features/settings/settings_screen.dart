import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:dio/dio.dart';
import '../../app/theme.dart';

// ---------------------------------------------------------------------------
// Settings state
// ---------------------------------------------------------------------------

class SettingsState {
  final String apiBaseUrl;
  final String aiProvider; // 'lmstudio' | 'gemini' | 'openrouter'
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

  const SettingsState({
    this.apiBaseUrl = 'http://10.0.2.2:8000',
    this.aiProvider = 'lmstudio',
    this.lmStudioEndpoint = 'http://10.0.2.2:1234/v1',
    this.lmStudioModel = 'local-model',
    this.geminiKey = '',
    this.geminiModel = 'gemini-1.5-pro',
    this.openRouterKey = '',
    this.openRouterModel = 'google/gemini-pro-1.5',
    this.riskPerTrade = 1.0,
    this.maxDailyLoss = 3.0,
    this.maxPositions = 3,
    this.isPaperMode = true,
    this.fcmEnabled = true,
    this.telegramToken = '',
    this.telegramChatId = '',
    this.lineToken = '',
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
      geminiKey: await _storage.read(key: 'gemini_key') ?? '',
      openRouterKey: await _storage.read(key: 'openrouter_key') ?? '',
    );
  }

  Future<void> save(SettingsState newState) async {
    state = newState;
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
    with SingleTickerProviderStateMixin {
  late TabController _aiTabController;

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

  bool _initialized = false;

  @override
  void initState() {
    super.initState();
    _aiTabController = TabController(length: 3, vsync: this);
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
  }

  void _initControllers(SettingsState s) {
    if (_initialized) return;
    _initialized = true;
    _apiUrlCtrl.text = s.apiBaseUrl;
    _lmEndpointCtrl.text = s.lmStudioEndpoint;
    _lmModelCtrl.text = s.lmStudioModel;
    _geminiKeyCtrl.text = s.geminiKey;
    _geminiModelCtrl.text = s.geminiModel;
    _openRouterKeyCtrl.text = s.openRouterKey;
    _openRouterModelCtrl.text = s.openRouterModel;
    _telegramTokenCtrl.text = s.telegramToken;
    _telegramChatIdCtrl.text = s.telegramChatId;
    _lineTokenCtrl.text = s.lineToken;

    // Set AI provider tab
    final tabIndex = {'lmstudio': 0, 'gemini': 1, 'openrouter': 2}[s.aiProvider] ?? 0;
    _aiTabController.animateTo(tabIndex);
  }

  @override
  void dispose() {
    _aiTabController.dispose();
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
    super.dispose();
  }

  Future<void> _save() async {
    final notifier = ref.read(settingsProvider.notifier);
    final current = ref.read(settingsProvider);
    final providers = ['lmstudio', 'gemini', 'openrouter'];
    await notifier.save(current.copyWith(
      apiBaseUrl: _apiUrlCtrl.text.trim(),
      aiProvider: providers[_aiTabController.index],
      lmStudioEndpoint: _lmEndpointCtrl.text.trim(),
      lmStudioModel: _lmModelCtrl.text.trim(),
      geminiKey: _geminiKeyCtrl.text.trim(),
      geminiModel: _geminiModelCtrl.text.trim(),
      openRouterKey: _openRouterKeyCtrl.text.trim(),
      openRouterModel: _openRouterModelCtrl.text.trim(),
      telegramToken: _telegramTokenCtrl.text.trim(),
      telegramChatId: _telegramChatIdCtrl.text.trim(),
      lineToken: _lineTokenCtrl.text.trim(),
    ));
    if (mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Settings saved ✓'),
          backgroundColor: AppColors.bullish,
          duration: Duration(seconds: 2),
        ),
      );
    }
  }

  Future<void> _testConnection(String label) async {
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(
        content: Text('Testing $label connection...'),
        duration: const Duration(seconds: 2),
      ),
    );

    try {
      final dio = Dio();
      final resp = await dio.post(
        'http://127.0.0.1:8000/api/v1/settings/notifications/test',
        data: {
          'telegram_bot_token': _telegramTokenCtrl.text.trim(),
          'telegram_chat_id': _telegramChatIdCtrl.text.trim(),
          'line_notify_token': _lineTokenCtrl.text.trim(),
        },
      );

      final results = resp.data['results'] as Map<String, dynamic>? ?? {};
      final isTg = label.toLowerCase().contains('telegram');
      final isLine = label.toLowerCase().contains('line');
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
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(backgroundColor: AppColors.bearish, content: Text('Test request failed: $e')),
        );
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final settings = ref.watch(settingsProvider);
    _initControllers(settings);

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
            _textField('API Base URL', _apiUrlCtrl, hint: 'http://10.0.2.2:8000'),
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
                Tab(text: 'LM Studio'),
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
                  // LM Studio tab
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Column(
                      children: [
                        _textField('Endpoint', _lmEndpointCtrl, hint: 'http://10.0.2.2:1234/v1'),
                        const SizedBox(height: 8),
                        _textField('Model', _lmModelCtrl, hint: 'local-model'),
                        const SizedBox(height: 8),
                        _testBtn('LM Studio'),
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
                    onPressed: () {
                      // TODO: open prompt editor
                    },
                    icon: const Icon(Icons.edit, size: 16),
                    label: const Text('Edit Prompt'),
                    style: OutlinedButton.styleFrom(foregroundColor: Colors.white70),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _testConnection('Prompt'),
                    icon: const Icon(Icons.play_arrow, size: 16),
                    label: const Text('Test Prompt'),
                    style: OutlinedButton.styleFrom(foregroundColor: AppColors.bullish),
                  ),
                ),
              ],
            ),
          ]),

          const SizedBox(height: 16),

          // ---- Risk Settings ----
          _sectionHeader('⚖️ Risk Settings'),
          _card([
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
                    onTap: () => ref
                        .read(settingsProvider.notifier)
                        .save(settings.copyWith(isPaperMode: true)),
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
      builder: (_) => AlertDialog(
        backgroundColor: AppColors.surface,
        title: const Text('Enable Live Trading?', style: TextStyle(color: Colors.white)),
        content: const Text(
          'This will use real funds. Make sure you understand the risks before proceeding.',
          style: TextStyle(color: Colors.white70),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () {
              ref.read(settingsProvider.notifier).save(settings.copyWith(isPaperMode: false));
              Navigator.pop(context);
            },
            child: const Text('Enable Live', style: TextStyle(color: AppColors.bearish)),
          ),
        ],
      ),
    );
  }
}
