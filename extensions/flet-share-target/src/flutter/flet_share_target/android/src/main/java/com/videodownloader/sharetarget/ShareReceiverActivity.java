package com.videodownloader.sharetarget;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;

/**
 * Receives "Share" from another app: keeps the text, then opens (or brings back) the app's own window. Also
 * opened by the "Paste & download" tile and widget: the app is then asked to download the link copied.
 */
public class ShareReceiverActivity extends Activity {
    static final String ACTION_PASTE = "com.videodownloader.sharetarget.PASTE";
    /** What the Python side receives for "Paste & download" (see flet_share_target.PASTE_REQUEST). */
    static final String PASTE_REQUEST = "videodownloader:paste";

    static Intent pasteIntent(Context context) {
        Intent intent = new Intent(context, ShareReceiverActivity.class);
        intent.setAction(ACTION_PASTE);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        return intent;
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        Intent intent = getIntent();
        if (intent != null && Intent.ACTION_SEND.equals(intent.getAction())) {
            CharSequence text = intent.getCharSequenceExtra(Intent.EXTRA_TEXT);
            if (text != null) {
                ShareTargetPlugin.post(text.toString());
            }
        } else if (intent != null && ACTION_PASTE.equals(intent.getAction())) {
            ShareTargetPlugin.post(PASTE_REQUEST);
        }
        // Like tapping the app's icon: the running app comes to the front, or the app starts.
        Intent launch = getPackageManager().getLaunchIntentForPackage(getPackageName());
        if (launch != null) {
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
            startActivity(launch);
        }
        finish();
    }
}
