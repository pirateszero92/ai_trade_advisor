import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:go_router/go_router.dart';
import '../features/chart/chart_screen.dart';
import '../features/signals/signals_screen.dart';
import '../features/journal/journal_screen.dart';
import '../features/settings/settings_screen.dart';
import '../features/chat/chat_screen.dart';
import '../shared/widgets/main_scaffold.dart';

final routerProvider = Provider<GoRouter>((ref) {
  return GoRouter(
    initialLocation: '/chart',
    routes: [
      ShellRoute(
        builder: (context, state, child) => MainScaffold(child: child),
        routes: [
          GoRoute(path: '/chart', builder: (c, s) => const ChartScreen()),
          GoRoute(path: '/signals', builder: (c, s) => const SignalsScreen()),
          GoRoute(path: '/journal', builder: (c, s) => const JournalScreen()),
          GoRoute(path: '/chat', builder: (c, s) => const ChatScreen()),
          GoRoute(path: '/settings', builder: (c, s) => const SettingsScreen()),
        ],
      ),
    ],
  );
});
