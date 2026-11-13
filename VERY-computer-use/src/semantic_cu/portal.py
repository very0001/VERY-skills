"""Explicit temporary ScreenCast consent; rebuilt from reviewed task context.

Importing/constructing does not contact a desktop. Only start() connects to the
session bus. One private asyncio thread keeps the connection and granted FD
alive until close() or revocation. No permission bypass or alternate capture.
"""
from __future__ import annotations

import asyncio
import importlib
import math
import os
import re
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from contextlib import suppress
from typing import Any

from .capture import CaptureError, CaptureUnavailable

PORTAL = 'org.freedesktop.portal.Desktop'
DESKTOP = '/org/freedesktop/portal/desktop'
SCREENCAST = 'org.freedesktop.portal.ScreenCast'
REQUEST = 'org.freedesktop.portal.Request'
SESSION = 'org.freedesktop.portal.Session'
DBUS = 'org.freedesktop.DBus'
DBUS_PATH = '/org/freedesktop/DBus'


class PortalError(CaptureError):
    """The portal operation failed; no alternative source was opened."""


class PortalUnavailable(PortalError, CaptureUnavailable):
    """A required dependency, bus, service or capability is unavailable."""


class PortalCancelled(PortalError):
    """The user cancelled the desktop's source-selection/consent dialog."""


class PortalTimeout(PortalError):
    """The overall startup budget, including consent, expired."""


class PortalClosed(PortalError):
    """The caller, portal or disconnected bus ended the session."""


def _load_dbus():
    try:
        api = importlib.import_module('dbus_next')
        aio = importlib.import_module('dbus_next.aio')
    except ImportError as exc:
        raise PortalUnavailable('Wayland portal consent needs optional dependency dbus-next; '
                                'install the portal extra in the application Python environment') from exc
    return api, aio.MessageBus


