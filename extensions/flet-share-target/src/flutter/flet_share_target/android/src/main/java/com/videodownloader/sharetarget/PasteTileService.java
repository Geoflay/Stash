package com.videodownloader.sharetarget;

import android.annotation.TargetApi;
import android.app.PendingIntent;
import android.content.Intent;
import android.os.Build;
import android.service.quicksettings.TileService;

/** The "Paste & download" tile of the quick settings: opens the app, which downloads the link copied. */
@TargetApi(Build.VERSION_CODES.N)
public class PasteTileService extends TileService {
    @Override
    @SuppressWarnings("deprecation")
    public void onClick() {
        super.onClick();
        Intent intent = ShareReceiverActivity.pasteIntent(this);
        if (Build.VERSION.SDK_INT >= 34) {
            startActivityAndCollapse(PendingIntent.getActivity(
                    this, 0, intent, PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
        } else {
            startActivityAndCollapse(intent);
        }
    }
}
