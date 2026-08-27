import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'app/router.dart';
import 'app/theme.dart';
import 'core/api/api_client.dart';
import 'core/api/ws_client.dart';

void main() async {
  WidgetsFlutterBinding.ensureInitialized();
  FlutterError.onError = (details) {
    FlutterError.presentError(details);
    debugPrint('[FlutterError] ${details.exceptionAsString()}');
  };
  PlatformDispatcher.instance.onError = (error, stack) {
    debugPrint('[PlatformError] $error\n$stack');
    return true;
  };
  try {
    final prefs = await SharedPreferences.getInstance();
    final savedUrl = prefs.getString('api_base_url');
    if (savedUrl != null && savedUrl.isNotEmpty) {
      AppApi.setBaseUrl(savedUrl);
    }
  } catch (e) {
    debugPrint('[Startup] SharedPreferences error: $e');
  }
  runApp(const ProviderScope(child: AITradeAdvisorApp()));
  WidgetsBinding.instance.addPostFrameCallback((_) {
    AppWebSocketClient.instance.init();
  });
}

class AITradeAdvisorApp extends ConsumerWidget {
  const AITradeAdvisorApp({super.key});

  @override
  Widget build(BuildContext context, WidgetRef ref) {
    final router = ref.watch(routerProvider);
    return MaterialApp.router(
      title: 'AI Trade Advisor',
      theme: AppTheme.dark(),
      darkTheme: AppTheme.dark(),
      themeMode: ThemeMode.dark,
      routerConfig: router,
      debugShowCheckedModeBanner: false,
    );
  }
}
