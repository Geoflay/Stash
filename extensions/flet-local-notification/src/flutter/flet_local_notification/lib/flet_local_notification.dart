library flet_local_notification;

import 'package:flet/flet.dart';
import 'package:flutter/services.dart';

/// Registered by `flet build`: creates the LocalNotification service asked for by the Python side.
class Extension extends FletExtension {
  @override
  FletService? createService(Control control) {
    switch (control.type) {
      case "LocalNotification":
        return LocalNotificationService(control: control);
      default:
        return null;
    }
  }
}

/// Passes the Python calls on to LocalNotificationPlugin (Java), and its Cancel button presses back to Python.
class LocalNotificationService extends FletService {
  static const _channel = MethodChannel("flet_local_notification");

  LocalNotificationService({required super.control});

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invokeMethod);
    _channel.setMethodCallHandler((call) async {
      if (call.method == "cancel") {
        control.triggerEvent("cancel", {"id": call.arguments});
      }
      return null;
    });
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    switch (name) {
      case "show":
        return await _channel.invokeMethod<bool>("show", {
          "title": args["title"] ?? "",
          "body": args["body"] ?? "",
          "id": args["id"] ?? 0,
          "alert": args["alert"] ?? true,
        });
      case "progress":
        return await _channel.invokeMethod<bool>("progress", {
          "id": args["id"] ?? 0,
          "title": args["title"] ?? "",
          "body": args["body"] ?? "",
          "percent": args["percent"] ?? -1,
          "cancelLabel": args["cancel_label"] ?? "Cancel",
        });
      case "cancel":
        return await _channel.invokeMethod("cancel", {"id": args["id"] ?? 0});
      default:
        throw Exception("Unknown LocalNotification method: $name");
    }
  }

  @override
  void dispose() {
    control.removeInvokeMethodListener(_invokeMethod);
    _channel.setMethodCallHandler(null);
    super.dispose();
  }
}