def _plain(value: Any) -> Any:
    if hasattr(value, 'signature') and hasattr(value, 'value'):
        return _plain(value.value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _uint(value: Any) -> bool:
    return type(value) is int and 0 <= value <= 0xFFFFFFFF


def _object_path(value: Any, kind: str) -> bool:
    return (isinstance(value, str) and value.startswith(f'{DESKTOP}/{kind}/')
            and re.fullmatch(r'(?:/[A-Za-z0-9_]+)+', value) is not None)


def _close_fds(fds):
    for fd in set(fds):
        with suppress(OSError):
            os.close(fd)


class _PortalClient:
    """All callbacks and methods belong to one asyncio loop."""
    def __init__(self, source_types: int, cursor_mode: int | None,
                 parent_window: str, on_closed: Callable[[PortalError], None]):
        self.source_types, self.cursor_mode = source_types, cursor_mode
        self.parent_window, self.on_closed = parent_window, on_closed
        self.bus = None
        self.owner = self.session_handle = None
        self.fd = self.node_id = None
        self.stream_properties = {}
        self.version = None
        self._pending_path = self._response_path = None
        self._responses = {}
        self._response_event = asyncio.Event()
        self._handling_signals = self._closing = False
        self._revoked = None

    async def _call(self, destination, path, interface, member, signature='', body=None):
        if self._revoked is not None and member != 'Close':
            raise self._revoked
        reply = await self.bus.call(self.api.Message(destination=destination, path=path,
            interface=interface, member=member, signature=signature, body=body or []))
        if reply is None:
            raise PortalError(f'{member} returned no D-Bus reply')
        if reply.message_type == self.api.MessageType.ERROR:
            _close_fds(reply.unix_fds)
            detail = str(reply.body[0]) if reply.body else 'no details'
            raise PortalError(f'{member}: {reply.error_name}: {detail}')
        if reply.message_type != self.api.MessageType.METHOD_RETURN:
            _close_fds(reply.unix_fds)
            raise PortalError(f'{member} returned an invalid D-Bus reply')
        return reply

    def _on_message(self, message):
        if message.message_type != self.api.MessageType.SIGNAL or message.sender != self.owner:
            return
        if (message.interface == SESSION and message.member == 'Closed'
                and message.path == self.session_handle and not self._closing):
            self._revoked = PortalClosed('The desktop portal closed the screen-sharing session')
            self.on_closed(self._revoked)
        elif (message.interface == REQUEST and message.member == 'Response'
              and self._pending_path is not None and _object_path(message.path, 'request')
              and (message.path == self._response_path or len(self._responses) < 32)):
            # Early Response signals are valid, including a returned path that
            # differs from the predicted modern handle. Bound unrelated buffering.
            self._responses[message.path] = message
            self._response_event.set()

    async def _request(self, member, signature, arguments, options):
        token = 'semantic_cu_' + uuid.uuid4().hex
        sender = self.bus.unique_name.lstrip(':').replace('.', '_')
        self._pending_path = f'{DESKTOP}/request/{sender}/{token}'
        self._response_path = None
        self._responses.clear()
        self._response_event.clear()
        options = {**options, 'handle_token': self.api.Variant('s', token)}
        reply = await self._call(self.owner, DESKTOP, SCREENCAST, member, signature,
                                 [*arguments, options])
        if (reply.signature != 'o' or len(reply.body) != 1
                or not _object_path(reply.body[0], 'request')):
            raise PortalError(f'{member} returned an invalid request handle')
        self._pending_path = self._response_path = reply.body[0]
        while self._response_path not in self._responses:
            self._response_event.clear()
            await self._response_event.wait()
        response = self._responses.pop(self._response_path)
        self._pending_path = None
        self._responses.clear()
        if (response.signature != 'ua{sv}' or len(response.body) != 2
                or not _uint(response.body[0]) or not isinstance(response.body[1], dict)):
            raise PortalError(f'{member} returned a malformed portal response')
        code, results = response.body
        if code == 1:
            raise PortalCancelled(f'Screen sharing was cancelled during {member}')
        if code != 0:
            raise PortalError(f'Screen sharing failed during {member} (portal response {code})')
        return _plain(results)

    async def open(self):
        self.api, bus_type = _load_dbus()
        try:
            self.bus = bus_type(negotiate_unix_fd=True)
            await self.bus.connect()
        except Exception as exc:
            raise PortalUnavailable(f'Cannot connect to the desktop session bus: {exc}') from exc
        try:
            reply = await self._call(PORTAL, DESKTOP, 'org.freedesktop.DBus.Properties',
                                     'GetAll', 's', [SCREENCAST])
        except PortalError as exc:
            raise PortalUnavailable(f'ScreenCast portal is unavailable: {exc}') from exc
        if (reply.signature != 'a{sv}' or len(reply.body) != 1
                or not isinstance(reply.body[0], dict) or not isinstance(reply.sender, str)
                or not reply.sender.startswith(':')):
            raise PortalError('ScreenCast portal returned invalid properties or sender')
        # GetAll can activate the service. Pin all later calls/signals to the
        # unique owner that replied, not to an arbitrary incoming signal sender.
        self.owner = reply.sender
        properties = _plain(reply.body[0])
        self.version = properties.get('version')
        available = properties.get('AvailableSourceTypes')
        if not _uint(available) or self.source_types & available != self.source_types:
            raise PortalUnavailable('The portal does not support the requested source types')
        if self.cursor_mode is not None:
            cursors = properties.get('AvailableCursorModes', 0)
            if not _uint(cursors) or not cursors & self.cursor_mode:
                raise PortalUnavailable('The portal does not support the requested cursor mode')
        self.bus.add_message_handler(self._on_message)
        self._handling_signals = True
        for interface, member, kind in ((REQUEST, 'Response', 'request'),
                                        (SESSION, 'Closed', 'session')):
            rule = (f"type='signal',sender='{self.owner}',interface='{interface}',"
                    f"member='{member}',path_namespace='{DESKTOP}/{kind}'")
            await self._call(DBUS, DBUS_PATH, DBUS, 'AddMatch', 's', [rule])
        token = 'semantic_cu_' + uuid.uuid4().hex
        sender = self.bus.unique_name.lstrip(':').replace('.', '_')
        self.session_handle = f'{DESKTOP}/session/{sender}/{token}'
        created = await self._request('CreateSession', 'a{sv}', [],
                                     {'session_handle_token': self.api.Variant('s', token)})
        handle = created.get('session_handle')
        if not _object_path(handle, 'session'):
            raise PortalError('CreateSession returned an invalid session_handle')
        self.session_handle = handle
        options = {'types': self.api.Variant('u', self.source_types),
                   'multiple': self.api.Variant('b', False)}
        if self.cursor_mode is not None:
            options['cursor_mode'] = self.api.Variant('u', self.cursor_mode)
        # No restore_token and no persistence: omitted persist_mode defaults 0.
        await self._request('SelectSources', 'oa{sv}', [handle], options)
        started = await self._request('Start', 'osa{sv}', [handle, self.parent_window], {})
        streams = started.get('streams')
        if (not isinstance(streams, list) or len(streams) != 1
                or not isinstance(streams[0], list) or len(streams[0]) != 2
                or not _uint(streams[0][0]) or not isinstance(streams[0][1], dict)):
            raise PortalError('Start did not return exactly one valid PipeWire stream')
        self.node_id, self.stream_properties = streams[0]
        reply = await self._call(self.owner, DESKTOP, SCREENCAST, 'OpenPipeWireRemote',
                                 'oa{sv}', [handle, {}])
        received_fds = list(reply.unix_fds)
        try:
            # Low-level D-Bus h is an index into unix_fds, NOT a raw OS FD.
            if (reply.signature != 'h' or len(reply.body) != 1
                    or not _uint(reply.body[0]) or reply.body[0] >= len(received_fds)):
                raise PortalError('OpenPipeWireRemote did not transfer a valid Unix FD')
            received_fd = received_fds[reply.body[0]]
            os.fstat(received_fd)
            self.fd = os.dup(received_fd)
            os.set_inheritable(self.fd, False)
        except OSError as exc:
            raise PortalError(f'Cannot retain the portal PipeWire FD: {exc}') from exc
        finally:
            _close_fds(received_fds)
        return self.fd, self.node_id

    async def close(self):
        self._closing = True
        if self.fd is not None:
            _close_fds([self.fd])
            self.fd = None
        if self.bus is None:
            return
        try:
            for path, interface in ((self._pending_path, REQUEST), (self.session_handle, SESSION)):
                if path is not None and self.owner is not None:
                    with suppress(Exception):
                        await asyncio.wait_for(self._call(self.owner, path, interface, 'Close'), 1.0)
        finally:
            if self._handling_signals:
                self.bus.remove_message_handler(self._on_message)
            self.bus.disconnect()
            with suppress(Exception):
                await asyncio.wait_for(asyncio.shield(self.bus.wait_for_disconnect()), 1.0)


class PortalSession:
    """One explicit consent flow returning a same-process borrowed (fd,node_id).

    Keep the session alive; close the capture source before this object. The
    returned FD belongs to this object. Never close or transfer its number as
    if it were a cross-process capability. Active start() calls are idempotent;
    closed or failed instances cannot restart. close() may interrupt start()
    from another thread. Context entry does not automatically request consent.
    """
    def __init__(self, *, source_types=1, cursor_mode=None, parent_window='', timeout=120.0):
        if not _uint(source_types) or source_types == 0 or source_types & ~7:
            raise ValueError('source_types must be a nonzero mask of MONITOR=1, WINDOW=2, VIRTUAL=4')
        if cursor_mode is not None and (type(cursor_mode) is not int or cursor_mode not in (1, 2, 4)):
            raise ValueError('cursor_mode must be None, Hidden=1, Embedded=2, or Metadata=4')
        if not isinstance(parent_window, str):
            raise TypeError('parent_window must be a portal window identifier string')
        timeout = float(timeout)
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('timeout must be positive and finite')
        self.source_types, self.cursor_mode = source_types, cursor_mode
        self.parent_window, self.timeout = parent_window, timeout
        self._lock = threading.Lock()
        self._ready = Future()
        self._thread = self._loop = self._stop_signal = None
        self._stop_requested, self._finished = threading.Event(), threading.Event()
        self._error = self._client = self._result = None

    def start(self):
        with self._lock:
            if self._stop_requested.is_set() or self._finished.is_set():
                raise self._error or PortalClosed('This portal session is already closed')
            if self._thread is None:
                self._thread = threading.Thread(target=self._run, name='semantic-cu-portal', daemon=True)
                self._thread.start()
        try:
            result = self._ready.result()
        except BaseException:
            self.close()
            raise
        self.check_alive()
        return result

    def _remote_closed(self, error):
        self._error = error
        self._stop_signal.set()

    async def _watch_disconnect(self):
        # Cancelling our watcher must not cancel dbus-next's shared disconnect Future.
        await asyncio.shield(self._client.bus.wait_for_disconnect())

    async def _serve(self):
        self._loop = asyncio.get_running_loop()
        self._stop_signal = asyncio.Event()
        if self._stop_requested.is_set():
            self._stop_signal.set()
        self._client = _PortalClient(self.source_types, self.cursor_mode,
                                     self.parent_window, self._remote_closed)
        tasks = []
        try:
            opening = asyncio.create_task(asyncio.wait_for(self._client.open(), self.timeout))
            stopping = asyncio.create_task(self._stop_signal.wait())
            tasks.extend((opening, stopping))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if stopping in done:
                raise self._error or PortalClosed('The portal consent flow was closed')
            self._result = opening.result()
            if self._stop_signal.is_set() or self._stop_requested.is_set():
                raise self._error or PortalClosed('The portal session was closed')
            self._ready.set_result(self._result)
            disconnected = asyncio.create_task(self._watch_disconnect())
            tasks.append(disconnected)
            done, _ = await asyncio.wait((stopping, disconnected), return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done and not self._stop_requested.is_set():
                self._error = PortalClosed('The desktop session bus disconnected')
        except TimeoutError:
            self._error = PortalTimeout(f'Screen-sharing consent timed out after {self.timeout:g}s')
        except PortalError as exc:
            self._error = exc
        except Exception as exc:  # noqa: BLE001 - transport/plugin errors cross the worker boundary via _error.
            self._error = PortalError(f'Screen-sharing portal failed: {exc}')
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._client.close()

    def _run(self):
        try:
            asyncio.run(self._serve())
        except Exception as exc:  # noqa: BLE001 - always resolve the caller Future after worker cleanup fails.
            if self._error is None:
                self._error = PortalError(f'Portal cleanup failed: {exc}')
        finally:
            self._finished.set()
            if not self._ready.done():
                self._ready.set_exception(self._error or PortalClosed('The portal session was closed'))

    def check_alive(self):
        if self._error is not None:
            raise self._error
        if self._stop_requested.is_set() or self._finished.is_set():
            raise PortalClosed('This portal session is closed')
        if self._result is None:
            raise PortalClosed('This portal session has not started')

    @property
    def fd(self):
        self.check_alive()
        return self._result[0]

    @property
    def node_id(self):
        self.check_alive()
        return self._result[1]

    @property
    def stream_properties(self):
        self.check_alive()
        return dict(self._client.stream_properties)

    def close(self):
        with self._lock:
            self._stop_requested.set()
            thread = self._thread
            if thread is None:
                self._finished.set()
            elif self._loop is not None and self._stop_signal is not None:
                with suppress(RuntimeError):
                    self._loop.call_soon_threadsafe(self._stop_signal.set)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise PortalError('Portal cleanup did not finish within 5 seconds')

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
