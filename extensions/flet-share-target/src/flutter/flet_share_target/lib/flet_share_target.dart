library flet_share_target;

import 'package:flet/flet.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/services.dart';

/// Registered by `flet build`: creates the ShareTarget service asked for by the Python side.
class Extension extends FletExtension {
  @override
  FletService? createService(Control control) {
    switch (control.type) {
      case "ShareTarget":
        return ShareTargetService(control: control);
      default:
        return null;
    }
  }
}

/// Talks to ShareTargetPlugin (Java): it keeps the text shared to the app until it is taken.
class ShareTargetService extends FletService {
  static const _channel = MethodChannel("flet_share_target");

  ShareTargetService({required super.control});

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invokeMethod);
    // Text shared while the app is open: read it and pass it on to Python.
    _channel.setMethodCallHandler((call) async {
      if (call.method == "shared") {
        final text = await _channel.invokeMethod<String>("takeSharedText");
        if (text != null) {
          control.triggerEvent("receive", {"text": text});
        }
      }
      return null;
    });
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    switch (name) {
      case "take_shared_text":
        return await _channel.invokeMethod<String>("takeSharedText");
      default:
        throw Exception("Unknown ShareTarget method: $name");
    }
  }

  @override
  void dispose() {
    debugPrint("ShareTarget(${control.id}).dispose()");
    control.removeInvokeMethodListener(_invokeMethod);
    _channel.setMethodCallHandler(null);
    super.dispose();
  }
}
