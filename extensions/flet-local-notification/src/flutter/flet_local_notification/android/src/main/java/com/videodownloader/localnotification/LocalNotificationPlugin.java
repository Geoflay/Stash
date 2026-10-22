package com.videodownloader.localnotification;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.os.Build;

import androidx.annotation.NonNull;

import io.flutter.embedding.engine.plugins.FlutterPlugin;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;

/**
 * Shows a notification ("show": title, body, id, alert); tapping it opens the app. "progress" shows a silent one
 * with a progress bar and a Cancel button, which is passed back to the Dart side ("cancel", id); "cancel" removes
 * a notification.
 */
public class LocalNotificationPlugin implements FlutterPlugin, MethodChannel.MethodCallHandler {
    private static final String CHANNEL_ID = "downloads";  // with the phone's sound and vibration
    private static final String QUIET_CHANNEL_ID = "downloads_quiet";
    private static final String PROGRESS_CHANNEL_ID = "downloads_progress";
    private static final String EXTRA_ID = "id";

    private Context context;
    private MethodChannel channel;
    private BroadcastReceiver cancelReceiver;

    private String cancelAction() {
        return context.getPackageName() + ".CANCEL_DOWNLOAD";
    }

    @Override
    public void onAttachedToEngine(@NonNull FlutterPluginBinding binding) {
        context = binding.getApplicationContext();
        channel = new MethodChannel(binding.getBinaryMessenger(), "flet_local_notification");
        channel.setMethodCallHandler(this);
        cancelReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context receiverContext, Intent intent) {
                if (channel != null) {
                    channel.invokeMethod("cancel", intent.getIntExtra(EXTRA_ID, 0));
                }
            }
        };
        IntentFilter filter = new IntentFilter(cancelAction());
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(cancelReceiver, filter, Context.RECEIVER_NOT_EXPORTED);
        } else {
            context.registerReceiver(cancelReceiver, filter);
        }
    }

    @Override
    public void onDetachedFromEngine(@NonNull FlutterPluginBinding binding) {
        context.unregisterReceiver(cancelReceiver);
        cancelReceiver = null;
        channel.setMethodCallHandler(null);
        channel = null;
        context = null;
    }

    @Override
    public void onMethodCall(@NonNull MethodCall call, @NonNull MethodChannel.Result result) {
        NotificationManager manager = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        Integer id = call.argument("id");
        int notificationId = id == null ? 0 : id;
        switch (call.method) {
            case "show":
                if (manager == null || !manager.areNotificationsEnabled()) {
                    result.success(false);
                    return;
                }
                Boolean alert = call.argument("alert");
                manager.notify(notificationId, finished(manager, call.argument("title"), call.argument("body"),
                        alert == null || alert));
                result.success(true);
                return;
            case "progress":
                if (manager == null || !manager.areNotificationsEnabled()) {
                    result.success(false);
                    return;
                }
                Integer percent = call.argument("percent");
                manager.notify(notificationId, progress(manager, notificationId, call.argument("title"),
                        call.argument("body"), percent == null ? -1 : percent, call.argument("cancelLabel")));
                result.success(true);
                return;
            case "cancel":
                if (manager != null) {
                    manager.cancel(notificationId);
                }
                result.success(null);
                return;
            default:
                result.notImplemented();
        }
    }

    private Notification.Builder builder(NotificationManager manager, String channelId, String name, int importance) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            manager.createNotificationChannel(new NotificationChannel(channelId, name, importance));
            return new Notification.Builder(context, channelId);
        }
        return new Notification.Builder(context);
    }

    private PendingIntent openApp() {
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(context.getPackageName());
        return launch == null ? null : PendingIntent.getActivity(context, 0, launch,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
    }

    private Notification finished(NotificationManager manager, String title, String body, boolean loud) {
        // A channel's sound cannot change once created: one channel with sound, one without.
        Notification.Builder builder = loud
                ? builder(manager, CHANNEL_ID, "Downloads", NotificationManager.IMPORTANCE_DEFAULT)
                : builder(manager, QUIET_CHANNEL_ID, "Downloads (silent)", NotificationManager.IMPORTANCE_LOW);
        if (loud && Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            builder.setDefaults(Notification.DEFAULT_ALL);
        }
        builder.setSmallIcon(android.R.drawable.stat_sys_download_done)
                .setContentTitle(title)
                .setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setAutoCancel(true);
        PendingIntent open = openApp();
        if (open != null) {
            builder.setContentIntent(open);
        }
        return builder.build();
    }

    @SuppressWarnings("deprecation")
    private Notification progress(NotificationManager manager, int id, String title, String body, int percent,
                                  String cancelLabel) {
        Notification.Builder builder = builder(manager, PROGRESS_CHANNEL_ID, "Downloads in progress",
                NotificationManager.IMPORTANCE_LOW);
        Intent cancel = new Intent(cancelAction()).setPackage(context.getPackageName()).putExtra(EXTRA_ID, id);
        PendingIntent cancelIntent = PendingIntent.getBroadcast(context, id, cancel,
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        builder.setSmallIcon(android.R.drawable.stat_sys_download)
                .setContentTitle(title)
                .setContentText(body)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setProgress(100, Math.max(percent, 0), percent < 0)
                .addAction(android.R.drawable.ic_menu_close_clear_cancel,
                        cancelLabel == null ? "Cancel" : cancelLabel, cancelIntent);
        PendingIntent open = openApp();
        if (open != null) {
            builder.setContentIntent(open);
        }
        return builder.build();
    }
}
