"""UI test conftest — MockCanvas and shared fixtures."""

import copy
import unittest.mock as _mock
import pytest


class MockCanvas:
    """In-memory tkinter.Canvas replacement for headless testing.

    Records all canvas operations (create, configure, delete, move) so that
    tests can inspect widget rendering without an X11 display or Tk mainloop.

    Tag semantics mirror real tkinter:
      - Every item can carry multiple string tags (stored as a tuple).
      - ``find_withtag("all")`` returns every live item.
      - ``find_withtag(tag)`` returns items whose tag-set contains *tag*.
      - Integer item-IDs are also valid as tag arguments (match that single item).
      - ``delete("all")`` wipes the canvas.  ``delete(tag)`` removes matching items.
      - ``itemconfig(tag, **kw)`` applies to ALL items that match *tag*.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, width=240, height=240, bg="#222222"):
        self._items = {}        # id -> {type, coords, options, tags}
        self._next_id = 1
        self._width = width
        self._height = height
        self._bg = bg
        self._timers = {}       # timer_id_str -> (ms, func, args)
        self._next_timer = 1
        self._now_ms = 0
        self._deadlines = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _alloc_id(self):
        """Return the next unique integer item ID and advance the counter."""
        item_id = self._next_id
        self._next_id += 1
        return item_id

    def _store(self, item_type, coords, kwargs):
        """Create a new canvas item, store it, and return its integer ID."""
        item_id = self._alloc_id()
        tags = self._normalize_tags(kwargs.pop("tags", ()))
        self._items[item_id] = {
            "type": item_type,
            "coords": list(coords),
            "options": dict(kwargs),
            "tags": tags,
        }
        return item_id

    @staticmethod
    def _normalize_tags(raw):
        """Accept a string, tuple, or list and return a tuple of strings."""
        if isinstance(raw, str):
            return tuple(raw.split())
        if isinstance(raw, (list, tuple)):
            return tuple(str(t) for t in raw)
        return ()

    def _resolve_ids(self, tag_or_id):
        """Return a list of item IDs matching *tag_or_id*.

        - ``"all"`` -> every live item
        - an integer (or stringified integer) -> that single item (if alive)
        - a string tag -> all items whose tag-set contains it
        """
        if tag_or_id == "all":
            return list(self._items.keys())

        # Integer id (or string that looks like one)
        try:
            int_id = int(tag_or_id)
            if int_id in self._items:
                return [int_id]
            return []
        except (TypeError, ValueError):
            pass

        # String tag lookup
        tag_str = str(tag_or_id)
        return [
            iid for iid, item in self._items.items()
            if tag_str in item["tags"]
        ]

    # ------------------------------------------------------------------
    # Creation methods — each returns an integer item ID
    # ------------------------------------------------------------------

    def create_rectangle(self, x1, y1, x2, y2, **kwargs):
        return self._store("rectangle", (x1, y1, x2, y2), kwargs)

    def create_text(self, x, y, **kwargs):
        return self._store("text", (x, y), kwargs)

    def create_image(self, x, y, **kwargs):
        return self._store("image", (x, y), kwargs)

    def create_line(self, *coords, **kwargs):
        return self._store("line", coords, kwargs)

    def create_oval(self, x1, y1, x2, y2, **kwargs):
        return self._store("oval", (x1, y1, x2, y2), kwargs)

    def create_polygon(self, *coords, **kwargs):
        return self._store("polygon", coords, kwargs)

    # ------------------------------------------------------------------
    # Item modification
    # ------------------------------------------------------------------

    def itemconfig(self, tag_or_id, **kwargs):
        """Update options on every item matching *tag_or_id*."""
        ids = self._resolve_ids(tag_or_id)
        new_tags = kwargs.pop("tags", None)
        for iid in ids:
            self._items[iid]["options"].update(kwargs)
            if new_tags is not None:
                self._items[iid]["tags"] = self._normalize_tags(new_tags)

    def itemconfigure(self, tag_or_id, **kwargs):
        """Alias for :meth:`itemconfig` (matches tkinter)."""
        return self.itemconfig(tag_or_id, **kwargs)

    def coords(self, item_id, *new_coords):
        """Get or set coordinates for *item_id*.

        With no extra arguments, returns the current coordinate list.
        With arguments, replaces the stored coordinates.
        """
        ids = self._resolve_ids(item_id)
        if not ids:
            return []
        target = ids[0]
        if new_coords:
            self._items[target]["coords"] = list(new_coords)
            return None
        return list(self._items[target]["coords"])

    def move(self, tag_or_id, dx, dy):
        """Translate every matching item by *(dx, dy)*."""
        for iid in self._resolve_ids(tag_or_id):
            c = self._items[iid]["coords"]
            self._items[iid]["coords"] = [
                c[i] + (dx if i % 2 == 0 else dy) for i in range(len(c))
            ]

    def tag_raise(self, tag_or_id, above=None):
        """No-op — draw order is not meaningful in headless tests."""

    def tag_lower(self, tag_or_id, below=None):
        """No-op — draw order is not meaningful in headless tests."""

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def delete(self, *args):
        """Delete items by tag or id.  ``delete("all")`` clears everything."""
        for tag_or_id in args:
            if tag_or_id == "all":
                self._items.clear()
                return
            for iid in self._resolve_ids(tag_or_id):
                self._items.pop(iid, None)

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def find_all(self):
        """Return a tuple of all live item IDs."""
        return tuple(self._items.keys())

    def find_withtag(self, tag):
        """Return a tuple of item IDs matching *tag*."""
        return tuple(self._resolve_ids(tag))

    def gettags(self, item_id):
        """Return the tag tuple for *item_id*."""
        ids = self._resolve_ids(item_id)
        if not ids:
            return ()
        return self._items[ids[0]]["tags"]

    def itemcget(self, item_id, option):
        """Return the value of *option* for *item_id* (as a string)."""
        ids = self._resolve_ids(item_id)
        if not ids:
            return ""
        return str(self._items[ids[0]]["options"].get(option, ""))

    def type(self, item_id):
        """Return the type string (``"rectangle"``, ``"text"``, etc.)."""
        ids = self._resolve_ids(item_id)
        if not ids:
            return ""
        return self._items[ids[0]]["type"]

    def bbox(self, *args):
        """Return a bounding box ``(x1, y1, x2, y2)`` for matching items.

        For simplicity the bounding box is the min/max of stored coordinates.
        If no items match, returns ``None`` (as tkinter does).
        """
        ids = []
        if not args:
            ids = list(self._items.keys())
        else:
            for a in args:
                ids.extend(self._resolve_ids(a))
        if not ids:
            return None

        all_x = []
        all_y = []
        for iid in ids:
            c = self._items[iid]["coords"]
            all_x.extend(c[0::2])
            all_y.extend(c[1::2])
        if not all_x:
            return None
        return (min(all_x), min(all_y), max(all_x), max(all_y))

    def winfo_width(self):
        return self._width

    def winfo_height(self):
        return self._height

    # ------------------------------------------------------------------
    # Tag operations
    # ------------------------------------------------------------------

    def addtag_withtag(self, new_tag, existing_tag):
        """Add *new_tag* to every item that currently has *existing_tag*."""
        for iid in self._resolve_ids(existing_tag):
            tags = self._items[iid]["tags"]
            if new_tag not in tags:
                self._items[iid]["tags"] = tags + (new_tag,)

    def dtag(self, tag_or_id, tag_to_remove):
        """Remove *tag_to_remove* from every item matching *tag_or_id*."""
        for iid in self._resolve_ids(tag_or_id):
            self._items[iid]["tags"] = tuple(
                t for t in self._items[iid]["tags"] if t != tag_to_remove
            )

    # ------------------------------------------------------------------
    # Layout / lifecycle compatibility (no-ops)
    # ------------------------------------------------------------------

    def grid(self, **kwargs):
        pass

    def grid_remove(self):
        pass

    def pack(self, **kwargs):
        pass

    def place(self, **kwargs):
        pass

    def destroy(self):
        self._items.clear()

    def update(self):
        pass

    def update_idletasks(self):
        pass

    def after(self, ms, func=None, *args):
        """Store a scheduled callback WITHOUT executing it.

        Returns a string timer ID.  Tests can inspect ``_timers`` or call
        :meth:`fire_after` to trigger stored callbacks manually.
        """
        timer_id = f"after#{self._next_timer}"
        self._next_timer += 1
        self._timers[timer_id] = (ms, func, args)
        self._deadlines[timer_id] = self._now_ms + ms
        return timer_id

    def after_cancel(self, timer_id):
        """Cancel a previously scheduled callback."""
        self._timers.pop(timer_id, None)
        self._deadlines.pop(timer_id, None)

    # ------------------------------------------------------------------
    # Test-only helpers (NOT part of tkinter API)
    # ------------------------------------------------------------------

    def get_items_by_type(self, item_type):
        """Return a list of ``(id, item_dict)`` for items of *item_type*."""
        return [
            (iid, item)
            for iid, item in self._items.items()
            if item["type"] == item_type
        ]

    def get_item(self, item_id):
        """Return the full item dict for *item_id*, or ``None``."""
        return self._items.get(item_id)

    def get_all_text(self):
        """Return a list of all ``text`` option strings currently on canvas."""
        return [
            item["options"]["text"]
            for item in self._items.values()
            if item["type"] == "text" and "text" in item["options"]
        ]

    def snapshot(self):
        """Return a deep-copy of the full canvas state for comparison."""
        return {
            "width": self._width,
            "height": self._height,
            "bg": self._bg,
            "items": copy.deepcopy(self._items),
        }

    def fire_after(self, timer_id):
        """Execute and remove a stored ``after`` callback (test helper)."""
        entry = self._timers.pop(timer_id, None)
        self._deadlines.pop(timer_id, None)
        if entry is None:
            return
        _ms, func, args = entry
        if func is not None:
            func(*args)

    def fire_all_after(self):
        """Execute and remove ALL stored ``after`` callbacks (test helper)."""
        for timer_id in list(self._timers):
            self.fire_after(timer_id)

    def advance(self, milliseconds):
        """Run callbacks due within virtual time, including newly queued work.

        Unlike fire_all_after(), this preserves delay ordering. The bound
        catches accidental zero-delay loops without running real-time sleeps.
        """
        end = self._now_ms + milliseconds
        for _ in range(1000):
            due = [key for key in self._timers
                   if self._deadlines[key] <= end]
            if not due:
                self._now_ms = end
                return
            key = min(due, key=lambda key: self._deadlines[key])
            self._now_ms = self._deadlines[key]
            self.fire_after(key)
        raise AssertionError('MockCanvas callback limit exceeded')


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def target(request):
    """Return the --target value ('current' or 'original')."""
    return request.config.getoption("--target")


@pytest.fixture
def canvas():
    """Provide a fresh 240x240 MockCanvas for each test."""
    return MockCanvas(width=240, height=240, bg="#222222")


@pytest.fixture(autouse=True)
def _mock_tk_font():
    """Prevent Toast._draw() and ConsoleView from crashing in headless env.

    tkinter.font.Font and PIL.ImageTk.PhotoImage both require a Tk root
    window.  MockCanvas doesn't provide one.  Patch them globally so that
    Font.measure / Font.metrics return sensible defaults.
    """
    _metrics = {'linespace': 14, 'ascent': 11, 'descent': 3}

    def _font_metrics(*args):
        if args:
            return _metrics.get(args[0], 0)
        return _metrics

    mock_font_cls = _mock.MagicMock()
    inst = _mock.MagicMock()
    inst.measure.return_value = 100
    inst.metrics.side_effect = _font_metrics
    inst.actual.return_value = {'family': 'mononoki', 'size': 12}
    mock_font_cls.return_value = inst

    with _mock.patch('tkinter.font.Font', mock_font_cls), \
         _mock.patch('PIL.ImageTk.PhotoImage', _mock.MagicMock()):
        yield
