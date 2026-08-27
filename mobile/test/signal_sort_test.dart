import 'package:ai_trade_advisor/features/signals/signal_sort.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('signals are sorted by confluence from highest to lowest', () {
    final source = <Map<String, dynamic>>[
      {'symbol': 'SUI/USDT', 'confluence': 52},
      {'symbol': 'XRP/USDT', 'confluence': 63},
      {'symbol': 'BNB/USDT', 'confluence': 42},
      {'symbol': 'ADA/USDT', 'confluence': '34'},
      {'symbol': 'NEAR/USDT', 'confluence': null},
    ];

    final sorted = sortSignalsByConfluenceDescending(source);

    expect(
      sorted.map((signal) => signal['symbol']),
      ['XRP/USDT', 'SUI/USDT', 'BNB/USDT', 'ADA/USDT', 'NEAR/USDT'],
    );
    expect(source.first['symbol'], 'SUI/USDT');
  });

  test('equal scores use newest timestamp then symbol for stable ordering', () {
    final sorted = sortSignalsByConfluenceDescending([
      {
        'symbol': 'ETH/USDT',
        'confluence': 70,
        'timestamp': '2026-08-26T10:00:00Z',
      },
      {
        'symbol': 'BTC/USDT',
        'confluence': 70,
        'timestamp': '2026-08-26T11:00:00Z',
      },
      {
        'symbol': 'ADA/USDT',
        'confluence': 70,
        'timestamp': '2026-08-26T11:00:00Z',
      },
    ]);

    expect(
      sorted.map((signal) => signal['symbol']),
      ['ADA/USDT', 'BTC/USDT', 'ETH/USDT'],
    );
  });
}
