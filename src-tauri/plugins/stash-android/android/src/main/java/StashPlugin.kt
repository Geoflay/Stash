package dev.stash.light.plugin

import android.Manifest
import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ContentUris
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.graphics.drawable.Icon
import android.os.Build
import android.net.Uri
import android.os.Environment
import android.provider.MediaStore
import android.provider.OpenableColumns
import android.view.View
import android.view.WindowInsetsController
import android.webkit.WebView
import app.tauri.annotation.Command
import app.tauri.annotation.InvokeArg
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Channel
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import com.yausername.youtubedl_android.YoutubeDL
import com.yausername.youtubedl_android.YoutubeDLException
import com.yausername.youtubedl_android.YoutubeDLRequest
import java.io.File
import kotlin.concurrent.thread
import kotlin.math.roundToInt

@InvokeArg
class RunArgs {
    lateinit var args: Array<String>
    var id: String? = null
    lateinit var lines: Channel
}

@InvokeArg
class IdArgs {
    lateinit var id: String
}

@InvokeArg
class SaveArgs {
    lateinit var path: String
    lateinit var name: String
    lateinit var mime: String
    /** A folder inside Download/Stash, or null. */
    var folder: String? = null
}

@InvokeArg
class UriArgs {
    lateinit var uri: String
}

@InvokeArg
class RenameArgs {
    lateinit var uri: String
    /** The new name, without the extension. */
    lateinit var name: String
}

@InvokeArg
class ImportArgs {
    lateinit var uri: String
    lateinit var dir: String
}

@InvokeArg
class NotifyArgs {
    var id: Int = 0
    lateinit var title: String
    /** The job event's step: queued, download, mux or encode. */
    lateinit var step: String
    var fraction: Double? = null
}

@InvokeArg
class NotificationArgs {
    var id: Int = 0
}

@InvokeArg
class SystemBarsArgs {
    var dark: Boolean = false
}

@InvokeArg
class CancelArgs {
    lateinit var cancels: Channel
}

@InvokeArg
class SharesArgs {
    lateinit var shares: Channel
}

private const val JOBS_CHANNEL = "jobs"
private const val CANCEL_ACTION = "dev.stash.light.CANCEL"

@TauriPlugin
class StashPlugin(private val activity: Activity) : Plugin(activity) {
    /** Unpacks Python and yt-dlp on first use; `lazy` is synchronized. */
    private val ytdl by lazy { YoutubeDL.getInstance().apply { init(activity.applicationContext) } }

    /** Runs blocking work off the main thread and rejects the call if it throws. */
    private fun background(invoke: Invoke, block: () -> Unit) {
        thread {
            try {
                block()
            } catch (e: Exception) {
                invoke.reject(e.message ?: e.toString())
            }
        }
    }

    private fun value(text: String) = JSObject().apply { put("value", text) }

    /** The text last shared to the app from another one, until the webview takes it. */
    @Volatile private var shared: String? = null
    @Volatile private var shares: Channel? = null

    override fun load(webView: WebView) {
        received(activity.intent)
    }

    override fun onNewIntent(intent: Intent) {
        received(intent)
    }

    /** Share → Stash in another app; not the same share again when the app is reopened from Recents. */
    private fun received(intent: Intent?) {
        if (intent?.action != Intent.ACTION_SEND || intent.flags and Intent.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY != 0) return
        shared = intent.getStringExtra(Intent.EXTRA_TEXT) ?: return
        shares?.send(JSObject())
    }

    /** Called once at startup: `shares` is told when a share arrives. */
    @Command
    fun onShare(invoke: Invoke) {
        shares = invoke.parseArgs(SharesArgs::class.java).shares
        invoke.resolve()
    }

    /** The shared text, once; no `value` when there is none. */
    @Command
    fun takeShared(invoke: Invoke) {
        val text = shared
        shared = null
        invoke.resolve(JSObject().apply { if (text != null) put("value", text) })
    }

    @Command
    fun run(invoke: Invoke) {
        val args = invoke.parseArgs(RunArgs::class.java)
        background(invoke) {
            val request = YoutubeDLRequest(emptyList<String>()).addCommands(args.args.toList())
            val result = JSObject()
            try {
                val response = ytdl.execute(request, args.id) { _, _, line ->
                    args.lines.send(JSObject().apply { put("line", line) })
                }
                result.put("code", response.exitCode).put("out", response.out).put("err", response.err)
            } catch (e: YoutubeDL.CanceledException) {
                result.put("code", -1).put("out", "").put("err", "canceled")
            } catch (e: YoutubeDLException) {
                result.put("code", 1).put("out", "").put("err", e.message ?: e.toString())
            }
            invoke.resolve(result)
        }
    }

