"""Recover only an authorized user's unambiguous graphical session context.

No DISPLAY is guessed and no environment belonging to another UID is used.
Callers receive a copy and decide whether to install it before constructing
desktop transports. Errors contain field names, never environment values.
"""
from __future__ import annotations

import os
from pathlib import Path


_FIELDS = frozenset({
    "DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR",
    "XAUTHORITY", "XDG_SESSION_TYPE",
})
_REQUIRED = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XDG_SESSION_TYPE")


def _missing(env):
    if env.get("XDG_SESSION_TYPE") and env["XDG_SESSION_TYPE"] not in {"x11", "wayland"}:
        raise RuntimeError("desktop_environment_invalid_session_type")
    missing = [key for key in _REQUIRED if not isinstance(env.get(key), str) or not env[key]]
    if not any(isinstance(env.get(key), str) and env[key] for key in ("DISPLAY", "WAYLAND_DISPLAY")):
        missing.append("DISPLAY_or_WAYLAND_DISPLAY")
    return missing


def _starttime(process):
    # The command can contain spaces and parentheses; fields after its final
    # ')' begin with field 3, and starttime is field 22.
    fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
    value = fields[19]
    if not value.isdecimal():
        raise ValueError("invalid process starttime")
    return value


def _read_session_environment(process):
    with (process / "environ").open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("environment exceeds read bound")
    result = {}
    for item in data.split(b"\0"):
        key, separator, value = item.partition(b"=")
        # Decode only whitelisted values; everything else is immediately lost.
        if separator and key.decode("ascii", errors="ignore") in _FIELDS:
            name = key.decode("ascii")
            result[name] = value.decode("utf-8", errors="strict")
    return result


def resolve_desktop_environment(environ=None, proc_root="/proc", uid=None):
    """Return (copied effective environment, non-secret diagnostic metadata).

    A complete inherited context is authoritative and does not scan /proc.
    Recovery requires exactly one verified, same-UID gnome-shell process.
    Conflicting supplied fields are refused instead of silently overwritten.
    """
    original = dict(os.environ if environ is None else environ)
    missing = _missing(original)
    if not missing:
        return original, {"source": "inherited", "missing_fields": [], "filled_fields": []}
    wanted_uid = os.getuid() if uid is None else uid
    root = Path(proc_root)
    candidates = []
    try:
        entries = list(root.iterdir())
    except OSError:
        raise RuntimeError("desktop_environment_proc_unreadable") from None
    for process in entries:
        if not process.name.isdecimal():
            continue
        try:
            if process.stat().st_uid != wanted_uid:
                continue
            executable = os.readlink(process / "exe")
        except FileNotFoundError:
            # A disappearing unrelated process is normal. A known shell whose
            # executable cannot be verified is not an acceptable candidate.
            try:
                known_shell = (process / "comm").read_text().strip() == "gnome-shell"
            except OSError:
                known_shell = False
            if known_shell:
                raise RuntimeError("desktop_environment_candidate_unreadable") from None
            continue
        except OSError:
            try:
                known_shell = (process / "comm").read_text().strip() == "gnome-shell"
            except OSError:
                known_shell = False
            if known_shell:
                raise RuntimeError("desktop_environment_candidate_unreadable") from None
            continue
        if Path(executable).name == "gnome-shell":
            candidates.append((process, executable))
    if not candidates:
        raise RuntimeError("desktop_environment_no_graphical_session")
    if len(candidates) != 1:
        raise RuntimeError("desktop_environment_multiple_graphical_sessions")
    process, executable = candidates[0]
    try:
        before = _starttime(process)
        recovered = _read_session_environment(process)
        after = _starttime(process)
        same_identity = (before == after and process.stat().st_uid == wanted_uid
                         and os.readlink(process / "exe") == executable)
    except (OSError, ValueError, IndexError, UnicodeError):
        raise RuntimeError("desktop_environment_candidate_unreadable") from None
    if not same_identity:
        raise RuntimeError("desktop_environment_candidate_changed")
    conflicts = sorted(key for key in _FIELDS
                       if original.get(key) and original[key] != recovered.get(key))
    if conflicts:
        raise RuntimeError("desktop_environment_conflicting_fields:" + ",".join(conflicts))
    effective = original.copy()
    filled = []
    for key in sorted(_FIELDS):
        if not effective.get(key) and recovered.get(key):
            effective[key] = recovered[key]
            filled.append(key)
    unresolved = _missing(effective)
    if unresolved:
        raise RuntimeError("desktop_environment_missing_fields:" + ",".join(unresolved))
    return effective, {"source": "same_uid_graphical_session", "source_pid": int(process.name),
                       "candidate_count": 1, "missing_fields": missing, "filled_fields": filled}
