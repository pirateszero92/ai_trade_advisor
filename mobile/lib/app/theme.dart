import 'package:flutter/material.dart';

class AppTheme {
  static ThemeData dark() {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      colorScheme: ColorScheme.dark(
        primary: const Color(0xFF00C087),      // Green for bullish
        secondary: const Color(0xFFFF6B6B),    // Red for bearish
        surface: const Color(0xFF1A1A2E),
        onPrimary: Colors.black,
        onSurface: Colors.white,
      ),
      scaffoldBackgroundColor: const Color(0xFF0F0F1A),
      appBarTheme: const AppBarTheme(
        backgroundColor: Color(0xFF1A1A2E),
        foregroundColor: Colors.white,
        elevation: 0,
      ),
      cardTheme: const CardThemeData(
        color: Color(0xFF1A1A2E),
        elevation: 2,
        shape: RoundedRectangleBorder(borderRadius: BorderRadius.all(Radius.circular(12))),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: const Color(0xFF252540),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(8),
          borderSide: BorderSide.none,
        ),
      ),
      fontFamily: 'Roboto',
    );
  }
}

// App-specific colors
class AppColors {
  static const bullish = Color(0xFF00C087);
  static const bearish = Color(0xFFFF6B6B);
  static const neutral = Color(0xFFFFD700);
  static const surface = Color(0xFF1A1A2E);
  static const background = Color(0xFF0F0F1A);
  static const orderBlock = Color(0xFF3A7BD5);
  static const fvg = Color(0xFF9B59B6);
  static const eqLine = Color(0xFFFF9900);
}
