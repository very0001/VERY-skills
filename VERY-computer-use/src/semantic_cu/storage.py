"""Optional bounded local recording. Disabled unless an output path is supplied.

One writer should own a store. Pruning recognizes only this format's exact
names and marker, and never recurses into directories or follows links. This
is conservative retention, not protection against a hostile local user who
already has permission to replace files in the store.
"""

from __future__ import annotations

import json
import re
import stat
import threading
import uuid
from pathlib import Path

from .perception import PerceptionEvent

_EVENT_NAME = re.compile(r"event-[0-9]{8,}-[0-9a-f]{32}\Z")
_FRAME_NAME = re.compile(r"[0-9]{2,}-[a-z][a-z0-9_-]{0,31}\.png\Z")
_ROLE = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_MARKER_NAME = ".semantic-cu-owned"
_MARKER = b"semantic-cu-event-v1\n"


class _BudgetWriter:
    def __init__(self, stream, remaining):
        self.stream = stream
        self.remaining = remaining
        self.written = 0

    def write(self, data):
        if len(data) > self.remaining:
            raise ValueError("event exceeds store byte budget")
        count = self.stream.write(data)
        self.remaining -= count
        self.written += count
        return count

    def flush(self):
        self.stream.flush()


def _new_file(path):
    import os

    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(fd, "wb")


class EventStore:
    def __init__(self, path, max_events=100, max_bytes=256 * 1024 * 1024):
        if (
            type(max_events) is not int
            or not 1 <= max_events <= 100000
            or type(max_bytes) is not int
            or max_bytes < 1024
        ):
            raise ValueError("invalid retention bounds")
        self.path = Path(path).expanduser()
        if self.path.is_symlink():
            raise ValueError("store must not be a symlink")
        self.path.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_events = max_events
        self.max_bytes = max_bytes
        self._lock = threading.RLock()

    def _check_root(self):
        if self.path.is_symlink() or not self.path.is_dir():
            raise ValueError("store must remain a regular directory")

    def save(self, event: PerceptionEvent):
        if type(event.event_id) is not int or event.event_id < 0:
            raise ValueError("event_id must be a nonnegative integer")
        metadata = event.metadata()
        names = []
        for index, keyframe in enumerate(event.keyframes):
            if not isinstance(keyframe.role, str) or not _ROLE.fullmatch(keyframe.role):
                raise ValueError("invalid keyframe role")
            name = f"{index:02d}-{keyframe.role}.png"
            names.append(name)
            metadata["keyframes"][index]["file"] = name
        raw = json.dumps(
            metadata, ensure_ascii=False, allow_nan=False, indent=2
        ).encode("utf-8")
        remaining = self.max_bytes - len(raw) - len(_MARKER)
        if remaining < 0:
            raise ValueError("event exceeds store byte budget")
        with self._lock:
            self._check_root()
            folder = self.path / f"event-{event.event_id:08d}-{uuid.uuid4().hex}"
            folder.mkdir(mode=0o700)
            created = []
            try:
                for keyframe, name in zip(event.keyframes, names):
                    with _new_file(folder / name) as handle:
                        created.append(folder / name)
                        writer = _BudgetWriter(handle, remaining)
                        keyframe.image.save(writer, format="PNG")
                        remaining -= writer.written
                with _new_file(folder / "event.json") as handle:
                    created.append(folder / "event.json")
                    handle.write(raw)
                with _new_file(folder / _MARKER_NAME) as handle:
                    created.append(folder / _MARKER_NAME)
                    handle.write(_MARKER)
                self.prune()
                if not folder.is_dir():
                    raise RuntimeError("new event was not retained")
                return folder
            except BaseException as exc:
                # Cleanup must not replace the original encoder/retention error.
                for child in created:
                    try:
                        child.unlink(missing_ok=True)
                    except OSError as cleanup_error:
                        exc.add_note(f"event cleanup also failed: {cleanup_error}")
                try:
                    folder.rmdir()
                except FileNotFoundError:
                    pass
                except OSError as cleanup_error:
                    exc.add_note(
                        f"event directory cleanup also failed: {cleanup_error}"
                    )
                raise

    @staticmethod
    def _owned_files(folder):
        if not _EVENT_NAME.fullmatch(folder.name):
            return None
        try:
            if not stat.S_ISDIR(folder.lstat().st_mode):
                return None
            children = list(folder.iterdir())
            names = {child.name for child in children}
            if not {_MARKER_NAME, "event.json"} <= names:
                return None
            size = 0
            for child in children:
                if child.name not in {
                    _MARKER_NAME,
                    "event.json",
                } and not _FRAME_NAME.fullmatch(child.name):
                    return None
                info = child.lstat()
                if not stat.S_ISREG(info.st_mode):
                    return None
                size += info.st_size
            marker = folder / _MARKER_NAME
            if marker.stat().st_size != len(_MARKER) or marker.read_bytes() != _MARKER:
                return None
            return children, size
        except FileNotFoundError:
            return None

    def prune(self):
        with self._lock:
            self._check_root()
            owned = []
            for folder in self.path.iterdir():
                inspected = self._owned_files(folder)
                if inspected is not None:
                    children, size = inspected
                    owned.append((folder.stat().st_mtime_ns, folder, children, size))
            owned.sort(key=lambda item: (item[0], item[1].name))
            total = sum(item[3] for item in owned)
            while owned and (len(owned) > self.max_events or total > self.max_bytes):
                _, folder, children, size = owned.pop(0)
                for child in children:
                    child.unlink()
                folder.rmdir()
                total -= size
