"""Temporarily suspend Fcitx5 conversion for literal input in one focused window.

The caller owns the shared-input lease and must call restore() in finally,
including after start() fails. No configuration, clipboard, selected engine,
virtual device or window focus is changed. Metadata never contains typed text.
A focus/engine change leaves restoration pending rather than modifying another
user context. This is cooperative focus checking, not an atomic desktop lock.
"""
from __future__ import annotations

import ast
import json
import re
import subprocess


class LiteralInputError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class ProviderUnavailable(LiteralInputError):
    def __init__(self, code='provider_unavailable'):
        super().__init__(code)


class LiteralInputGuard:
    def __init__(self, backend, window_id, pid=None):
        if type(window_id) is not int or window_id <= 0:
            raise ValueError('invalid literal input window')
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise ValueError('invalid literal input PID')
        self.backend = backend
        self.window_id = window_id
        self.pid = pid
        self.original_ime = None
        self._attempted = False
        self._restored = False
        self._started = False
        self._start_result = None
        self._owner = None

    @staticmethod
    def _invoke(argv):
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True,
                                    timeout=0.8, check=False)
        except FileNotFoundError:
            raise ProviderUnavailable('provider_unavailable') from None
        if result.returncode:
            if any(marker in result.stderr for marker in (
                'org.freedesktop.DBus.Error.ServiceUnknown',
                'org.freedesktop.DBus.Error.NameHasNoOwner',
            )):
                raise ProviderUnavailable('provider_unavailable')
            raise LiteralInputError('input_method_call_failed')
        if len(result.stdout) > 4096:
            raise LiteralInputError('malformed_input_method_result')
        try:
            value = ast.literal_eval(result.stdout.strip())
        except (ValueError, SyntaxError):
            raise LiteralInputError('malformed_input_method_result') from None
        if not isinstance(value, tuple):
            raise LiteralInputError('malformed_input_method_result')
        return value

    def _call(self, method):
        if method not in {'State', 'CurrentInputMethod', 'Deactivate', 'Activate'}:
            raise ValueError('unsupported input method operation')
        if self._owner is None:
            # Resolving through the bus does not auto-activate a dormant IME.
            # Address the unique owner afterwards, so a service restart cannot
            # cause restoration in an unrelated new provider instance.
            owner = self._invoke([
                '/usr/bin/gdbus', 'call', '--session', '--dest', 'org.freedesktop.DBus',
                '--object-path', '/org/freedesktop/DBus', '--method',
                'org.freedesktop.DBus.GetNameOwner', 'org.fcitx.Fcitx5',
            ])
            if (len(owner) != 1 or not isinstance(owner[0], str)
                    or not re.fullmatch(r':[0-9]+\.[0-9]+', owner[0])):
                raise LiteralInputError('malformed_input_method_owner')
            self._owner = owner[0]
        return self._invoke([
            '/usr/bin/gdbus', 'call', '--session', '--dest', self._owner,
            '--object-path', '/controller', '--method',
            'org.fcitx.Fcitx.Controller1.' + method,
        ])

    def _state(self):
        value = self._call('State')
        if len(value) != 1 or type(value[0]) is not int or value[0] not in {0, 1, 2}:
            raise LiteralInputError('malformed_input_method_state')
        return value[0]

    def _engine(self):
        value = self._call('CurrentInputMethod')
        if (len(value) != 1 or not isinstance(value[0], str) or not value[0]
                or len(value[0]) > 160 or any(ord(c) < 32 for c in value[0])):
            raise LiteralInputError('malformed_input_method_name')
        return value[0]

    def _same_focus(self):
        raw = self.backend.call_tool('focused_window', {})
        if raw.get('isError'):
            return False
        report = raw.get('structuredContent')
        if not isinstance(report, dict):
            for block in raw.get('content', []):
                if block.get('type') == 'text':
                    try:
                        report = json.loads(block['text'])
                    except (ValueError, KeyError):
                        continue
                    if isinstance(report, dict):
                        break
        if not isinstance(report, dict) or report.get('error'):
            return False
        window = report.get('focused_window')
        return (isinstance(window, dict)
                and type(window.get('window_id')) is int
                and window['window_id'] == self.window_id
                and window.get('focused') is True and window.get('hidden') is False
                and (self.pid is None or (type(window.get('pid')) is int and window['pid'] == self.pid)))

    def _metadata(self, status, reason=None):
        result = {'provider': 'fcitx5', 'status': status}
        if self.original_ime is not None:
            result['original_ime'] = self.original_ime
        if reason:
            result['reason'] = reason
        return result

    def start(self):
        if self._started:
            if self._start_result is not None:
                return dict(self._start_result)
            raise LiteralInputError('guard_start_already_failed')
        self._started = True
        try:
            focused = self._same_focus()
        except Exception:
            raise LiteralInputError('focus_unavailable') from None
        if not focused:
            raise LiteralInputError('focus_changed')
        try:
            state = self._state()
            if state != 2:
                self._start_result = self._metadata('already_inactive')
                return dict(self._start_result)
            self.original_ime = self._engine()
        except ProviderUnavailable:
            self._start_result = self._metadata('unavailable', 'provider_unavailable')
            return dict(self._start_result)
        except Exception:
            raise LiteralInputError('ime_query_failed') from None
        # Querying the controller took time; recheck before a side effect.
        try:
            focused = self._same_focus()
        except Exception:
            raise LiteralInputError('focus_unavailable') from None
        if not focused:
            raise LiteralInputError('focus_changed')
        self._attempted = True  # A timed-out Deactivate may already have applied.
        try:
            self._call('Deactivate')
        except Exception:
            raise LiteralInputError('deactivation_failed') from None
        try:
            inactive = self._state() == 1
        except Exception:
            raise LiteralInputError('deactivation_not_confirmed') from None
        if not inactive:
            raise LiteralInputError('deactivation_not_confirmed')
        self._start_result = self._metadata('disabled_for_literal_input')
        return dict(self._start_result)

    def restore(self):
        if not self._attempted:
            return self._metadata('not_needed')
        if self._restored:
            return self._metadata('already_restored')
        try:
            if not self._same_focus():
                return self._metadata('restoration_pending', 'focus_changed')
            state = self._state()
            engine = self._engine()
            if engine != self.original_ime and not engine.startswith('keyboard-'):
                return self._metadata('restoration_pending', 'input_method_changed')
            if state == 2:
                if engine != self.original_ime:
                    return self._metadata('restoration_pending', 'input_method_changed')
                self._restored = True
                return self._metadata('already_restored')
            # Do not let controller queries race a focus switch into another app.
            if not self._same_focus():
                return self._metadata('restoration_pending', 'focus_changed')
            self._call('Activate')
            if self._state() != 2 or self._engine() != self.original_ime:
                return self._metadata('restoration_failed', 'activation_not_confirmed')
            self._restored = True
            return self._metadata('restored')
        except Exception:
            return self._metadata('restoration_failed', 'controller_or_focus_unavailable')
