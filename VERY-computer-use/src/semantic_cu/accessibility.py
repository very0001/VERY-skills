"""Optional bounded AT-SPI event hints from an existing desktop session.

No permission/settings changes or text/value scraping. Accessible names are off
by default; enabling names still excludes objects with password roles.
"""

from __future__ import annotations

import importlib
import os
import threading
import time
from collections import deque


class AccessibilityUnavailable(RuntimeError):
    """The existing session accessibility bus/listener is unavailable."""


class ATSPIMonitor:
    """Explicit start/close; bounded queue drops oldest hints when full.

    Default owns pyatspi's process-global event loop, with one owner per process.
    Use manage_event_loop=False when the host already drives that loop. close
    deregisters only this listener and stops a loop only if this object owns it.
    This observer never enables accessibility in desktop settings.
    """

    DEFAULT_EVENTS = (
        "window:activate",
        "window:deactivate",
        "window:create",
        "window:destroy",
        "object:state-changed:focused",
        "object:children-changed",
        "object:property-change:accessible-name",
        "document:load-complete",
    )
    _owner_lock = threading.Lock()
    _loop_owner = None

    def __init__(
        self,
        *,
        event_types=None,
        max_events=256,
        include_names=False,
        manage_event_loop=True,
    ):
        if (
            isinstance(max_events, bool)
            or not isinstance(max_events, int)
            or max_events < 1
        ):
            raise ValueError("max_events must be a positive integer")
        self.event_types = tuple(
            self.DEFAULT_EVENTS if event_types is None else event_types
        )
        if not self.event_types or not all(
            isinstance(e, str) and e for e in self.event_types
        ):
            raise ValueError("event_types must contain nonempty event names")
        self.include_names = include_names
        self.manage_event_loop = manage_event_loop
        self._queue = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._thread = None
        self._registry = None
        self._registered = False
        self._started = False
        self._closed = False
        self.dropped_count = 0
        self.last_error = None

    @property
    def running(self):
        return self._started and not self._closed and self.last_error is None

    def start(self):
        if self._closed:
            raise AccessibilityUnavailable("Closed AT-SPI monitor cannot be restarted")
        if self._started:
            return self
        if not any(
            os.environ.get(name)
            for name in ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY")
        ):
            raise AccessibilityUnavailable(
                "No desktop/session bus is available for AT-SPI; use video/replay without semantic events"
            )
        try:
            pyatspi = importlib.import_module("pyatspi")
        except ImportError as exc:
            raise AccessibilityUnavailable(
                "AT-SPI events need optional system dependency python3-pyatspi"
            ) from exc
        self._registry = pyatspi.Registry
        if self.manage_event_loop:
            with self._owner_lock:
                if ATSPIMonitor._loop_owner is not None:
                    raise AccessibilityUnavailable(
                        "An AT-SPI event loop is already owned; use manage_event_loop=False"
                    )
                ATSPIMonitor._loop_owner = self
        try:
            self._registered = True  # also clean up partial batch registration
            self._registry.registerEventListener(self._on_event, *self.event_types)
            self._started = True
            if self.manage_event_loop:
                self._thread = threading.Thread(
                    target=self._run, name="semantic-cu-atspi", daemon=True
                )
                self._thread.start()
        except Exception as exc:
            self.close()
            raise AccessibilityUnavailable(
                f"Cannot subscribe to AT-SPI events: {exc}"
            ) from exc
        return self

    def _run(self):
        try:
            self._registry.start(gil=False)
        except Exception as exc:  # noqa: BLE001 - third-party DBus/backend failures
            self.last_error = f"AT-SPI event loop failed: {exc}"
        finally:
            if not self._closed and self.last_error is None:
                self.last_error = "AT-SPI event loop stopped unexpectedly"

    def _on_event(self, event):
        if self._closed:
            return
        item = {
            "type": str(getattr(event, "type", "unknown"))[:200],
            "timestamp": time.monotonic(),
            "source": "atspi",
        }
        for name in ("detail1", "detail2"):
            value = getattr(event, name, None)
            if isinstance(value, (bool, int, float)):
                item[name] = value
        source = getattr(event, "source", None)
        if source is not None:
            try:
                role = str(source.getRoleName())[:100]
                item["role"] = role
                password = "password" in role.casefold()
                try:
                    pyatspi = importlib.import_module("pyatspi")
                    password = (
                        password or source.getRole() == pyatspi.ROLE_PASSWORD_TEXT
                    )
                except (ImportError, AttributeError, RuntimeError):
                    pass
                if self.include_names and not password:
                    item["name"] = str(source.name or "")[:200]
            except Exception:  # noqa: BLE001 - a defunct accessible can fail at any call
                item["defunct"] = True
        with self._lock:
            if len(self._queue) == self._queue.maxlen:
                self.dropped_count += 1
            self._queue.append(item)

    def drain(self):
        with self._lock:
            events = list(self._queue)
            self._queue.clear()
        return events

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._registry is not None and self._registered:
            try:
                self._registry.deregisterEventListener(
                    self._on_event, *self.event_types
                )
            except Exception as exc:  # noqa: BLE001 - third-party DBus/backend failures
                self.last_error = f"AT-SPI listener cleanup failed: {exc}"
            self._registered = False
        with self._owner_lock:
            owned = ATSPIMonitor._loop_owner is self
        if owned:
            try:
                if self._thread is not None:
                    self._registry.stop()
                    if self._thread is not threading.current_thread():
                        self._thread.join(timeout=1.0)
            except Exception as exc:  # noqa: BLE001 - third-party DBus/backend failures
                self.last_error = f"AT-SPI event loop cleanup failed: {exc}"
            finally:
                with self._owner_lock:
                    if self._thread is None or not self._thread.is_alive():
                        ATSPIMonitor._loop_owner = None
                    else:
                        self.last_error = "AT-SPI event loop did not stop within 1s; do not start another owning monitor"
        self._started = False

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()
