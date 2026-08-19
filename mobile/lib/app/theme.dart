import 'package:flutter/material.dart';

class AppTheme {
  static ThemeData dark() {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      scaffoldBackgroundColor: const Color(0xFF0B0E14),
      colorScheme: const ColorScheme.dark(
        primary: Color(0xFF0ECB81),         // Binance Bullish Green
        secondary: Color(0xFFF6465D),       // Binance Bearish Red
        surface: Color(0xFF151A24),         // TradingView dark surface
        surfaceContainerHigh: Color(0xFF1A202C),
        onPrimary: Colors.black,
        onSurface: Colors.white,
      ),
      appBarTheme: const AppBarTheme(
        backgroundColor: Color(0xFF151A24),
        foregroundColor: Colors.white,
        elevation: 0,
        scrolledUnderElevation: 0,
      ),
      cardTheme: const CardThemeData(
        color: Color(0xFF151A24),
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.all(Radius.circular(8)),
          side: BorderSide(color: Color(0xFF232A38), width: 1),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: const Color(0xFF1A202C),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: const BorderSide(color: Color(0xFF232A38)),
        ),
        enabledBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: const BorderSide(color: Color(0xFF232A38)),
        ),
        focusedBorder: OutlineInputBorder(
          borderRadius: BorderRadius.circular(6),
          borderSide: const BorderSide(color: Color(0xFF0ECB81)),
        ),
      ),
      dividerTheme: const DividerThemeData(
        color: Color(0xFF232A38),
        thickness: 1,
        space: 1,
      ),
    );
  }
}

class AppColors {
  static const background = Color(0xFF0B0E14);
  static const surface = Color(0xFF151A24);
  static const panel = Color(0xFF181E2A);
  static const border = Color(0xFF232A38);
  static const bullish = Color(0xFF0ECB81);
  static const bearish = Color(0xFFF6465D);
  static const neutral = Color(0xFFF59E0B);
  static const orderBlock = Color(0xFF2E82FE);
  static const fvg = Color(0xFFA855F7);
  static const eqLine = Color(0xFFF59E0B);
  static const textMuted = Color(0xFF848E9C);
}