    @Command
    fun cancel(invoke: Invoke) {
        val args = invoke.parseArgs(IdArgs::class.java)
        YoutubeDL.getInstance().destroyProcessById(args.id)
        invoke.resolve()
    }

    @Command
    fun version(invoke: Invoke) {
        background(invoke) {
            val response = ytdl.execute(YoutubeDLRequest(emptyList<String>()).addOption("--version"))
            invoke.resolve(value(response.out.trim()))
        }
    }

    @Command
    fun update(invoke: Invoke) {
        background(invoke) {
            val status = ytdl.updateYoutubeDL(activity.applicationContext, YoutubeDL.UpdateChannel.STABLE)
            invoke.resolve(value(status?.name ?: ""))
        }
    }

    @Command
    fun readText(invoke: Invoke) {
        val uri = Uri.parse(invoke.parseArgs(UriArgs::class.java).uri)
        background(invoke) {
            val text = activity.contentResolver.openInputStream(uri)!!.use { it.readBytes().toString(Charsets.UTF_8) }
            invoke.resolve(value(text))
        }
    }

    private val notifications by lazy {
        activity.getSystemService(NotificationManager::class.java).apply {
            createNotificationChannel(NotificationChannel(JOBS_CHANNEL, "Downloads", NotificationManager.IMPORTANCE_LOW))
        }
    }
    private var askedPermission = false

