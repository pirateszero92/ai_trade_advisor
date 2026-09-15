import 'dart:math' as math;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:candlesticks/candlesticks.dart';
import 'package:intl/intl.dart' hide TextDirection;
import '../../app/theme.dart';

List<Map<String, dynamic>> selectCleanSmcZones(
  dynamic rawZones,
  dynamic fallbackZone,
  double currentPrice, {
  int maxZones = 1,
}) {
  final zones = <Map<String, dynamic>>[];
  final seen = <String>{};
  if (rawZones is List) {
    for (final item in rawZones) {
      if (item is Map && item['mitigated'] != true) {
        final zone = Map<String, dynamic>.from(item);
        final top = (zone['top'] as num?)?.toDouble();
        final bottom = (zone['bottom'] as num?)?.toDouble();
        if (top == null ||
            bottom == null ||
            !top.isFinite ||
            !bottom.isFinite ||
            top <= bottom) {
          continue;
        }
        final identity = zone['zone_id']?.toString().trim();
        final key = identity != null && identity.isNotEmpty
            ? identity
            : '${zone['direction']}:$top:$bottom';
        if (seen.add(key)) zones.add(zone);
      }
    }
  }
  if (zones.isEmpty &&
      fallbackZone is Map &&
      fallbackZone['mitigated'] != true) {
    final zone = Map<String, dynamic>.from(fallbackZone);
    final top = (zone['top'] as num?)?.toDouble();
    final bottom = (zone['bottom'] as num?)?.toDouble();
    if (top != null &&
        bottom != null &&
        top.isFinite &&
        bottom.isFinite &&
        top > bottom) {
      zones.add(zone);
    }
  }
  if (zones.length <= 1) return zones;
  zones.sort((left, right) {
    double midpoint(Map<String, dynamic> zone) {
      final top = (zone['top'] as num?)?.toDouble() ?? currentPrice;
      final bottom = (zone['bottom'] as num?)?.toDouble() ?? currentPrice;
      return (top + bottom) / 2;
    }

    final distanceOrder = (midpoint(left) - currentPrice)
        .abs()
        .compareTo((midpoint(right) - currentPrice).abs());
    if (distanceOrder != 0) return distanceOrder;
    final leftIndex = (left['confirmed_index'] as num?)?.toInt() ??
        (left['origin_index'] as num?)?.toInt() ??
        0;
    final rightIndex = (right['confirmed_index'] as num?)?.toInt() ??
        (right['origin_index'] as num?)?.toInt() ??
        0;
    return rightIndex.compareTo(leftIndex);
  });
  return zones.take(math.max(1, maxZones)).toList(growable: false);
}

class SMCInteractiveChart extends StatefulWidget {
  final List<Candle> candles;
  final Map<String, dynamic>? smcData;
  final List<Map<String, dynamic>> openPositions;
  final double currentPrice;
  final bool showOverlay;
  final String symbol;

  const SMCInteractiveChart({
    super.key,
    required this.candles,
    required this.smcData,
    required this.openPositions,
    required this.currentPrice,
    required this.showOverlay,
    required this.symbol,
  });

  @override
  State<SMCInteractiveChart> createState() => _SMCInteractiveChartState();
}

class _SMCInteractiveChartState extends State<SMCInteractiveChart> {
  double _candleWidth = 10.0;
  double _scrollOffset = 0.0;
  double _priceScaleMultiplier = 1.0;
  double _priceOffset = 0.0;
  Offset? _hoverOffset;
  double _lastScale = 1.0;
  bool _isDraggingPriceScale = false;
  Map<int, int> _candleIndexByTime = {};

  @override
  void initState() {
    super.initState();
    _recomputeCandleIndex();
  }

  @override
  void didUpdateWidget(covariant SMCInteractiveChart oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (!identical(widget.candles, oldWidget.candles) ||
        widget.candles.length != oldWidget.candles.length) {
      _recomputeCandleIndex();
    }
  }

  void _recomputeCandleIndex() {
    _candleIndexByTime = <int, int>{
      for (int i = 0; i < widget.candles.length; i++)
        widget.candles[i].date.toUtc().millisecondsSinceEpoch: i,
    };
  }

  void _zoomIn() {
    setState(() {
      _candleWidth = (_candleWidth + 2.0).clamp(4.0, 40.0);
    });
  }

  void _zoomOut() {
    setState(() {
      _candleWidth = (_candleWidth - 2.0).clamp(4.0, 40.0);
    });
  }

