package com.aitrade.ai_trade_advisor

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.RingtoneManager
import android.media.ToneGenerator
import android.os.Build
import android.os.Bundle
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel

class MainActivity : FlutterActivity() {
    companion object {
        private const val CHANNEL_NAME = "com.aitrade.ai_trade_advisor/local_notifications"
        private const val NOTIFICATION_CHANNEL_ID = "apex_trade_high_v2"
        private const val NOTIFICATION_CHANNEL_NAME = "AI Trade Alerts & Execution"
        private const val PERMISSION_REQUEST_CODE = 1001
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        createNotificationChannel()
        ensureNotificationPermission()
    }

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        createNotificationChannel()

        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, CHANNEL_NAME)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "showNotification" -> {
                        val id = call.argument<Int>("id") ?: (System.currentTimeMillis() % 100000).toInt()
                        val title = call.argument<String>("title") ?: "AI Trade Advisor"
                        val body = call.argument<String>("body") ?: ""
                        val playSound = call.argument<Boolean>("playSound") ?: true
                        val shown = showSystemNotification(id, title, body, playSound)
                        result.success(shown)
                    }
                    "requestPermission" -> {
                        val granted = ensureNotificationPermission()
                        result.success(granted)
                    }
                    "playAlertSound" -> {
                        playAudibleChime()
                        result.success(true)
                    }
                    else -> result.notImplemented()
                }
            }
    }

    private fun ensureNotificationPermission(): Boolean {
        if (Build.VERSION.SDK_INT >= 33) {
            val granted = checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
                PackageManager.PERMISSION_GRANTED
            if (!granted) {
                requestPermissions(
                    arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                    PERMISSION_REQUEST_CODE
                )
                return false
            }
        }
        return true
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            val soundUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
            val audioAttrs = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_NOTIFICATION_EVENT)
                .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION)
                .build()

            val channel = NotificationChannel(
                NOTIFICATION_CHANNEL_ID,
                NOTIFICATION_CHANNEL_NAME,
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Real-time trading signals, order fills, and TP/SL alerts"
                enableLights(true)
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 220, 120, 220)
                if (soundUri != null) {
                    setSound(soundUri, audioAttrs)
                }
            }
            manager.createNotificationChannel(channel)
        }
    }

    private fun playAudibleChime() {
        try {
            val soundUri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_NOTIFICATION)
            val ringtone = if (soundUri != null) {
                RingtoneManager.getRingtone(applicationContext, soundUri)
            } else {
                null
            }
            if (ringtone != null) {
                ringtone.play()
            }
        } catch (_: Exception) {
        }

        try {
            val toneGen = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 90)
            toneGen.startTone(ToneGenerator.TONE_PROP_BEEP2, 220)
            window?.decorView?.postDelayed({
                try {
                    toneGen.release()
                } catch (_: Exception) {
                }
            }, 400)
        } catch (_: Exception) {
        }
    }

    @Suppress("DEPRECATION")
    private fun showSystemNotification(
        id: Int,
        title: String,
        body: String,
        playSound: Boolean
    ): Boolean {
        return try {
            createNotificationChannel()
            if (playSound) {
                playAudibleChime()
            }

            if (Build.VERSION.SDK_INT >= 33) {
                val granted = checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) ==
                    PackageManager.PERMISSION_GRANTED
                if (!granted) {
                    ensureNotificationPermission()
                    return false
                }
            }

            val intent = Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            }
            val pendingFlags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            } else {
                PendingIntent.FLAG_UPDATE_CURRENT
            }
            val pendingIntent = PendingIntent.getActivity(this, id, intent, pendingFlags)

            val builder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Notification.Builder(this, NOTIFICATION_CHANNEL_ID)
            } else {
                Notification.Builder(this)
                    .setPriority(Notification.PRIORITY_HIGH)
                    .setDefaults(Notification.DEFAULT_ALL)
            }

            val iconRes = if (applicationInfo.icon != 0) {
                applicationInfo.icon
            } else {
                android.R.drawable.ic_dialog_info
            }

            val notification = builder
                .setSmallIcon(iconRes)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(Notification.BigTextStyle().bigText(body))
                .setAutoCancel(true)
                .setContentIntent(pendingIntent)
                .setCategory(Notification.CATEGORY_MESSAGE)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .build()

            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            manager.notify(id, notification)
            true
        } catch (e: Exception) {
            false
        }
    }
}
