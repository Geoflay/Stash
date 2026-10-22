package com.videodownloader.sharetarget;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.Context;
import android.widget.RemoteViews;

/** The "Paste & download" widget of the home screen: a button opening the app, which downloads the link copied. */
public class PasteWidgetProvider extends AppWidgetProvider {
    @Override
    public void onUpdate(Context context, AppWidgetManager manager, int[] widgetIds) {
        PendingIntent open = PendingIntent.getActivity(context, 0, ShareReceiverActivity.pasteIntent(context),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        for (int id : widgetIds) {
            RemoteViews views = new RemoteViews(context.getPackageName(), R.layout.videodownloader_paste_widget);
            views.setOnClickPendingIntent(R.id.videodownloader_paste_button, open);
            manager.updateAppWidget(id, views);
        }
    }
}