  void _resetView() {
    setState(() {
      _candleWidth = 10.0;
      _scrollOffset = 0.0;
      _priceScaleMultiplier = 1.0;
      _priceOffset = 0.0;
      _hoverOffset = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (widget.candles.isEmpty) {
      return const Center(
          child: Text('No candles data.',
              style: TextStyle(color: Colors.white54)));
    }

    return LayoutBuilder(
      builder: (context, constraints) {
        final chartWidth =
            math.max(10.0, constraints.maxWidth - 70); // 70px right price axis
        final isOverPriceScale =
            _hoverOffset != null && _hoverOffset!.dx >= chartWidth;

        final maxScrollRight = math.max(
            0.0, (widget.candles.length * _candleWidth) - chartWidth * 0.3);
        final minScrollLeft = math.min(-chartWidth * 0.6,
            maxScrollRight); // Allow smooth panning into future space
        final effectiveScrollOffset =
            _scrollOffset.clamp(minScrollLeft, maxScrollRight);

        return Stack(
          children: [
            // Interactive Chart Canvas
            Positioned.fill(
              child: Listener(
                onPointerSignal: (pointerSignal) {
                  if (pointerSignal is PointerScrollEvent) {
                    setState(() {
                      final isOverAxis =
                          pointerSignal.localPosition.dx >= chartWidth;
                      if (isOverAxis) {
                        // Vertical price scale zoom (TradingView style)
                        if (pointerSignal.scrollDelta.dy < 0) {
                          _priceScaleMultiplier =
                              (_priceScaleMultiplier * 1.12).clamp(0.1, 10.0);
                        } else {
                          _priceScaleMultiplier =
                              (_priceScaleMultiplier / 1.12).clamp(0.1, 10.0);
                        }
                      } else {
                        // Horizontal candle width zoom
                        if (pointerSignal.scrollDelta.dy < 0) {
                          _candleWidth = (_candleWidth + 1.2).clamp(4.0, 40.0);
                        } else {
                          _candleWidth = (_candleWidth - 1.2).clamp(4.0, 40.0);
                        }
                      }
                    });
                  }
                },
                child: GestureDetector(
                  onDoubleTap: () {
                    // Double-click on right price axis or chart to Auto-fit
                    setState(() {
                      _priceScaleMultiplier = 1.0;
                      _priceOffset = 0.0;
                    });
                  },
                  onScaleStart: (details) {
                    _lastScale = 1.0;
                    _isDraggingPriceScale =
                        details.localFocalPoint.dx >= chartWidth;
                  },
                  onScaleUpdate: (details) {
                    setState(() {
                      if (details.scale == 1.0) {
                        if (_isDraggingPriceScale) {
                          // Dragging right price axis up/down -> scale price vertically!
                          // dy < 0 (drag up) -> stretch/zoom in; dy > 0 (drag down) -> compress/zoom out
                          final dy = details.focalPointDelta.dy;
                          if (dy != 0) {
                            final factor = math.pow(0.985, dy).toDouble();
                            _priceScaleMultiplier =
                                (_priceScaleMultiplier * factor)
                                    .clamp(0.1, 10.0);
                          }
                        } else {
                          // Pan chart horizontally
                          _scrollOffset =
                              (_scrollOffset + details.focalPointDelta.dx)
                                  .clamp(minScrollLeft, maxScrollRight);
                          _hoverOffset = details.localFocalPoint;
                        }
                      } else {
                        // Pinch Zoom
                        final scaleDelta = details.scale / _lastScale;
                        _lastScale = details.scale;
                        if (_isDraggingPriceScale) {
                          _priceScaleMultiplier =
                              (_priceScaleMultiplier * scaleDelta)
                                  .clamp(0.1, 10.0);
                        } else {
                          _candleWidth =
                              (_candleWidth * scaleDelta).clamp(4.0, 40.0);
                        }
                      }
                    });
                  },
                  onScaleEnd: (_) {
                    _isDraggingPriceScale = false;
                    setState(() {
                      _hoverOffset = null;
                    });
                  },
                  onTapDown: (details) {
                    setState(() {
                      _hoverOffset = details.localPosition;
                    });
                  },
                  onTapUp: (_) {
                    setState(() {
                      _hoverOffset = null;
                    });
                  },
                  child: MouseRegion(
                    cursor: isOverPriceScale
                        ? SystemMouseCursors.resizeUpDown
                        : SystemMouseCursors.basic,
                    onHover: (event) {
                      setState(() {
                        _hoverOffset = event.localPosition;
                      });
                    },
                    onExit: (_) {
                      setState(() {
                        _hoverOffset = null;
                      });
                    },
                    child: CustomPaint(
                      size: Size(constraints.maxWidth, constraints.maxHeight),
                      painter: _SMCUnifiedPainter(
                        candles: widget.candles,
                        candleIndexByTime: _candleIndexByTime,
                        smcData: widget.smcData,
                        openPositions: widget.openPositions,
                        currentPrice: widget.currentPrice,
                        showOverlay: widget.showOverlay,
                        symbol: widget.symbol,
                        candleWidth: _candleWidth,
                        scrollOffset: effectiveScrollOffset,
                        priceScaleMultiplier: _priceScaleMultiplier,
                        priceOffset: _priceOffset,
                        hoverOffset: _hoverOffset,
                      ),
                    ),
                  ),
                ),
              ),
            ),

            // Top-Left Floating Controls: Zoom In / Zoom Out / Reset
            Positioned(
              top: 10,
              left: 10,
              child: Row(
                children: [
                  _controlBtn(
                      icon: Icons.add, tooltip: 'Zoom In', onTap: _zoomIn),
                  const SizedBox(width: 6),
                  _controlBtn(
                      icon: Icons.remove, tooltip: 'Zoom Out', onTap: _zoomOut),
                  const SizedBox(width: 6),
                  _controlBtn(
                      icon: Icons.restart_alt,
                      tooltip: 'Reset Zoom & Pan (Auto-fit)',
                      onTap: _resetView),
                ],
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _controlBtn(
      {required IconData icon,
      required String tooltip,
      required VoidCallback onTap}) {
    return Container(
      decoration: BoxDecoration(
        color: const Color(0xFF1E2533).withValues(alpha: 0.9),
        borderRadius: BorderRadius.circular(6),
        border: Border.all(color: AppColors.border),
      ),
      child: Material(
        color: Colors.transparent,
        child: InkWell(
          borderRadius: BorderRadius.circular(6),
          onTap: onTap,
          child: Tooltip(
            message: tooltip,
            child: Padding(
              padding: const EdgeInsets.all(6),
              child: Icon(icon, size: 16, color: Colors.white70),
            ),
          ),
        ),
      ),
    );
  }
}

class _SMCUnifiedPainter extends CustomPainter {
  final List<Candle> candles;
  final Map<int, int> candleIndexByTime;
  final Map<String, dynamic>? smcData;
  final List<Map<String, dynamic>> openPositions;
  final double currentPrice;
  final bool showOverlay;
  final String symbol;
  final double candleWidth;
  final double scrollOffset;
  final double priceScaleMultiplier;
  final double priceOffset;
  final Offset? hoverOffset;

  _SMCUnifiedPainter({
    required this.candles,
    required this.candleIndexByTime,
    required this.smcData,
    required this.openPositions,
    required this.currentPrice,
    required this.showOverlay,
    required this.symbol,
    required this.candleWidth,
    required this.scrollOffset,
    required this.priceScaleMultiplier,
    required this.priceOffset,
    required this.hoverOffset,
  });

  @override
  void paint(Canvas canvas, Size size) {
    const priceAxisWidth = 70.0;
    const timeAxisHeight = 26.0;

    final chartWidth = size.width - priceAxisWidth;
    final chartHeight = size.height - timeAxisHeight;

    if (chartWidth <= 0 || chartHeight <= 0 || candles.isEmpty) return;

    // 1. Calculate visible candle range based on scroll offset and candle width
    // Candles are ordered [0: newest, ..., N: oldest]
    // Index 0 is displayed at: chartWidth - rightPadding + scrollOffset
    const rightPadding = 20.0;
    final rawStart = ((scrollOffset - rightPadding) / candleWidth).floor() - 1;
    final startIndex = rawStart.clamp(0, candles.length - 1);
    final rawEnd =
        ((chartWidth - rightPadding + scrollOffset) / candleWidth).ceil() + 3;
    final endIndex = rawEnd.clamp(startIndex, candles.length - 1);

    if (startIndex > endIndex || startIndex >= candles.length) return;

    final visibleCandles = candles.sublist(startIndex, endIndex + 1);

    // 2. Find min and max price within the VISIBLE candles
    double minPrice = visibleCandles.map((c) => c.low).reduce(math.min);
    double maxPrice = visibleCandles.map((c) => c.high).reduce(math.max);

    // Auto-fit follows visible candles only. Distant historical SMC zones must
    // never compress candle bodies just to force an old level onto the screen.
    if (currentPrice > 0) {
      minPrice = math.min(minPrice, currentPrice);
      maxPrice = math.max(maxPrice, currentPrice);
    }

    // Apply user vertical price scale multiplier & vertical pan offset
    final basePriceSpan = maxPrice - minPrice;
    final paddedMinPrice =
        minPrice - (basePriceSpan > 0 ? basePriceSpan * 0.06 : 1.0);
    final paddedMaxPrice =
        maxPrice + (basePriceSpan > 0 ? basePriceSpan * 0.06 : 1.0);
    final baseMidPrice = (paddedMaxPrice + paddedMinPrice) / 2.0;
    final baseHalfSpan = (paddedMaxPrice - paddedMinPrice) / 2.0;

    // With price scale multiplier (>1 = stretch candles / zoom in, <1 = compress candles / zoom out):
    final scaledHalfSpan = baseHalfSpan / priceScaleMultiplier;
    final effectiveMidPrice = baseMidPrice + priceOffset;
    final effectiveMinPrice = effectiveMidPrice - scaledHalfSpan;
    final effectiveMaxPrice = effectiveMidPrice + scaledHalfSpan;
    final effectiveSpan = effectiveMaxPrice - effectiveMinPrice;

    // Coordinate conversion function
    double priceToY(double price) {
      if (effectiveSpan <= 0) return chartHeight * 0.5;
      final ratio = (effectiveMaxPrice - price) / effectiveSpan;
      return ratio * chartHeight;
    }

    double yToPrice(double y) {
      final ratio = y / chartHeight;
      return effectiveMaxPrice - (ratio * effectiveSpan);
    }

    double candleIndexToX(int index) {
      return chartWidth - rightPadding - (index * candleWidth) + scrollOffset;
    }

    int? candleIndexForTime(dynamic rawTime) {
      if (rawTime == null) return null;
      final parsed = DateTime.tryParse(rawTime.toString());
      if (parsed == null) return null;
      final targetMs = parsed.toUtc().millisecondsSinceEpoch;
      final exact = candleIndexByTime[targetMs];
      if (exact != null) return exact;

      // Nearest candle fallback
      int? bestIndex;
      int minDiff = 9007199254740991;
      for (int i = 0; i < candles.length; i++) {
        final diff =
            (candles[i].date.toUtc().millisecondsSinceEpoch - targetMs).abs();
        if (diff < minDiff) {
          minDiff = diff;
          bestIndex = i;
        }
      }
      return bestIndex;
    }

    int? candleIndexForAnchor(dynamic rawTime, dynamic rawIndex) {
      final byTime = candleIndexForTime(rawTime);
      if (byTime != null) return byTime;
      final chronologicalIndex = (rawIndex as num?)?.toInt();
      if (chronologicalIndex == null || chronologicalIndex < 0) return null;
      // Engine indices run oldest -> newest; the painter stores candles in
      // reverse chronological order. The formula also accounts for an
      // optional forming candle at index zero.
      final reversedIndex = candles.length - 1 - chronologicalIndex;
      if (reversedIndex < 0 || reversedIndex >= candles.length) return null;
      return reversedIndex;
    }

    final latestCandleRight =
        (candleIndexToX(0) + candleWidth * 0.5).clamp(0.0, chartWidth);

    // ------------------------------------------------------------------------
    // A. Draw Grid Lines & Price Axis Labels
    // ------------------------------------------------------------------------
    final gridPaint = Paint()
      ..color = const Color(0xFF1E2533).withValues(alpha: 0.6)
      ..strokeWidth = 1.0;

    const numPriceTicks = 7;
    for (int i = 0; i <= numPriceTicks; i++) {
      final y = (chartHeight / numPriceTicks) * i;
      canvas.drawLine(Offset(0, y), Offset(chartWidth, y), gridPaint);

      // Price label on right axis
      final priceAtTick = yToPrice(y);
      final priceStr = _formatPrice(priceAtTick);
      _drawText(
        canvas,
        text: priceStr,
        offset: Offset(chartWidth + 6, y - 6),
        style: const TextStyle(
            fontSize: 10, color: Colors.white54, fontFamily: 'monospace'),
      );
    }

    // Keep time-grid strokes behind the market data, as TradingView does.
    // Previously they were drawn inside the candle loop and covered bodies.
    final timeLabelStep = math.max(1, (60 / candleWidth).round());
    for (int i = startIndex; i <= endIndex; i++) {
      if (i % timeLabelStep != 0) continue;
      final x = candleIndexToX(i);
      if (x < 0 || x > chartWidth) continue;
      canvas.drawLine(Offset(x, 0), Offset(x, chartHeight),
          Paint()..color = const Color(0xFF1E2533).withValues(alpha: 0.4));
      final timeStr =
          DateFormat('MM/dd HH:mm').format(candles[i].date.toLocal());
      _drawText(
        canvas,
        text: timeStr,
        offset: Offset(x - 30, chartHeight + 6),
        style: const TextStyle(
            fontSize: 9, color: Colors.white38, fontFamily: 'monospace'),
      );
    }

    // Right axis vertical divider
    canvas.drawLine(Offset(chartWidth, 0), Offset(chartWidth, chartHeight),
        Paint()..color = AppColors.border);
    // Bottom time axis horizontal divider
    canvas.drawLine(Offset(0, chartHeight), Offset(size.width, chartHeight),
        Paint()..color = AppColors.border);

    // ------------------------------------------------------------------------
    // B. Draw SMC Overlays (Order Blocks, FVGs, EQ 50%) — SYNCHRONIZED TO PRICE
    // ------------------------------------------------------------------------
    if (showOverlay && smcData != null) {
      canvas.save();
      canvas.clipRect(Rect.fromLTWH(0, 0, chartWidth, chartHeight));

      // 1. Draw Order Blocks (OB) - TradingView LuxAlgo Multi-block Extend-Right
      final obList = selectCleanSmcZones(
          smcData!['order_blocks'], smcData!['order_block'], currentPrice,
          maxZones: 5);

      for (final ob in obList) {
        final obTop = (ob['top'] as num?)?.toDouble();
        final obBottom = (ob['bottom'] as num?)?.toDouble();
        final isBullish =
            (ob['direction'] as String? ?? 'bullish') == 'bullish';
        final isSwing = (ob['source'] as String? ?? 'swing') == 'swing';

        if (obTop != null && obBottom != null) {
          final yTop = priceToY(obTop);
          final yBottom = priceToY(obBottom);
          final boxY = math.min(yTop, yBottom);
          final boxH = (yTop - yBottom).abs().clamp(4.0, chartHeight);

          final obColor = isBullish
              ? (isSwing ? const Color(0xFF1848CC) : const Color(0xFF1E60E6))
              : (isSwing ? const Color(0xFFB22833) : const Color(0xFFD32F2F));
          final obBorderColor =
              isBullish ? const Color(0xFF3179F5) : const Color(0xFFF77C80);

          final originIndex = candleIndexForTime(ob['timestamp']);
          final naturalBoxX = originIndex != null
              ? (candleIndexToX(originIndex) - candleWidth * 0.5)
              : (latestCandleRight - chartWidth * 0.5);
          final boxX = naturalBoxX.clamp(0.0, chartWidth);
          // TradingView LuxAlgo extends unmitigated blocks to the current/future bar
          final boxRight = math
              .max(boxX + candleWidth, latestCandleRight + candleWidth * 1.5)
              .clamp(0.0, chartWidth);

          if (boxRight > boxX) {
            final rect = Rect.fromLTWH(boxX, boxY, boxRight - boxX, boxH);
            canvas.drawRect(
                rect,
                Paint()
                  ..color = obColor.withValues(alpha: isSwing ? 0.20 : 0.14));

            final obBorder = Paint()
              ..color = obBorderColor.withValues(alpha: 0.85)
              ..strokeWidth = isSwing ? 1.4 : 1.0
              ..style = PaintingStyle.stroke;
            canvas.drawLine(
                Offset(boxX, boxY), Offset(boxRight, boxY), obBorder);
            canvas.drawLine(Offset(boxX, boxY + boxH),
                Offset(boxRight, boxY + boxH), obBorder);
            // Left edge vertical border
            canvas.drawLine(
                Offset(boxX, boxY), Offset(boxX, boxY + boxH), obBorder);

            // Discreet TradingView-style mini tag
            final obTag = isBullish
                ? (isSwing ? '+OB (Swing)' : '+OB')
                : (isSwing ? '-OB (Swing)' : '-OB');
            _drawText(
              canvas,
              text: obTag,
              offset: Offset(boxX + 4, boxY + 2),
              style: TextStyle(
                fontSize: 9,
                fontWeight: FontWeight.w600,
                color: obBorderColor.withValues(alpha: 0.9),
                fontFamily: 'monospace',
              ),
            );
          }
        }
      }

      // 2. Draw Fair Value Gaps (FVG)
      final fvgList = selectCleanSmcZones(
          smcData!['fvgs'], smcData!['fvg'], currentPrice,
          maxZones: 3);

      for (final fvg in fvgList) {
        final fvgTop = (fvg['top'] as num?)?.toDouble();
        final fvgBottom = (fvg['bottom'] as num?)?.toDouble();
        final isBullish =
            (fvg['direction'] as String? ?? 'bullish') == 'bullish';

        if (fvgTop != null && fvgBottom != null) {
          final yTop = priceToY(fvgTop);
          final yBottom = priceToY(fvgBottom);
          final boxY = math.min(yTop, yBottom);
          final boxH = (yTop - yBottom).abs().clamp(4.0, chartHeight);

          final fvgColor =
              isBullish ? const Color(0xFF00FF68) : const Color(0xFFFF0008);
          final originIndex = candleIndexForTime(fvg['timestamp']);
          final naturalBoxX = originIndex != null
              ? (candleIndexToX(originIndex) - candleWidth * 0.5)
              : (latestCandleRight - chartWidth * 0.45);
          final boxX = naturalBoxX.clamp(0.0, chartWidth);
          final boxRight = math
              .max(boxX + candleWidth, latestCandleRight + candleWidth * 1.2)
              .clamp(0.0, chartWidth);

          if (boxRight > boxX) {
            final rect = Rect.fromLTWH(boxX, boxY, boxRight - boxX, boxH);
            canvas.drawRect(
                rect, Paint()..color = fvgColor.withValues(alpha: 0.10));
            final fvgBorder = Paint()
              ..color = fvgColor.withValues(alpha: 0.75)
              ..strokeWidth = 1.0
              ..style = PaintingStyle.stroke;
            canvas.drawLine(
                Offset(boxX, boxY), Offset(boxRight, boxY), fvgBorder);
            canvas.drawLine(Offset(boxX, boxY + boxH),
                Offset(boxRight, boxY + boxH), fvgBorder);
          }
        }
      }

      // 3. Draw Historical Swing Structure Breaks (BOS / CHoCH)
      final rawSwingStructs =
          smcData!['swing_structures'] as List<dynamic>? ?? [];
      for (final raw in rawSwingStructs) {
        if (raw is! Map) continue;
        final level = (raw['level'] as num?)?.toDouble();
        final tag = raw['tag']?.toString() ?? 'BOS';
        final isBullish =
            (raw['direction']?.toString() ?? 'bullish') == 'bullish';
        final pivotIndex = candleIndexForTime(raw['pivot_time']);
        final breakIndex = candleIndexForTime(raw['break_time']);
        if (level != null && pivotIndex != null && breakIndex != null) {
          final y = priceToY(level);
          if (y >= 0 && y <= chartHeight) {
            final xPivot = candleIndexToX(pivotIndex);
            final xBreak = candleIndexToX(breakIndex);
            final lineLeft = math.min(xPivot, xBreak).clamp(0.0, chartWidth);
            final lineRight = math.max(xPivot, xBreak).clamp(0.0, chartWidth);
            if (lineRight > lineLeft + 4) {
              final color =
                  isBullish ? const Color(0xFF00E676) : const Color(0xFFFF5252);
              final structPaint = Paint()
                ..color = color.withValues(alpha: 0.85)
                ..strokeWidth = 1.2;
              _drawDashedLine(canvas, Offset(lineLeft, y), Offset(lineRight, y),
                  structPaint);

              // Pill Tag placed cleanly on the line
              final tagX = ((lineLeft + lineRight) / 2.0 - 18)
                  .clamp(lineLeft, math.max(lineLeft, lineRight - 42))
                  .toDouble();
              _drawPillTag(
                canvas,
                text: tag,
                offset: Offset(tagX, y - 11),
                bgColor: const Color(0xFF111722).withValues(alpha: 0.92),
                textColor: color,
                borderColor: color.withValues(alpha: 0.7),
              );
            }
          }
        }
      }

      // 3.1 Draw Historical Internal Structures (I-BOS / I-CHoCH)
      final rawInternalStructs =
          smcData!['internal_structures'] as List<dynamic>? ?? [];
      for (final raw in rawInternalStructs) {
        if (raw is! Map) continue;
        final level = (raw['level'] as num?)?.toDouble();
        final tag = raw['tag']?.toString() ?? 'BOS';
        final isBullish =
            (raw['direction']?.toString() ?? 'bullish') == 'bullish';
        final pivotIndex = candleIndexForTime(raw['pivot_time']);
        final breakIndex = candleIndexForTime(raw['break_time']);
        if (level != null && pivotIndex != null && breakIndex != null) {
          final y = priceToY(level);
          if (y >= 0 && y <= chartHeight) {
            final xPivot = candleIndexToX(pivotIndex);
            final xBreak = candleIndexToX(breakIndex);
            final lineLeft = math.min(xPivot, xBreak).clamp(0.0, chartWidth);
            final lineRight = math.max(xPivot, xBreak).clamp(0.0, chartWidth);
            if (lineRight > lineLeft + 4) {
              final color =
                  isBullish ? const Color(0xFF26A69A) : const Color(0xFFEF5350);
              final internalPaint = Paint()
                ..color = color.withValues(alpha: 0.65)
                ..strokeWidth = 0.9;
              _drawDottedLine(canvas, Offset(lineLeft, y), Offset(lineRight, y),
                  internalPaint);

              final tagLabel = 'I-$tag';
              final tagX =
                  (lineRight - 36).clamp(lineLeft, chartWidth - 40).toDouble();
              _drawPillTag(
                canvas,
                text: tagLabel,
                offset: Offset(tagX, y - 9),
                bgColor: const Color(0xFF0F1520).withValues(alpha: 0.88),
                textColor: color,
                borderColor: color.withValues(alpha: 0.45),
              );
            }
          }
        }
      }

      // 3.2 Draw Equal Highs / Equal Lows (Liquidity pools)
      final eqhList = smcData!['equal_high_levels'] as List<dynamic>? ?? [];
      for (final item in eqhList) {
        if (item is! Map) continue;
        final price = (item['price'] as num?)?.toDouble();
        if (price == null) continue;
        final y = priceToY(price);
        if (y >= 0 && y <= chartHeight) {
          final p1Idx = candleIndexForAnchor(
              item['first_origin_timestamp'], item['first_origin_index']);
          final p2Idx = candleIndexForAnchor(
              item['origin_timestamp'], item['origin_index']);
          final x1 = p1Idx != null
              ? candleIndexToX(p1Idx)
              : (latestCandleRight - chartWidth * 0.3);
          final x2 = p2Idx != null ? candleIndexToX(p2Idx) : latestCandleRight;
          final left = math.min(x1, x2).clamp(0.0, chartWidth);
          final right = math.max(x1, x2).clamp(0.0, chartWidth);
          if (right > left + 4) {
            final eqhPaint = Paint()
              ..color = const Color(0xFFFFB74D).withValues(alpha: 0.8)
              ..strokeWidth = 1.0;
            _drawDashedLine(
                canvas, Offset(left, y), Offset(right, y), eqhPaint);
            final tagX = ((left + right) / 2.0 - 14)
                .clamp(left, chartWidth - 30)
                .toDouble();
            _drawPillTag(
              canvas,
              text: 'EQH',
              offset: Offset(tagX, y - 11),
              bgColor: const Color(0xFF2A1C0A),
              textColor: const Color(0xFFFFB74D),
              borderColor: const Color(0xFFFFB74D).withValues(alpha: 0.6),
            );
          }
        }
      }

      final eqlList = smcData!['equal_low_levels'] as List<dynamic>? ?? [];
      for (final item in eqlList) {
        if (item is! Map) continue;
        final price = (item['price'] as num?)?.toDouble();
        if (price == null) continue;
        final y = priceToY(price);
        if (y >= 0 && y <= chartHeight) {
          final p1Idx = candleIndexForAnchor(
              item['first_origin_timestamp'], item['first_origin_index']);
          final p2Idx = candleIndexForAnchor(
              item['origin_timestamp'], item['origin_index']);
          final x1 = p1Idx != null
              ? candleIndexToX(p1Idx)
              : (latestCandleRight - chartWidth * 0.3);
          final x2 = p2Idx != null ? candleIndexToX(p2Idx) : latestCandleRight;
          final left = math.min(x1, x2).clamp(0.0, chartWidth);
          final right = math.max(x1, x2).clamp(0.0, chartWidth);
          if (right > left + 4) {
            final eqlPaint = Paint()
              ..color = const Color(0xFF4DD0E1).withValues(alpha: 0.8)
              ..strokeWidth = 1.0;
            _drawDashedLine(
                canvas, Offset(left, y), Offset(right, y), eqlPaint);
            final tagX = ((left + right) / 2.0 - 14)
                .clamp(left, chartWidth - 30)
                .toDouble();
            _drawPillTag(
              canvas,
              text: 'EQL',
              offset: Offset(tagX, y + 2),
              bgColor: const Color(0xFF0A242A),
              textColor: const Color(0xFF4DD0E1),
              borderColor: const Color(0xFF4DD0E1).withValues(alpha: 0.6),
            );
          }
        }
      }

      // 4. Context rays & Strong/Weak Extremes
      final contextRayLeft =
          math.max(0.0, latestCandleRight - chartWidth * 0.42);
      final swHigh = smcData!['strong_weak_high'] as Map<String, dynamic>?;
      if (swHigh != null) {
        final price = (swHigh['price'] as num?)?.toDouble();
        final label = swHigh['label']?.toString() ?? 'High';
        if (price != null) {
          final y = priceToY(price);
          if (y >= 0 && y <= chartHeight) {
            final p = Paint()
              ..color = AppColors.bearish.withValues(alpha: 0.5)
              ..strokeWidth = 0.8;
            final originIndex =
                candleIndexForAnchor(swHigh['time'], swHigh['origin_index']);
            final rayLeft = originIndex != null
                ? candleIndexToX(originIndex).clamp(0.0, latestCandleRight)
                : contextRayLeft;
            _drawDashedLine(
                canvas, Offset(rayLeft, y), Offset(latestCandleRight, y), p);
            _drawPillTag(
              canvas,
              text: '$label: ${_formatPrice(price)}',
              offset: Offset(rayLeft + 4, y - 14),
              bgColor: const Color(0xFF251A1E),
              textColor: AppColors.bearish,
            );
          }
        }
      }

      final swLow = smcData!['strong_weak_low'] as Map<String, dynamic>?;
      if (swLow != null) {
        final price = (swLow['price'] as num?)?.toDouble();
        final label = swLow['label']?.toString() ?? 'Low';
        if (price != null) {
          final y = priceToY(price);
          if (y >= 0 && y <= chartHeight) {
            final p = Paint()
              ..color = AppColors.bullish.withValues(alpha: 0.5)
              ..strokeWidth = 0.8;
            final originIndex =
                candleIndexForAnchor(swLow['time'], swLow['origin_index']);
            final rayLeft = originIndex != null
                ? candleIndexToX(originIndex).clamp(0.0, latestCandleRight)
                : contextRayLeft;
            _drawDashedLine(
                canvas, Offset(rayLeft, y), Offset(latestCandleRight, y), p);
            _drawPillTag(
              canvas,
              text: '$label: ${_formatPrice(price)}',
              offset: Offset(rayLeft + 4, y + 2),
              bgColor: const Color(0xFF1A251E),
              textColor: AppColors.bullish,
            );
          }
        }
      }

      // 5. Equilibrium context ray
      final eq = (smcData!['equilibrium'] as num?)?.toDouble();
      if (eq != null) {
        final y = priceToY(eq);
        if (y >= 0 && y <= chartHeight) {
          final eqPaint = Paint()
            ..color = const Color(0xFFFFD700)
            ..strokeWidth = 1.0
            ..style = PaintingStyle.stroke;
          _drawDashedLine(canvas, Offset(contextRayLeft, y),
              Offset(latestCandleRight, y), eqPaint);

          _drawPillTag(
            canvas,
            text: 'EQ 50% (${_formatPrice(eq)})',
            offset: Offset(
                (latestCandleRight - 112).clamp(0.0, chartWidth - 112), y - 16),
            bgColor: const Color(0xFF332B00),
            textColor: const Color(0xFFFFD700),
            borderColor: const Color(0xFFFFD700),
          );
        }
      }

      canvas.restore();
    }

    // ------------------------------------------------------------------------
    // C. Draw Open Position Levels (Entry, SL, TP) for Current Symbol
    // ------------------------------------------------------------------------
    for (final pos in openPositions) {
      final posSym = pos['symbol']?.toString() ?? '';
      if (posSym == symbol) {
        final entry = (pos['entry'] as num?)?.toDouble();
        final sl = (pos['stop_loss'] as num?)?.toDouble();
        final tp = (pos['take_profit'] as num?)?.toDouble();
        final isLong =
            (pos['direction']?.toString() ?? 'long').toLowerCase() == 'long';

        if (entry != null) {
          final y = priceToY(entry);
          final p = Paint()
            ..color = const Color(0xFF00E5FF)
            ..strokeWidth = 1.2;
          _drawDashedLine(canvas, Offset(0, y), Offset(chartWidth, y), p);
          _drawPillTag(
            canvas,
            text:
                '${isLong ? 'LONG' : 'SHORT'} ENTRY @ \$${_formatPrice(entry)}',
            offset: Offset(12, y - 16),
            bgColor: const Color(0xFF00E5FF),
            textColor: Colors.black,
          );
        }
        if (sl != null) {
          final y = priceToY(sl);
          final p = Paint()
            ..color = AppColors.bearish
            ..strokeWidth = 1.2;
          _drawDashedLine(canvas, Offset(0, y), Offset(chartWidth, y), p);
          _drawPillTag(
            canvas,
            text: 'SL @ \$${_formatPrice(sl)}',
            offset: Offset(chartWidth - 130, y - 16),
            bgColor: AppColors.bearish,
            textColor: Colors.black,
          );
        }
        if (tp != null) {
          final y = priceToY(tp);
          final p = Paint()
            ..color = AppColors.bullish
            ..strokeWidth = 1.2;
          _drawDashedLine(canvas, Offset(0, y), Offset(chartWidth, y), p);
          _drawPillTag(
            canvas,
            text: 'TP @ \$${_formatPrice(tp)}',
            offset: Offset(chartWidth - 130, y - 16),
            bgColor: AppColors.bullish,
            textColor: Colors.black,
          );
        }
      }
    }

    // ------------------------------------------------------------------------
    // D. Draw Candlesticks & Volume Bars
    // ------------------------------------------------------------------------
    final bodyWidth = (candleWidth * 0.72).clamp(1.5, 30.0);
    final maxVol = visibleCandles.map((c) => c.volume).fold(1.0, math.max);
    final volAreaHeight = chartHeight * 0.18;

    for (int i = startIndex; i <= endIndex; i++) {
      final c = candles[i];
      final x = candleIndexToX(i);

      if (x < -candleWidth || x > chartWidth + candleWidth) continue;

      final isBull = c.close >= c.open;
      final candleColor =
          isBull ? const Color(0xFF089981) : const Color(0xFFF23645);

      // Volume Bar (at bottom of chart)
      final volH = maxVol > 0 ? (c.volume / maxVol) * volAreaHeight : 0.0;
      final volRect = Rect.fromLTWH(
          x - (bodyWidth * 0.5), chartHeight - volH, bodyWidth, volH);
      canvas.drawRect(
          volRect, Paint()..color = candleColor.withValues(alpha: 0.35));

      // Wick (High to Low)
      final yHigh = priceToY(c.high);
      final yLow = priceToY(c.low);
      final wickPaint = Paint()
        ..color = candleColor
        ..strokeWidth = 1.2;
      canvas.drawLine(Offset(x, yHigh), Offset(x, yLow), wickPaint);

      // Candle Body (Open to Close)
      final yOpen = priceToY(c.open);
      final yClose = priceToY(c.close);
      final bodyTop = math.min(yOpen, yClose);
      final bodyHeight = math.max(1.5, (yOpen - yClose).abs());

      final bodyRect =
          Rect.fromLTWH(x - (bodyWidth * 0.5), bodyTop, bodyWidth, bodyHeight);
      canvas.drawRect(bodyRect, Paint()..color = candleColor);
    }

    // TradingView-style 20-period volume moving average.
    final volumeMaPaint = Paint()
      ..color = const Color(0xFF2962FF)
      ..strokeWidth = 1.6
      ..style = PaintingStyle.stroke;
    final volumeMaPath = Path();
    var volumeMaStarted = false;
    for (int i = endIndex; i >= startIndex; i--) {
      final maEnd = math.min(candles.length, i + 20);
      var total = 0.0;
      for (int j = i; j < maEnd; j++) {
        total += candles[j].volume;
      }
      final average = total / (maEnd - i);
      final x = candleIndexToX(i);
      final y =
          chartHeight - (average / maxVol).clamp(0.0, 1.0) * volAreaHeight;
      if (!volumeMaStarted) {
        volumeMaPath.moveTo(x, y);
        volumeMaStarted = true;
      } else {
        volumeMaPath.lineTo(x, y);
      }
    }
    if (volumeMaStarted) canvas.drawPath(volumeMaPath, volumeMaPaint);

    // ------------------------------------------------------------------------
    // E. Draw Current Live Price Line & Right Axis Badge
    // ------------------------------------------------------------------------
    if (currentPrice > 0) {
      final curY = priceToY(currentPrice);
      if (curY >= 0 && curY <= chartHeight) {
        final currentColor = candles.first.close >= candles.first.open
            ? const Color(0xFF089981)
            : const Color(0xFFF23645);
        final curPricePaint = Paint()
          ..color = currentColor
          ..strokeWidth = 1.2;
        _drawDashedLine(
            canvas, Offset(0, curY), Offset(chartWidth, curY), curPricePaint);

        // Right Axis Badge
        final priceStr = _formatPrice(currentPrice);
        final badgeRect = RRect.fromRectAndRadius(
          Rect.fromLTWH(chartWidth + 1, curY - 9, priceAxisWidth - 2, 18),
          const Radius.circular(3),
        );
        canvas.drawRRect(badgeRect, Paint()..color = currentColor);
        _drawText(
          canvas,
          text: priceStr,
          offset: Offset(chartWidth + 6, curY - 6),
          style: const TextStyle(
              fontSize: 11,
              fontWeight: FontWeight.bold,
              color: Colors.black,
              fontFamily: 'monospace'),
        );
      }
    }

    // ------------------------------------------------------------------------
    // F. Crosshair & Hover Tooltip
    // ------------------------------------------------------------------------
    if (hoverOffset != null &&
        hoverOffset!.dx >= 0 &&
        hoverOffset!.dx <= chartWidth &&
        hoverOffset!.dy >= 0 &&
        hoverOffset!.dy <= chartHeight) {
      final hx = hoverOffset!.dx;
      final hy = hoverOffset!.dy;

      final crossPaint = Paint()
        ..color = Colors.white38
        ..strokeWidth = 0.8;
      _drawDashedLine(
          canvas, Offset(0, hy), Offset(chartWidth, hy), crossPaint);
      _drawDashedLine(
          canvas, Offset(hx, 0), Offset(hx, chartHeight), crossPaint);

      // Price Tag at cursor on right axis
      final hoverPrice = yToPrice(hy);
      final priceStr = _formatPrice(hoverPrice);
      final cursorBadge = RRect.fromRectAndRadius(
        Rect.fromLTWH(chartWidth + 1, hy - 9, priceAxisWidth - 2, 18),
        const Radius.circular(3),
      );
      canvas.drawRRect(cursorBadge, Paint()..color = const Color(0xFF2E82FE));
      _drawText(
        canvas,
        text: priceStr,
        offset: Offset(chartWidth + 6, hy - 6),
        style: const TextStyle(
            fontSize: 10,
            fontWeight: FontWeight.bold,
            color: Colors.white,
            fontFamily: 'monospace'),
      );

      // Find nearest candle
      final nearestIndex = math.max(
          0,
          math.min(
              candles.length - 1,
              ((chartWidth - rightPadding + scrollOffset - hx) / candleWidth)
                  .round()));
      if (nearestIndex >= 0 && nearestIndex < candles.length) {
        final nc = candles[nearestIndex];
        final isBull = nc.close >= nc.open;
        final cColor = isBull ? AppColors.bullish : AppColors.bearish;
        final timeStr =
            DateFormat('yyyy-MM-dd HH:mm').format(nc.date.toLocal());

        // Header info banner at top
        final infoText =
            'Time: $timeStr  O: ${nc.open.toStringAsFixed(2)}  H: ${nc.high.toStringAsFixed(2)}  L: ${nc.low.toStringAsFixed(2)}  C: ${nc.close.toStringAsFixed(2)}  Vol: ${_formatVol(nc.volume)}';
        _drawPillTag(
          canvas,
          text: infoText,
          offset: const Offset(80, 10),
          bgColor: const Color(0xFF1E2533).withValues(alpha: 0.9),
          textColor: cColor,
          borderColor: AppColors.border,
        );
      }
    }
  }

  void _drawDashedLine(Canvas canvas, Offset p1, Offset p2, Paint paint) {
    const dashWidth = 4.0;
    const dashSpace = 3.0;
    final dx = p2.dx - p1.dx;
    final dy = p2.dy - p1.dy;
    final count =
        (math.sqrt(dx * dx + dy * dy) / (dashWidth + dashSpace)).floor();
    for (int i = 0; i < count; i++) {
      final startRatio = i / count;
      final endRatio = (i + 0.6) / count;
      canvas.drawLine(
        Offset(p1.dx + dx * startRatio, p1.dy + dy * startRatio),
        Offset(p1.dx + dx * endRatio, p1.dy + dy * endRatio),
        paint,
      );
    }
  }

  void _drawDottedLine(Canvas canvas, Offset p1, Offset p2, Paint paint) {
    const dashWidth = 2.0;
    const dashSpace = 2.5;
    final dx = p2.dx - p1.dx;
    final dy = p2.dy - p1.dy;
    final count =
        (math.sqrt(dx * dx + dy * dy) / (dashWidth + dashSpace)).floor();
    for (int i = 0; i < count; i++) {
      final startRatio = i / count;
      final endRatio = (i + 0.5) / count;
      canvas.drawLine(
        Offset(p1.dx + dx * startRatio, p1.dy + dy * startRatio),
        Offset(p1.dx + dx * endRatio, p1.dy + dy * endRatio),
        paint,
      );
    }
  }

  void _drawPillTag(
    Canvas canvas, {
    required String text,
    required Offset offset,
    required Color bgColor,
    required Color textColor,
    Color? borderColor,
  }) {
    final textSpan = TextSpan(
      text: text,
      style: TextStyle(
          fontSize: 10,
          fontWeight: FontWeight.bold,
          color: textColor,
          fontFamily: 'monospace'),
    );
    final textPainter = TextPainter(
      text: textSpan,
      textDirection: TextDirection.ltr,
    )..layout();

    const paddingH = 6.0;
    const paddingV = 2.0;
    final rect = RRect.fromRectAndRadius(
      Rect.fromLTWH(
        offset.dx,
        offset.dy,
        textPainter.width + (paddingH * 2),
        textPainter.height + (paddingV * 2),
      ),
      const Radius.circular(4),
    );

    canvas.drawRRect(rect, Paint()..color = bgColor);
    if (borderColor != null) {
      canvas.drawRRect(
          rect,
          Paint()
            ..color = borderColor
            ..style = PaintingStyle.stroke
            ..strokeWidth = 1.0);
    }

    textPainter.paint(
        canvas, Offset(offset.dx + paddingH, offset.dy + paddingV));
  }

  void _drawText(Canvas canvas,
      {required String text,
      required Offset offset,
      required TextStyle style}) {
    final tp = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, offset);
  }

  String _formatPrice(double value) {
    final magnitude = value.abs();
    if (magnitude >= 1000) return value.toStringAsFixed(2);
    if (magnitude >= 10) return value.toStringAsFixed(3);
    if (magnitude >= 1) return value.toStringAsFixed(4);
    if (magnitude >= 0.01) return value.toStringAsFixed(5);
    return value.toStringAsFixed(8);
  }

  String _formatVol(double vol) {
    if (vol >= 1000000000) return '${(vol / 1000000000).toStringAsFixed(2)}B';
    if (vol >= 1000000) return '${(vol / 1000000).toStringAsFixed(2)}M';
    if (vol >= 1000) return '${(vol / 1000).toStringAsFixed(1)}K';
    return vol.toStringAsFixed(0);
  }

  @override
  bool shouldRepaint(covariant _SMCUnifiedPainter oldDelegate) {
    return oldDelegate.candles != candles ||
        oldDelegate.smcData != smcData ||
        oldDelegate.openPositions != openPositions ||
        oldDelegate.currentPrice != currentPrice ||
        oldDelegate.showOverlay != showOverlay ||
        oldDelegate.symbol != symbol ||
        oldDelegate.candleWidth != candleWidth ||
        oldDelegate.scrollOffset != scrollOffset ||
        oldDelegate.priceScaleMultiplier != priceScaleMultiplier ||
        oldDelegate.priceOffset != priceOffset ||
        oldDelegate.hoverOffset != hoverOffset;
  }
}
