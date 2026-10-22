"""A list shown as a table (Edit files, Join files, Tag music, the Library): a title line whose columns sort the
rows when tapped (tapped again: the other way), rows dragged to move them when their order matters, and the
filters, opened by a button (another one clears them)."""

import flet as ft

from ui.style import S, SMALL_SIZE, XS, row

DRAG_HANDLE = 36  # room at the end of each row for the handle that drags it (a phone: a long press instead)


class TableColumn:
    """`width`: a fixed width (None: it shares the room left, by `expand`); `sort(item)`: the value its rows are
    sorted by (None: not sortable); `wide`: only shown on a wide screen (a computer)."""

    def __init__(self, key, label="", width=None, sort=None, expand=1, wide=False):
        self.key, self.label, self.width, self.sort, self.expand, self.wide = key, label, width, sort, expand, wide


class ListTable:
    """`cells(item)`: {column key: control} for one row; `on_sort()`: called after a column was tapped (the owner
    shows its rows again, sorted with sort()); `on_reorder(old, new)`: rows can be dragged (None: they cannot);
    `wrap(item, row)`: what a row is put in (a menu opened by a right click…)."""

    def __init__(self, app, columns, cells, on_sort, on_reorder=None, sort_key=None, ascending=True, wrap=None):
        self.app = app
        self.columns = [c for c in columns if not (c.wide and app.needs_bytes)]
        self.cells = cells
        self.on_sort = on_sort
        self.wrap = wrap or (lambda item, control: control)
        self.sort_key, self.ascending = sort_key, ascending
        self.end_room = DRAG_HANDLE if on_reorder is not None and not app.mobile else 0
        self.header = ft.Container(padding=ft.Padding(left=S, right=S + self.end_room, top=XS, bottom=XS),
                                   border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)))
        if on_reorder is not None:
            self.body = ft.ReorderableListView([], on_reorder=lambda e: on_reorder(e.old_index, e.new_index))
        else:
            self.body = ft.Column([], spacing=0)
        self.box = ft.Column([self.header, self.body], spacing=0)
        self.show_header()

    def place(self, column, control):
        if column.width is not None:
            return ft.Container(control, width=column.width)
        return ft.Container(control, expand=column.expand)

    def show_header(self):
        cells = []
        for column in self.columns:
            if column.width == 0:  # hidden for now
                continue
            label = [ft.Text(column.label, size=SMALL_SIZE, weight=ft.FontWeight.W_600, max_lines=1,
                             overflow=ft.TextOverflow.ELLIPSIS)] if column.label else []
            if column.key == self.sort_key:
                label.append(ft.Icon(ft.Icons.ARROW_UPWARD if self.ascending else ft.Icons.ARROW_DOWNWARD, size=14))
            cell = ft.Container(row(label, spacing=2), ink=column.sort is not None,
                                tooltip="Sort by %s" % column.label if column.sort is not None and column.label else None,
                                on_click=(lambda e, c=column: self.sort_by(c)) if column.sort is not None else None)
            cells.append(self.place(column, cell))
        self.header.content = row(cells, spacing=S)

    def sort_by(self, column):
        if self.sort_key == column.key:
            self.ascending = not self.ascending
        else:
            self.sort_key, self.ascending = column.key, True
        self.show_header()
        self.on_sort()

    def sort(self, items):
        """`items` in the order of the column tapped (as they are when none was)."""
        column = next((c for c in self.columns if c.key == self.sort_key and c.sort is not None), None)
        if column is None:
            return list(items)

        def value(item):
            found = column.sort(item)
            return found.casefold() if isinstance(found, str) else found
        filled = [item for item in items if value(item) not in (None, "")]
        empty = [item for item in items if value(item) in (None, "")]  # at the end, either way
        return sorted(filled, key=value, reverse=not self.ascending) + empty

    def make_row(self, item):
        cells = self.cells(item)
        return self.wrap(item, ft.Container(row([self.place(column, cells.get(column.key) or ft.Container())
                                                 for column in self.columns if column.width != 0], spacing=S),
                                            padding=ft.Padding(left=S, right=S + self.end_room, top=XS, bottom=XS),
                                            border=ft.Border(bottom=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT))))

    def show(self, items, empty=None):
        """The rows of `items` (in that order); `empty`: what is shown instead when there is none."""
        self.body.controls = [self.make_row(item) for item in items]
        if not items and empty is not None and isinstance(self.body, ft.Column):
            self.body.controls = [ft.Container(empty, padding=S)]
        self.header.visible = bool(items)


class Filters:
    """The filters of a list (menus, a field), shown under it by the filter button; the other button, shown while
    one is set, puts them all back. `on_change()` is called after each change."""

    def __init__(self, controls, on_change):
        self.controls = controls
        self.on_change = on_change
        self.defaults = [control.value for control in controls]
        for control in controls:
            control.col, control.expand = {"xs": 12, "sm": 6, "md": 4, "lg": 3}, True
            handler = "on_select" if isinstance(control, ft.Dropdown) else "on_change"
            setattr(control, handler, self.changed)
        self.panel = ft.Container(ft.ResponsiveRow(controls, spacing=S, run_spacing=S), visible=False,
                                  padding=ft.Padding(left=0, right=0, top=XS, bottom=XS))
        self.open_btn = ft.IconButton(icon=ft.Icons.FILTER_LIST, tooltip="Filters", on_click=self.toggle)
        self.clear_btn = ft.IconButton(icon=ft.Icons.FILTER_ALT_OFF, tooltip="Clear the filters", visible=False,
                                       on_click=self.clear)
        self.buttons = row([self.open_btn, self.clear_btn], spacing=0)

    def active(self):
        return any((control.value or "") != (default or "") for control, default in zip(self.controls, self.defaults))

    def refresh(self):
        self.clear_btn.visible = self.active()
        self.open_btn.selected = self.panel.visible
        self.open_btn.style = ft.ButtonStyle(bgcolor=ft.Colors.SECONDARY_CONTAINER) if self.active() else None

    def toggle(self, e=None):
        self.panel.visible = not self.panel.visible
        self.refresh()
        self.on_change()

    def changed(self, e=None):
        self.refresh()
        self.on_change()

    def clear(self, e=None):
        for control, default in zip(self.controls, self.defaults):
            control.value = default
        self.refresh()
        self.on_change()
