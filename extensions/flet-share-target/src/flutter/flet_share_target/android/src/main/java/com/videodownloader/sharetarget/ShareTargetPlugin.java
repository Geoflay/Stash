package com.videodownloader.sharetarget;

import androidx.annotation.NonNull;

import io.flutter.embedding.engine.plugins.FlutterPlugin;
import io.flutter.plugin.common.MethodCall;
import io.flutter.plugin.common.MethodChannel;

/**
 * Keeps the text shared to the app until the Dart side takes it ("takeSharedText"), and tells the Dart side
 * ("shared") when text arrives while the app is open.
 */
public class ShareTargetPlugin implements FlutterPlugin, MethodChannel.MethodCallHandler {
    private static String pending;
    private static MethodChannel channel;

    /** Called on the main thread by ShareReceiverActivity. */
    static synchronized void post(String text) {
        pending = text;
        if (channel != null) {
            channel.invokeMethod("shared", null);
        }
    }

    private static synchronized String take() {
        String text = pending;
        pending = null;
        return text;
    }

    @Override
    public void onAttachedToEngine(@NonNull FlutterPluginBinding binding) {
        MethodChannel created = new MethodChannel(binding.getBinaryMessenger(), "flet_share_target");
        created.setMethodCallHandler(this);
        synchronized (ShareTargetPlugin.class) {
            channel = created;
        }
    }

    @Override
    public void onDetachedFromEngine(@NonNull FlutterPluginBinding binding) {
        synchronized (ShareTargetPlugin.class) {
            if (channel != null) {
                channel.setMethodCallHandler(null);
                channel = null;
            }
        }
    }

    @Override
    public void onMethodCall(@NonNull MethodCall call, @NonNull MethodChannel.Result result) {
        if ("takeSharedText".equals(call.method)) {
            result.success(take());
        } else {
            result.notImplemented();
        }
    }
}
