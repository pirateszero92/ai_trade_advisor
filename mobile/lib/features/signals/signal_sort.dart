double _confluenceValue(Map<String, dynamic> signal) {
  final value = signal['confluence'];
  if (value is num) return value.toDouble();
  return double.tryParse(value?.toString() ?? '') ?? 0.0;
}

/// Return a new list ordered by confluence (highest first).
///
/// Newer timestamps and then symbols provide deterministic ordering when two
/// signals have the same score. The input list and signal maps are not mutated.
List<Map<String, dynamic>> sortSignalsByConfluenceDescending(
  Iterable<Map<String, dynamic>> signals,
) {
  final sorted = List<Map<String, dynamic>>.of(signals);
  sorted.sort((left, right) {
    final scoreOrder =
        _confluenceValue(right).compareTo(_confluenceValue(left));
    if (scoreOrder != 0) return scoreOrder;

    final timestampOrder = (right['timestamp'] ?? '')
        .toString()
        .compareTo((left['timestamp'] ?? '').toString());
    if (timestampOrder != 0) return timestampOrder;

    return (left['symbol'] ?? '')
        .toString()
        .compareTo((right['symbol'] ?? '').toString());
  });
  return sorted;
}
