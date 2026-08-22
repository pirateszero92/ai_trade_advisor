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
      StatefulShellRoute.indexedStack(
        builder: (context, state, navigationShell) => MainScaffold(
          navigationShell: navigationShell,
        ),
        branches: [
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/chart', builder: (c, s) => const ChartScreen()),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/signals', builder: (c, s) => const SignalsScreen()),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/journal', builder: (c, s) => const JournalScreen()),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/chat', builder: (c, s) => const ChatScreen()),
            ],
          ),
          StatefulShellBranch(
            routes: [
              GoRoute(path: '/settings', builder: (c, s) => const SettingsScreen()),
            ],
          ),
        ],
      ),
    ],
  );
});
