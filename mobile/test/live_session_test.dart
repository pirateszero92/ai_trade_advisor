import 'package:flutter_test/flutter_test.dart';
import 'package:ai_trade_advisor/core/api/api_client.dart';
import 'package:ai_trade_advisor/features/settings/settings_screen.dart';

void main() {
  tearDown(AppApi.clearLiveSession);

  test('application state starts in paper mode', () {
    const settings = SettingsState();
    expect(settings.isPaperMode, isTrue);
    expect(AppApi.hasActiveLiveSession, isFalse);
  });

  test('live session is process-memory only and expires closed', () {
    AppApi.setLiveSession(
      token: 'short-lived-test-token',
      expiresAt: DateTime.now().toUtc().add(const Duration(minutes: 1)),
    );
    expect(AppApi.hasActiveLiveSession, isTrue);

    AppApi.clearLiveSession();
    expect(AppApi.hasActiveLiveSession, isFalse);
    expect(AppApi.liveSessionExpiresAt, isNull);
  });

  test('expired live session cannot be installed', () {
    expect(
      () => AppApi.setLiveSession(
        token: 'expired-token',
        expiresAt: DateTime.now().toUtc().subtract(const Duration(seconds: 1)),
      ),
      throwsFormatException,
    );
    expect(AppApi.hasActiveLiveSession, isFalse);
  });
}
