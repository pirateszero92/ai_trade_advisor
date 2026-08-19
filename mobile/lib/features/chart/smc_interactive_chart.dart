import 'dart:math' as math;
import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:candlesticks/candlesticks.dart';
import 'package:intl/intl.dart' hide TextDirection;
import '../../app/theme.dart';

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
  Offset? _hoverOffset;
  double _lastScale = 1.0;

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
      _hoverOffset = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    if (widget.candles.isEmpty) {
      return const Center(child: Text('No candles data.', style: TextStyle(color: Colors.white54)));
    }

    return LayoutBuilder(
      builder: (context, constraints) {
        final chartWidth = constraints.maxWidth - 70; // 70px right price axis

        final maxScroll = math.max(0.0, (widget.candles.length * _candleWidth) - chartWidth * 0.5);
        _scrollOffset = _scrollOffset.clamp(0.0, maxScroll);

        return Stack(
          children: [
            // Interactive Chart Canvas
            Positioned.fill(
              child: Listener(
                onPointerSignal: (pointerSignal) {
                  if (pointerSignal is PointerScrollEvent) {
                    setState(() {
                      if (pointerSignal.scrollDelta.dy < 0) {
                        _candleWidth = (_candleWidth + 1.2).clamp(4.0, 40.0);
                      } else {
                        _candleWidth = (_candleWidth - 1.2).clamp(4.0, 40.0);
                      }
                    });
                  }
                },
                child: GestureDetector(
                  onScaleStart: (_) {
                    _lastScale = 1.0;
                  },
                  onScaleUpdate: (details) {
                    setState(() {
                      // Pan
                      if (details.scale == 1.0) {
                        _scrollOffset = (_scrollOffset - details.focalPointDelta.dx).clamp(0.0, maxScroll);
                        _hoverOffset = details.localFocalPoint;
                      } else {
                        // Pinch Zoom
                        final scaleDelta = details.scale / _lastScale;
                        _lastScale = details.scale;
                        _candleWidth = (_candleWidth * scaleDelta).clamp(4.0, 40.0);
                      }
                    });
                  },
                  onScaleEnd: (_) {
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
                        smcData: widget.smcData,
                        openPositions: widget.openPositions,
                        currentPrice: widget.currentPrice,
                        showOverlay: widget.showOverlay,
                        symbol: widget.symbol,
                        candleWidth: _candleWidth,
                        scrollOffset: _scrollOffset,
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
                  _controlBtn(icon: Icons.add, tooltip: 'Zoom In', onTap: _zoomIn),
                  const SizedBox(width: 6),
                  _controlBtn(icon: Icons.remove, tooltip: 'Zoom Out', onTap: _zoomOut),
                  const SizedBox(width: 6),
                  _controlBtn(icon: Icons.restart_alt, tooltip: 'Reset Zoom & Pan', onTap: _resetView),
                ],
              ),
            ),
          ],
        );
      },
    );
  }

  Widget _controlBtn({required IconData icon, required String tooltip, required VoidCallback onTap}) {
    return Container(
      decoration: BoxDecoration(
        color: const Color(0xFF1E2533).withOpacity(0.9),
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
  final Map<String, dynamic>? smcData;
  final List<Map<String, dynamic>> openPositions;
  final double currentPrice;
  final bool showOverlay;
  final String symbol;
  final double candleWidth;
  final double scrollOffset;
  final Offset? hoverOffset;

  _SMCUnifiedPainter({
    required this.candles,
    required this.smcData,
    required this.openPositions,
    required this.currentPrice,
    required this.showOverlay,
    required this.symbol,
    required this.candleWidth,
    required this.scrollOffset,
    required this.hoverOffset,
  });

  @override
  void paint(Canvas canvas, Size size) {
    final priceAxisWidth = 70.0;
    final timeAxisHeight = 26.0;

    final chartWidth = size.width - priceAxisWidth;
    final chartHeight = size.height - timeAxisHeight;

    if (chartWidth <= 0 || chartHeight <= 0 || candles.isEmpty) return;

    // 1. Calculate visible candle range based on scroll offset and candle width
    // Candles are ordered [0: newest, ..., N: oldest]
    // Index 0 is displayed on the rightmost edge (chartWidth - scrollOffset)
    final rightPadding = 20.0;
    final startIndex = math.max(0, ((scrollOffset - rightPadding) / candleWidth).floor());
    final visibleCount = ((chartWidth + rightPadding) / candleWidth).ceil() + 3;
    final endIndex = math.min(candles.length - 1, startIndex + visibleCount);

    if (startIndex > endIndex || startIndex >= candles.length) return;

    final visibleCandles = candles.sublist(startIndex, endIndex + 1);

    // 2. Find min and max price within the VISIBLE candles
    double minPrice = visibleCandles.map((c) => c.low).reduce(math.min);
    double maxPrice = visibleCandles.map((c) => c.high).reduce(math.max);

    // Expand price range to include current price and SMC key levels if visible
    if (currentPrice > 0) {
      minPrice = math.min(minPrice, currentPrice);
      maxPrice = math.max(maxPrice, currentPrice);
    }

    if (showOverlay && smcData != null) {
      final ob = smcData!['order_block'] as Map<String, dynamic>?;
      if (ob != null) {
        final obTop = (ob['top'] as num?)?.toDouble();
        final obBottom = (ob['bottom'] as num?)?.toDouble();
        if (obTop != null) maxPrice = math.max(maxPrice, obTop);
        if (obBottom != null) minPrice = math.min(minPrice, obBottom);
      }
      final fvg = smcData!['fvg'] as Map<String, dynamic>?;
      if (fvg != null) {
        final fvgTop = (fvg['top'] as num?)?.toDouble();
        final fvgBottom = (fvg['bottom'] as num?)?.toDouble();
        if (fvgTop != null) maxPrice = math.max(maxPrice, fvgTop);
        if (fvgBottom != null) minPrice = math.min(minPrice, fvgBottom);
      }
      final eq = (smcData!['equilibrium'] as num?)?.toDouble();
      if (eq != null) {
        maxPrice = math.max(maxPrice, eq);
        minPrice = math.min(minPrice, eq);
      }
    }

    // Add 6% top & bottom padding so candles don't touch the top/bottom edges
    final priceSpan = maxPrice - minPrice;
    final paddedMinPrice = minPrice - (priceSpan > 0 ? priceSpan * 0.06 : 1.0);
    final paddedMaxPrice = maxPrice + (priceSpan > 0 ? priceSpan * 0.06 : 1.0);
    final effectiveSpan = paddedMaxPrice - paddedMinPrice;

    // Coordinate conversion function
    double priceToY(double price) {
      if (effectiveSpan <= 0) return chartHeight * 0.5;
      final ratio = (paddedMaxPrice - price) / effectiveSpan;
      return ratio * chartHeight;
    }

    double yToPrice(double y) {
      final ratio = y / chartHeight;
      return paddedMaxPrice - (ratio * effectiveSpan);
    }

    double candleIndexToX(int index) {
      return chartWidth - rightPadding - (index * candleWidth) + scrollOffset;
    }

    // ------------------------------------------------------------------------
    // A. Draw Grid Lines & Price Axis Labels
    // ------------------------------------------------------------------------
    final gridPaint = Paint()
      ..color = const Color(0xFF1E2533).withOpacity(0.6)
      ..strokeWidth = 1.0;

    final numPriceTicks = 7;
    for (int i = 0; i <= numPriceTicks; i++) {
      final y = (chartHeight / numPriceTicks) * i;
      canvas.drawLine(Offset(0, y), Offset(chartWidth, y), gridPaint);

      // Price label on right axis
      final priceAtTick = yToPrice(y);
      final priceStr = priceAtTick < 10 ? priceAtTick.toStringAsFixed(4) : priceAtTick.toStringAsFixed(2);
      _drawText(
        canvas,
        text: priceStr,
        offset: Offset(chartWidth + 6, y - 6),
        style: const TextStyle(fontSize: 10, color: Colors.white54, fontFamily: 'monospace'),
      );
    }

    // Right axis vertical divider
    canvas.drawLine(Offset(chartWidth, 0), Offset(chartWidth, chartHeight), Paint()..color = AppColors.border);
    // Bottom time axis horizontal divider
    canvas.drawLine(Offset(0, chartHeight), Offset(size.width, chartHeight), Paint()..color = AppColors.border);

    // ------------------------------------------------------------------------
    // B. Draw SMC Overlays (Order Blocks, FVGs, EQ 50%) — SYNCHRONIZED TO PRICE
    // ------------------------------------------------------------------------
    if (showOverlay && smcData != null) {
      // 1. Order Block (OB) Box
      final ob = smcData!['order_block'] as Map<String, dynamic>?;
      if (ob != null) {
        final obTop = (ob['top'] as num?)?.toDouble();
        final obBottom = (ob['bottom'] as num?)?.toDouble();
        final isBullish = (ob['direction'] as String? ?? 'bullish') == 'bullish';

        if (obTop != null && obBottom != null) {
          final yTop = priceToY(obTop);
          final yBottom = priceToY(obBottom);
          final boxY = math.min(yTop, yBottom);
          final boxH = (yTop - yBottom).abs().clamp(6.0, chartHeight);

          final obColor = isBullish ? const Color(0xFF00C087) : const Color(0xFFFF6B6B);
          final rect = Rect.fromLTWH(0, boxY, chartWidth, boxH);

          canvas.drawRect(rect, Paint()..color = obColor.withOpacity(0.18));
          final obBorder = Paint()
            ..color = obColor.withOpacity(0.8)
            ..strokeWidth = 1.2
            ..style = PaintingStyle.stroke;
          canvas.drawLine(Offset(0, boxY), Offset(chartWidth, boxY), obBorder);
          canvas.drawLine(Offset(0, boxY + boxH), Offset(chartWidth, boxY + boxH), obBorder);

          _drawPillTag(
            canvas,
            text: isBullish
                ? '🟢 BULLISH OB [${obBottom.toStringAsFixed(1)} - ${obTop.toStringAsFixed(1)}]'
                : '🔴 BEARISH OB [${obBottom.toStringAsFixed(1)} - ${obTop.toStringAsFixed(1)}]',
            offset: Offset(8, boxY + 2),
            bgColor: obColor.withOpacity(0.85),
            textColor: Colors.black,
          );
        }
      }

      // 2. Fair Value Gap (FVG) Box
      final fvg = smcData!['fvg'] as Map<String, dynamic>?;
      if (fvg != null) {
        final fvgTop = (fvg['top'] as num?)?.toDouble();
        final fvgBottom = (fvg['bottom'] as num?)?.toDouble();

        if (fvgTop != null && fvgBottom != null) {
          final yTop = priceToY(fvgTop);
          final yBottom = priceToY(fvgBottom);
          final boxY = math.min(yTop, yBottom);
          final boxH = (yTop - yBottom).abs().clamp(6.0, chartHeight);

          const fvgColor = Color(0xFF9B59B6);
          final rect = Rect.fromLTWH(0, boxY, chartWidth, boxH);

          canvas.drawRect(rect, Paint()..color = fvgColor.withOpacity(0.16));
          final fvgBorder = Paint()
            ..color = fvgColor.withOpacity(0.75)
            ..strokeWidth = 1.2
            ..style = PaintingStyle.stroke;
          canvas.drawLine(Offset(0, boxY), Offset(chartWidth, boxY), fvgBorder);
          canvas.drawLine(Offset(0, boxY + boxH), Offset(chartWidth, boxY + boxH), fvgBorder);

          _drawPillTag(
            canvas,
            text: '⚡ FVG IMBALANCE [${fvgBottom.toStringAsFixed(1)} - ${fvgTop.toStringAsFixed(1)}]',
            offset: Offset(8, boxY + 2),
            bgColor: fvgColor.withOpacity(0.85),
            textColor: Colors.white,
          );
        }
      }

      // 3. Equilibrium Line (EQ 50%)
      final eq = (smcData!['equilibrium'] as num?)?.toDouble();
      if (eq != null) {
        final y = priceToY(eq);
        if (y >= 0 && y <= chartHeight) {
          final eqPaint = Paint()
            ..color = const Color(0xFFFFD700)
            ..strokeWidth = 1.0
            ..style = PaintingStyle.stroke;
          _drawDashedLine(canvas, Offset(0, y), Offset(chartWidth, y), eqPaint);

          _drawPillTag(
            canvas,
            text: '⚖️ EQ 50% (${eq.toStringAsFixed(1)})',
            offset: Offset(chartWidth - 140, y - 16),
            bgColor: const Color(0xFF332B00),
            textColor: const Color(0xFFFFD700),
            borderColor: const Color(0xFFFFD700),
          );
        }
      }
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
        final isLong = (pos['direction']?.toString() ?? 'long').toLowerCase() == 'long';

        if (entry != null) {
          final y = priceToY(entry);
          final p = Paint()
            ..color = const Color(0xFF00E5FF)
            ..strokeWidth = 1.2;
          _drawDashedLine(canvas, Offset(0, y), Offset(chartWidth, y), p);
          _drawPillTag(
            canvas,
            text: '🎯 ${isLong ? 'LONG' : 'SHORT'} ENTRY @ \$${entry.toStringAsFixed(2)}',
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
            text: '🛑 SL @ \$${sl.toStringAsFixed(2)}',
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
            text: '🎯 TP @ \$${tp.toStringAsFixed(2)}',
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
      final candleColor = isBull ? const Color(0xFF00C087) : const Color(0xFFFF6B6B);

      // Volume Bar (at bottom of chart)
      final volH = maxVol > 0 ? (c.volume / maxVol) * volAreaHeight : 0.0;
      final volRect = Rect.fromLTWH(x - (bodyWidth * 0.5), chartHeight - volH, bodyWidth, volH);
      canvas.drawRect(volRect, Paint()..color = candleColor.withOpacity(0.35));

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

      final bodyRect = Rect.fromLTWH(x - (bodyWidth * 0.5), bodyTop, bodyWidth, bodyHeight);
      canvas.drawRect(bodyRect, Paint()..color = candleColor);

      // Time labels at intervals on bottom axis
      if (i % math.max(1, (60 / candleWidth).round()) == 0) {
        final timeStr = DateFormat('MM/dd HH:mm').format(c.date.toLocal());
        _drawText(
          canvas,
          text: timeStr,
          offset: Offset(x - 30, chartHeight + 6),
          style: const TextStyle(fontSize: 9, color: Colors.white38, fontFamily: 'monospace'),
        );
        // Subtle vertical grid line
        canvas.drawLine(Offset(x, 0), Offset(x, chartHeight), Paint()..color = const Color(0xFF1E2533).withOpacity(0.4));
      }
    }

    // ------------------------------------------------------------------------
    // E. Draw Current Live Price Line & Right Axis Badge
    // ------------------------------------------------------------------------
    if (currentPrice > 0) {
      final curY = priceToY(currentPrice);
      if (curY >= 0 && curY <= chartHeight) {
        final curPricePaint = Paint()
          ..color = AppColors.bullish
          ..strokeWidth = 1.2;
        _drawDashedLine(canvas, Offset(0, curY), Offset(chartWidth, curY), curPricePaint);

        // Right Axis Badge
        final priceStr = currentPrice < 10 ? currentPrice.toStringAsFixed(4) : currentPrice.toStringAsFixed(2);
        final badgeRect = RRect.fromRectAndRadius(
          Rect.fromLTWH(chartWidth + 1, curY - 9, priceAxisWidth - 2, 18),
          const Radius.circular(3),
        );
        canvas.drawRRect(badgeRect, Paint()..color = AppColors.bullish);
        _drawText(
          canvas,
          text: priceStr,
          offset: Offset(chartWidth + 6, curY - 6),
          style: const TextStyle(fontSize: 11, fontWeight: FontWeight.bold, color: Colors.black, fontFamily: 'monospace'),
        );
      }
    }

    // ------------------------------------------------------------------------
    // F. Crosshair & Hover Tooltip
    // ------------------------------------------------------------------------
    if (hoverOffset != null && hoverOffset!.dx >= 0 && hoverOffset!.dx <= chartWidth && hoverOffset!.dy >= 0 && hoverOffset!.dy <= chartHeight) {
      final hx = hoverOffset!.dx;
      final hy = hoverOffset!.dy;

      final crossPaint = Paint()
        ..color = Colors.white38
        ..strokeWidth = 0.8;
      _drawDashedLine(canvas, Offset(0, hy), Offset(chartWidth, hy), crossPaint);
      _drawDashedLine(canvas, Offset(hx, 0), Offset(hx, chartHeight), crossPaint);

      // Price Tag at cursor on right axis
      final hoverPrice = yToPrice(hy);
      final priceStr = hoverPrice < 10 ? hoverPrice.toStringAsFixed(4) : hoverPrice.toStringAsFixed(2);
      final cursorBadge = RRect.fromRectAndRadius(
        Rect.fromLTWH(chartWidth + 1, hy - 9, priceAxisWidth - 2, 18),
        const Radius.circular(3),
      );
      canvas.drawRRect(cursorBadge, Paint()..color = const Color(0xFF2E82FE));
      _drawText(
        canvas,
        text: priceStr,
        offset: Offset(chartWidth + 6, hy - 6),
        style: const TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: Colors.white, fontFamily: 'monospace'),
      );

      // Find nearest candle
      final nearestIndex = math.max(0, math.min(candles.length - 1, ((chartWidth - rightPadding + scrollOffset - hx) / candleWidth).round()));
      if (nearestIndex >= 0 && nearestIndex < candles.length) {
        final nc = candles[nearestIndex];
        final isBull = nc.close >= nc.open;
        final cColor = isBull ? AppColors.bullish : AppColors.bearish;
        final timeStr = DateFormat('yyyy-MM-dd HH:mm').format(nc.date.toLocal());

        // Header info banner at top
        final infoText = 'Time: $timeStr  O: ${nc.open.toStringAsFixed(2)}  H: ${nc.high.toStringAsFixed(2)}  L: ${nc.low.toStringAsFixed(2)}  C: ${nc.close.toStringAsFixed(2)}  Vol: ${_formatVol(nc.volume)}';
        _drawPillTag(
          canvas,
          text: infoText,
          offset: const Offset(80, 10),
          bgColor: const Color(0xFF1E2533).withOpacity(0.9),
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
    final count = (math.sqrt(dx * dx + dy * dy) / (dashWidth + dashSpace)).floor();
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
      style: TextStyle(fontSize: 10, fontWeight: FontWeight.bold, color: textColor, fontFamily: 'monospace'),
    );
    final textPainter = TextPainter(
      text: textSpan,
      textDirection: TextDirection.ltr,
    )..layout();

    final paddingH = 6.0;
    final paddingV = 2.0;
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
      canvas.drawRRect(rect, Paint()..color = borderColor..style = PaintingStyle.stroke..strokeWidth = 1.0);
    }

    textPainter.paint(canvas, Offset(offset.dx + paddingH, offset.dy + paddingV));
  }

  void _drawText(Canvas canvas, {required String text, required Offset offset, required TextStyle style}) {
    final tp = TextPainter(
      text: TextSpan(text: text, style: style),
      textDirection: TextDirection.ltr,
    )..layout();
    tp.paint(canvas, offset);
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
        oldDelegate.candleWidth != candleWidth ||
        oldDelegate.scrollOffset != scrollOffset ||
        oldDelegate.hoverOffset != hoverOffset;
  }
}
