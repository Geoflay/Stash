library flet_open_file;

import 'package:flet/flet.dart';
import 'package:flutter/services.dart';

/// Registered by `flet build`: creates the OpenFile service asked for by the Python side.
class Extension extends FletExtension {
  @override
  FletService? createService(Control control) {
    switch (control.type) {
      case "OpenFile":
        return OpenFileService(control: control);
      default:
        return null;
    }
  }
}

/// Passes the Python calls on to OpenFilePlugin (Java).
class OpenFileService extends FletService {
  static const _channel = MethodChannel("flet_open_file");

  OpenFileService({required super.control});

  @override
  void init() {
    super.init();
    control.addInvokeMethodListener(_invokeMethod);
  }

  Future<dynamic> _invokeMethod(String name, dynamic args) async {
    switch (name) {
      case "open":
        return await _channel.invokeMethod<bool>("open", {"path": args["path"], "mime": args["mime"]});
      default:
        throw Exception("Unknown OpenFile method: $name");
    }
  }

  @override
  void dispose() {
    control.removeInvokeMethodListener(_invokeMethod);
    super.dispose();
  }
}
