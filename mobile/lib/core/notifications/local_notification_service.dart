import 'dart:async';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:shared_preferences/shared_preferences.dart';
import '../../app/theme.dart';

final GlobalKey<ScaffoldMessengerState> rootScaffoldMessengerKey =
    GlobalKey<ScaffoldMessengerState>();

class LocalNotificationService {
  static final LocalNotificationService instance =
      LocalNotificationService._internal();
  LocalNotificationService._internal();

  static const MethodChannel _channel =
      MethodChannel('com.aitrade.ai_trade_advisor/local_notifications');

  final Map<String, String> _lastTradeStatus = {};
  final Map<String, String> _lastProtectionStage = {};
  final Map<String, bool> _lastTp1Filled = {};
  final Set<String> _closedAlertedIds = {};
  final Map<String, DateTime> _recentAlertTimestamps = {};

  int _notificationSeq = 1000;

  Future<void> init() async {
    if (!kIsWeb) {
      try {
        await _channel.invokeMethod('requestPermission');
      } catch (e) {
        debugPrint('[LocalNotifications] requestPermission error: $e');
      }
    }
  }

  Future<bool> _isEnabled() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      return prefs.getBool('fcm_enabled') ?? true;
    } catch (_) {
      return true;
    }
  }

  bool _shouldThrottle(String dedupKey,
      {Duration cooldown = const Duration(seconds: 6)}) {
    final now = DateTime.now();
    final prev = _recentAlertTimestamps[dedupKey];
    if (prev != null && now.difference(prev) < cooldown) {
      return true;
    }
    _recentAlertTimestamps[dedupKey] = now;
    if (_recentAlertTimestamps.length > 200) {
      final cutoff = now.subtract(const Duration(minutes: 10));
      _recentAlertTimestamps.removeWhere((_, ts) => ts.isBefore(cutoff));
    }
    return false;
  }

  String _fmtPrice(dynamic val) {
    final num? n = val is num ? val : num.tryParse(val?.toString() ?? '');
    if (n == null || n == 0) return '-';
    if (n.abs() >= 100) return n.toStringAsFixed(2);
    if (n.abs() >= 1) return n.toStringAsFixed(4);
    return n.toStringAsFixed(6);
  }

  String _formatAction(String dir, dynamic contractMode, String symbol) {
    final upperSym = symbol.toUpperCase();
    final isSpot = contractMode == 'spot' || upperSym.endsWith('/THB');
    final upperDir = dir.toUpperCase();
    if (isSpot) {
      if (upperDir == 'LONG' || upperDir == 'BUY') return 'BUY (SPOT)';
      if (upperDir == 'SHORT' || upperDir == 'SELL') return 'SELL (SPOT)';
    }
    return upperDir;
  }

  Future<void> showNotification({
    required String title,
    required String body,
    Color? accentColor,
    IconData? icon,
    bool force = false,
    String? dedupKey,
    Duration cooldown = const Duration(seconds: 6),
  }) async {
    if (!force && !(await _isEnabled())) {
      return;
    }
    if (!force &&
        dedupKey != null &&
        _shouldThrottle(dedupKey, cooldown: cooldown)) {
      return;
    }

    final int notifId = (++_notificationSeq) % 90000 + 1000;

    // 1. Trigger system sound & haptic feedback
    try {
      await SystemSound.play(SystemSoundType.alert);
      await HapticFeedback.mediumImpact();
    } catch (_) {}

    // 2. Show Android system status-bar & heads-up notification + chime
    if (!kIsWeb) {
      try {
        await _channel.invokeMethod('showNotification', {
          'id': notifId,
          'title': title,
          'body': body,
          'playSound': true,
        });
      } catch (e) {
        debugPrint('[LocalNotifications] Native showNotification error: $e');
      }
    }

    // 3. Show in-app popup banner
    final messenger = rootScaffoldMessengerKey.currentState;
    if (messenger != null) {
      final Color borderColor = accentColor ?? AppColors.bullish;
      messenger.hideCurrentSnackBar();
      messenger.showSnackBar(
        SnackBar(
          behavior: SnackBarBehavior.floating,
          backgroundColor: const Color(0xFF161C2A),
          elevation: 10,
          margin: const EdgeInsets.fromLTRB(12, 0, 12, 16),
          duration: const Duration(seconds: 5),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
            side: BorderSide(color: borderColor.withValues(alpha: 0.85), width: 1.4),
          ),
          content: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(
                icon ?? Icons.notifications_active,
                color: borderColor,
                size: 22,
              ),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      title,
                      style: const TextStyle(
                        color: Colors.white,
                        fontWeight: FontWeight.bold,
                        fontSize: 13,
                      ),
                    ),
                    const SizedBox(height: 3),
                    Text(
                      body,
                      maxLines: 3,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: Colors.white70,
                        fontSize: 11.5,
                        height: 1.3,
                      ),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      );
    }
  }

  Future<void> handleWebSocketEvent(Map<String, dynamic> message) async {
    final type = message['type']?.toString() ?? '';
    final rawData = message['data'];
    if (rawData is! Map) return;
    final data = Map<String, dynamic>.from(rawData);

    if (type == 'auto_trade_executed') {
      final id = data['id']?.toString() ?? '';
      final sym = data['symbol']?.toString() ?? 'UNKNOWN';
      final dir = (data['direction']?.toString() ?? 'long').toUpperCase();
      final contractMode = data['contract_mode'];
      final actionLabel = data['action_label']?.toString() ?? _formatAction(dir, contractMode, sym);
      final isBull = actionLabel.startsWith('BUY') || actionLabel.startsWith('LONG') || dir == 'LONG';
      final status = (data['status']?.toString() ?? 'open').toLowerCase();
      final entry = _fmtPrice(data['entry'] ?? data['entry_price']);
      final sl = _fmtPrice(data['stop_loss']);
      final tp = _fmtPrice(data['take_profit']);

      if (id.isNotEmpty) {
        _lastTradeStatus[id] = status;
      }

      final isPending = status == 'pending' || status == 'new' || status == 'accepted';
      final title = isPending
          ? '📌 ตั้ง Limit Order อัตโนมัติ: $actionLabel $sym'
          : '🚀 เปิดออเดอร์อัตโนมัติ: $actionLabel $sym';
      final body = 'Entry: \$$entry | SL: \$$sl | TP: \$$tp';

      await showNotification(
        title: title,
        body: body,
        accentColor: isBull ? AppColors.bullish : AppColors.bearish,
        icon: isPending ? Icons.schedule_send : Icons.rocket_launch,
        dedupKey: 'order_new:$sym:$dir',
      );
      return;
    }

    if (type == 'trade_updated') {
      final id = data['id']?.toString() ?? '';
      final sym = data['symbol']?.toString() ?? 'UNKNOWN';
      final dir = (data['direction']?.toString() ?? 'long').toUpperCase();
      final contractMode = data['contract_mode'];
      final actionLabel = data['action_label']?.toString() ?? _formatAction(dir, contractMode, sym);
      final isBull = actionLabel.startsWith('BUY') || actionLabel.startsWith('LONG') || dir == 'LONG';
      final status = (data['status']?.toString() ?? '').toLowerCase();
      final protStage = (data['protection_stage']?.toString() ?? 'initial').toLowerCase();
      final tp1Filled = data['tp1_filled'] == true;
      final entry = _fmtPrice(data['entry'] ?? data['entry_price']);
      final sl = _fmtPrice(data['stop_loss']);
      final tp = _fmtPrice(data['take_profit']);

      final prevStatus = id.isNotEmpty ? _lastTradeStatus[id] : null;
      final prevProt = id.isNotEmpty ? (_lastProtectionStage[id] ?? 'initial') : 'initial';
      final prevTp1 = id.isNotEmpty ? (_lastTp1Filled[id] ?? false) : false;

      if (id.isNotEmpty) {
        _lastTradeStatus[id] = status;
        _lastProtectionStage[id] = protStage;
        _lastTp1Filled[id] = tp1Filled;
      }

      // 1. Pending Limit order just got filled -> Open!
      if (status == 'open' &&
          (prevStatus == 'pending' || prevStatus == 'new' || prevStatus == 'accepted')) {
        await showNotification(
          title: '⚡ แมตช์ออเดอร์ Limit แล้ว: $actionLabel $sym',
          body: 'เข้าสถานะที่ราคา \$$entry | SL: \$$sl | TP: \$$tp',
          accentColor: isBull ? AppColors.bullish : AppColors.bearish,
          icon: Icons.bolt,
          dedupKey: 'limit_filled:$id',
        );
        return;
      }

      // 2. Newly seen pending/open order (if not already announced by auto_trade_executed)
      if (prevStatus == null && (status == 'pending' || status == 'open')) {
        final isPending = status == 'pending';
        await showNotification(
          title: isPending
              ? '📌 ตั้งรอ Limit Order: $actionLabel $sym'
              : '🚀 เปิดสถานะใหม่: $actionLabel $sym',
          body: 'Entry: \$$entry | SL: \$$sl | TP: \$$tp',
          accentColor: isBull ? AppColors.bullish : AppColors.bearish,
          icon: isPending ? Icons.pending_actions : Icons.play_circle_fill,
          dedupKey: 'order_new:$sym:$dir',
        );
        return;
      }

      // 3. TP1 Partial Fill
      if (tp1Filled && !prevTp1) {
        await showNotification(
          title: '🎯 TP1 Hit (แบ่งปิดทำกำไร): $sym',
          body: 'ล็อกกำไรบางส่วนเรียบร้อย พร้อมขยับ SL บังหน้าทุน (\$$sl)',
          accentColor: AppColors.bullish,
          icon: Icons.emoji_events,
          dedupKey: 'tp1:$id',
        );
        return;
      }

      // 4. Protection Stage Upgraded (Breakeven / Trailing Stop)
      if (protStage != prevProt &&
          protStage != 'initial' &&
          protStage.isNotEmpty) {
        final label = protStage == 'breakeven'
            ? 'Breakeven บังทุน'
            : 'Trailing Stop ($protStage)';
        await showNotification(
          title: '🛡️ ยกระดับเกราะป้องกันกำไร: $sym',
          body: 'ขยับ Stop Loss มาที่ \$$sl ($label)',
          accentColor: const Color(0xFF00E5FF),
          icon: Icons.shield,
          dedupKey: 'prot:$id:$protStage',
        );
        return;
      }

      // 5. Order Cancelled (Timeout or Opposing Momentum Invalidation)
      if (status == 'cancelled' && (prevStatus == 'pending' || prevStatus == null)) {
        final reason = data['close_reason']?.toString() ?? 'ยกเลิกคำสั่งรอเข้า';
        await showNotification(
          title: '🛑 ยกเลิกออเดอร์รอ Match: $sym',
          body: '$actionLabel $sym | $reason',
          accentColor: const Color(0xFFFFB300),
          icon: Icons.cancel_outlined,
          dedupKey: 'cancelled:$id',
        );
        return;
      }
      return;
    }

    if (type == 'trade_closed') {
      final id = data['id']?.toString() ?? '';
      if (id.isNotEmpty && _closedAlertedIds.contains(id)) {
        return;
      }
      if (id.isNotEmpty) {
        _closedAlertedIds.add(id);
      }

      final sym = data['symbol']?.toString() ?? 'UNKNOWN';
      final reason = data['close_reason']?.toString() ?? 'Closed';
      final closePx = _fmtPrice(data['close_price'] ?? data['exit_price']);
      final num pnl = (data['pnl'] ?? data['realized_pnl_net'] ?? 0) is num
          ? (data['pnl'] ?? data['realized_pnl_net'] ?? 0) as num
          : num.tryParse((data['pnl'] ?? data['realized_pnl_net'] ?? '0').toString()) ?? 0;
      final num pnlPct = (data['pnl_pct'] ?? 0) is num
          ? (data['pnl_pct'] ?? 0) as num
          : num.tryParse((data['pnl_pct'] ?? '0').toString()) ?? 0;

      final isProfit = pnl >= 0;
      final sign = isProfit ? '+' : '';
      final title = isProfit
          ? '🎯 ปิดออเดอร์กำไร: $sym ($sign\$${pnl.toStringAsFixed(2)})'
          : '🛑 ปิดออเดอร์: $sym (\$${pnl.toStringAsFixed(2)})';
      final body =
          '$reason | ปิดที่ \$$closePx | PnL: $sign\$${pnl.toStringAsFixed(2)} ($sign${pnlPct.toStringAsFixed(2)}%)';

      await showNotification(
        title: title,
        body: body,
        accentColor: isProfit ? AppColors.bullish : AppColors.bearish,
        icon: isProfit ? Icons.check_circle : Icons.cancel,
        dedupKey: 'closed:$sym',
      );
      return;
    }

    if (type == 'app_notification') {
      final sym = data['symbol']?.toString() ?? '';
      final dir = (data['direction']?.toString() ?? '').toLowerCase();
      final contractMode = data['contract_mode'];
      final actionLabel = data['action_label']?.toString() ?? _formatAction(dir, contractMode, sym);
      final isBull = actionLabel.startsWith('BUY') || actionLabel.startsWith('LONG') || dir == 'long';
      final isBear = actionLabel.startsWith('SELL') || actionLabel.startsWith('SHORT') || dir == 'short';
      final body = data['body']?.toString() ?? '';
      final rawTitle = data['title']?.toString();
      final title = (rawTitle != null && rawTitle.isNotEmpty)
          ? rawTitle
          : '🔔 Apex Alert: $actionLabel $sym';

      // Avoid duplicate popup when trade_closed or auto_trade_executed already fired
      if (dir == 'closed') {
        if (_shouldThrottle('closed:$sym')) return;
      }
      if (body.contains('[AUTO-PILOT]')) {
        if (_shouldThrottle('order_new:$sym:${dir.toUpperCase()}')) return;
      }

      // Filter routine lifecycle pre-alerts so only actionable states pop up with sound
      if (body.startsWith('Tri-Core lifecycle:')) {
        final upper = body.toUpperCase();
        final isHighPriority = upper.contains('ENTRY_READY') ||
            upper.contains('LIMIT_RETEST_ONLY') ||
            upper.contains('FLOW_CONFIRMED');
        if (!isHighPriority) {
          return;
        }
      }

      await showNotification(
        title: title,
        body: body,
        accentColor: isBull
            ? AppColors.bullish
            : isBear
                ? AppColors.bearish
                : const Color(0xFF00E5FF),
        icon: Icons.crisis_alert,
        dedupKey: 'signal:$sym:$dir',
        cooldown: const Duration(seconds: 30),
      );
    }
  }
}
