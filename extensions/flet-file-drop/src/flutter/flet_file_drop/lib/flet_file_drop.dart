library flet_file_drop;

import 'package:desktop_drop/desktop_drop.dart';
import 'package:flet/flet.dart';
import 'package:flutter/material.dart';

/// Registered by `flet build`: creates the FileDrop control asked for by the Python side.
class Extension extends FletExtension {
  @override
  Widget? createWidget(Key? key, Control control) {
    switch (control.type) {
      case "FileDrop":
        return FileDropControl(key: key, control: control);
      default:
        return null;
    }
  }
}

/// Its content, with a frame drawn while files are dragged over it; the paths of the dropped files are
/// sent to Python ("drop" event).
class FileDropControl extends StatefulWidget {
  final Control control;

  const FileDropControl({super.key, required this.control});

  @override
  State<FileDropControl> createState() => _FileDropControlState();
}

class _FileDropControlState extends State<FileDropControl> {
  bool _dragging = false;

  @override
  Widget build(BuildContext context) {
    var content = widget.control.buildWidget("content") ?? const SizedBox.shrink();
    return LayoutControl(
        control: widget.control,
        child: DropTarget(
            onDragEntered: (_) => setState(() => _dragging = true),
            onDragExited: (_) => setState(() => _dragging = false),
            onDragDone: (details) {
              setState(() => _dragging = false);
              widget.control.triggerEvent(
                  "drop", {"files": details.files.map((file) => file.path).toList()});
            },
            child: Container(
                foregroundDecoration: _dragging
                    ? BoxDecoration(
                        border: Border.all(
                            color: Theme.of(context).colorScheme.primary, width: 3))
                    : null,
                child: content)));
  }
}