    /** Called once at startup: Cancel actions on job notifications are sent to `cancels`. */
    @Command
    fun onCancel(invoke: Invoke) {
        val cancels = invoke.parseArgs(CancelArgs::class.java).cancels
        // Notifications left by a previous run that was killed.
        notifications.cancelAll()
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                cancels.send(JSObject().apply { put("id", intent.getIntExtra("id", -1)) })
            }
        }
        val context = activity.applicationContext
        val filter = IntentFilter(CANCEL_ACTION)
        if (Build.VERSION.SDK_INT >= 33) context.registerReceiver(receiver, filter, Context.RECEIVER_NOT_EXPORTED)
        else context.registerReceiver(receiver, filter)
        invoke.resolve()
    }

    /** Shows or updates a job's notification: its title, step, progress and a Cancel action. */
    @Command
    fun notify(invoke: Invoke) {
        val args = invoke.parseArgs(NotifyArgs::class.java)
        askNotificationPermission()
        val percent = args.fraction?.let { (it * 100).roundToInt() }
        val text = when (args.step) {
            "download" -> percent?.let { "Downloading · $it%" } ?: "Downloading…"
            "mux" -> "Finishing…"
            "encode" -> percent?.let { "Re-encoding · $it%" } ?: "Re-encoding…"
            else -> "Waiting…"
        }
        val flags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        val cancel = Intent(CANCEL_ACTION).setPackage(activity.packageName).putExtra("id", args.id)
        val open = activity.packageManager.getLaunchIntentForPackage(activity.packageName)
        val notification = Notification.Builder(activity, JOBS_CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(args.title)
            .setContentText(text)
            .setProgress(100, percent ?: 0, percent == null && args.step != "queued")
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setContentIntent(PendingIntent.getActivity(activity, 0, open, flags))
            .addAction(
                Notification.Action.Builder(
                    Icon.createWithResource(activity, android.R.drawable.ic_menu_close_clear_cancel),
                    "Cancel",
                    PendingIntent.getBroadcast(activity, args.id, cancel, flags),
                ).build()
            )
            .build()
        notifications.notify(args.id, notification)
        invoke.resolve()
    }

    @Command
    fun clearNotification(invoke: Invoke) {
        notifications.cancel(invoke.parseArgs(NotificationArgs::class.java).id)
        invoke.resolve()
    }

    /** Dark or light status and navigation bar icons, to match the app's theme rather than the phone's. */
    @Command
    fun systemBars(invoke: Invoke) {
        val dark = invoke.parseArgs(SystemBarsArgs::class.java).dark
        activity.runOnUiThread {
            val window = activity.window
            if (Build.VERSION.SDK_INT >= 30) {
                val light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS or WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS
                window.insetsController?.setSystemBarsAppearance(if (dark) 0 else light, light)
            } else {
                @Suppress("DEPRECATION")
                val light = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR or View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR
                @Suppress("DEPRECATION")
                window.decorView.systemUiVisibility = window.decorView.systemUiVisibility.let { if (dark) it and light.inv() else it or light }
            }
        }
        invoke.resolve()
    }

    /** Android 13+ asks before an app may post notifications; asked once per run. */
    private fun askNotificationPermission() {
        if (askedPermission || Build.VERSION.SDK_INT < 33) return
        askedPermission = true
        if (activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            activity.runOnUiThread { activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1) }
        }
    }

    /** Copies a picked file (a content URI) into `dir`, under its display name, for editing. */
    @Command
    fun importFile(invoke: Invoke) {
        val args = invoke.parseArgs(ImportArgs::class.java)
        val uri = Uri.parse(args.uri)
        background(invoke) {
            val resolver = activity.contentResolver
            val name = resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use {
                if (it.moveToFirst()) it.getString(0) else null
            } ?: uri.lastPathSegment?.substringAfterLast('/') ?: "video.mp4"
            val file = File(args.dir, name.replace('/', '_'))
            file.parentFile?.mkdirs()
            resolver.openInputStream(uri)!!.use { input -> file.outputStream().use { input.copyTo(it) } }
            invoke.resolve(JSObject().apply {
                put("path", file.absolutePath)
                put("name", name)
            })
        }
    }

    /** Copies a finished file into Download/Stash; MediaStore renames it if the name is taken. */
    @Command
    fun save(invoke: Invoke) {
        val args = invoke.parseArgs(SaveArgs::class.java)
        background(invoke) {
            val resolver = activity.contentResolver
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, args.name)
                put(MediaStore.Downloads.MIME_TYPE, args.mime)
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/Stash" + (args.folder?.let { "/$it" } ?: ""))
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: throw IllegalStateException("MediaStore refused ${args.name}")
            try {
                resolver.openOutputStream(uri)!!.use { out -> File(args.path).inputStream().use { it.copyTo(out) } }
                resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
            } catch (e: Exception) {
                resolver.delete(uri, null, null)
                throw e
            }
            invoke.resolve(value(uri.toString()))
        }
    }

    /** Renames a file saved by `save`, keeping its extension; `name (n)` when another file in
     * its folder has that name. */
    @Command
    fun rename(invoke: Invoke) {
        val args = invoke.parseArgs(RenameArgs::class.java)
        background(invoke) {
            val resolver = activity.contentResolver
            val uri = Uri.parse(args.uri)
            val columns = arrayOf(MediaStore.Downloads.DISPLAY_NAME, MediaStore.Downloads.RELATIVE_PATH)
            val (current, folder) = resolver.query(uri, columns, null, null, null)!!.use {
                check(it.moveToFirst()) { "File not found" }
                it.getString(0) to it.getString(1)
            }
            val ext = current.substringAfterLast('.', "").let { if (it.isEmpty()) "" else ".$it" }
            fun taken(name: String) = resolver.query(
                MediaStore.Downloads.EXTERNAL_CONTENT_URI,
                arrayOf(MediaStore.Downloads._ID),
                "${MediaStore.Downloads.DISPLAY_NAME} = ? AND ${MediaStore.Downloads.RELATIVE_PATH} = ? AND ${MediaStore.Downloads._ID} != ?",
                arrayOf(name, folder, ContentUris.parseId(uri).toString()),
                null,
            )!!.use { it.count > 0 }
            val name = generateSequence(0) { it + 1 }
                .map { if (it == 0) "${args.name}$ext" else "${args.name} ($it)$ext" }
                .first { !taken(it) }
            resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.DISPLAY_NAME, name) }, null, null)
            invoke.resolve(value(uri.toString()))
        }
    }

    @Command
    fun open(invoke: Invoke) {
        val uri = Uri.parse(invoke.parseArgs(UriArgs::class.java).uri)
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, activity.contentResolver.getType(uri))
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        activity.startActivity(Intent.createChooser(intent, null))
        invoke.resolve()
    }

    @Command
    fun share(invoke: Invoke) {
        val uri = Uri.parse(invoke.parseArgs(UriArgs::class.java).uri)
        val intent = Intent(Intent.ACTION_SEND)
            .setType(activity.contentResolver.getType(uri))
            .putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        activity.startActivity(Intent.createChooser(intent, null))
        invoke.resolve()
    }
}
