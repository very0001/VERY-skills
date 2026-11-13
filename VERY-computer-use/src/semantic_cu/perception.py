"""Bounded rule-based frame gate; not a trained WalkVLM/EAD implementation.

Reconstructed from the development record after workspace interruption.
Timestamps are monotonic seconds in a single source's timebase.
"""

from __future__ import annotations

import hashlib
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image, ImageDraw


@dataclass(frozen=True)
class GateConfig:
    width: int = 320
    height: int = 180
    pixel_threshold: int = 18
    changed_ratio: float = 0.008
    stable_ratio: float = 0.003
    local_changed_ratio: float = 0.12
    min_changed_pixels: int = 24
    stable_frames: int = 3
    buffer_frames: int = 50
    max_event_seconds: float = 3.0
    min_event_seconds: float = 0.2
    cooldown_seconds: float = 0.5
    idle_fps: float = 2.0
    active_fps: float = 10.0
    max_keyframes: int = 4
    max_image_pixels: int = 16_777_216
    max_output_side: int = 1280
    masks: tuple[tuple[float, float, float, float], ...] = ()

    def __post_init__(self):
        limits = {
            "width": (1, 4096),
            "height": (1, 4096),
            "pixel_threshold": (1, 255),
            "min_changed_pixels": (1, self.width * self.height),
            "stable_frames": (1, 100),
            "buffer_frames": (2, 1000),
            "max_keyframes": (1, 4),
            "max_image_pixels": (1, 134_217_728),
            "max_output_side": (1, 8192),
        }
        for key, (low, high) in limits.items():
            value = getattr(self, key)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"invalid {key}")
        for key in (
            "changed_ratio",
            "stable_ratio",
            "local_changed_ratio",
            "max_event_seconds",
            "min_event_seconds",
            "cooldown_seconds",
            "idle_fps",
            "active_fps",
        ):
            value = getattr(self, key)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"invalid {key}")
        if not 0 <= self.stable_ratio <= self.changed_ratio <= 1:
            raise ValueError("invalid change ratios")
        if not 0 < self.local_changed_ratio <= 1:
            raise ValueError("invalid local ratio")
        if not 0 < self.min_event_seconds <= self.max_event_seconds:
            raise ValueError("invalid event durations")
        if self.cooldown_seconds < 0 or not 0 < self.idle_fps <= self.active_fps <= 120:
            raise ValueError("invalid rate/cooldown")
        for mask in self.masks:
            if (
                len(mask) != 4
                or not all(
                    isinstance(v, (int, float))
                    and not isinstance(v, bool)
                    and math.isfinite(v)
                    and 0 <= v <= 1
                    for v in mask
                )
                or mask[0] >= mask[2]
                or mask[1] >= mask[3]
            ):
                raise ValueError("mask must be normalized left,top,right,bottom")


@dataclass
class Snapshot:
    image: Image.Image
    timestamp: float
    digest: str
    gray: np.ndarray = field(repr=False)
    source_size: tuple[int, int]


@dataclass
class Keyframe:
    role: str
    image: Image.Image
    timestamp: float
    digest: str
    source_size: tuple[int, int]


@dataclass
class PerceptionEvent:
    event_id: int
    reason: str
    started_at: float
    ended_at: float
    peak_changed_ratio: float
    semantic_events: list[dict[str, Any]]
    keyframes: list[Keyframe]
    stable: bool

    def metadata(self):
        return {
            "event_id": self.event_id,
            "reason": self.reason,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "peak_changed_ratio": self.peak_changed_ratio,
            "semantic_events": self.semantic_events,
            "stable": self.stable,
            "keyframes": [
                {
                    "role": k.role,
                    "timestamp": k.timestamp,
                    "digest": k.digest,
                    "width": k.image.width,
                    "height": k.image.height,
                    "source_width": k.source_size[0],
                    "source_height": k.source_size[1],
                }
                for k in self.keyframes
            ],
        }


class EventGate:
    def __init__(self, config: GateConfig | None = None):
        self.config = config or GateConfig()
        self.buffer: deque[Snapshot] = deque(maxlen=self.config.buffer_frames)
        self.before = self.onset = self.peak = self.anchor = None
        self.peak_ratio = 0.0
        self.stable_count = 0
        self.events = []
        self.sequence = 0
        self.last_emit = -math.inf
        self.last_timestamp = -math.inf
        self.frames_seen = 0
        self.emitted_keyframes = 0
        self._semantic_seen = {}
        self._diversity_candidates = deque(maxlen=4)

    @property
    def active(self):
        return self.onset is not None

    @property
    def recommended_fps(self):
        return self.config.active_fps if self.active else self.config.idle_fps

    def _snapshot(self, image, timestamp):
        c = self.config
        if (
            image.width < 1
            or image.height < 1
            or image.width * image.height > c.max_image_pixels
        ):
            raise ValueError("capture exceeds image bounds")
        source_size = image.size
        image = image.convert("RGB").copy()
        draw = ImageDraw.Draw(image)
        for x1, y1, x2, y2 in c.masks:
            draw.rectangle(
                (
                    int(x1 * image.width),
                    int(y1 * image.height),
                    math.ceil(x2 * image.width) - 1,
                    math.ceil(y2 * image.height) - 1,
                ),
                fill="black",
            )
        image.thumbnail(
            (c.max_output_side, c.max_output_side), Image.Resampling.LANCZOS
        )
        gray = np.asarray(
            image.convert("L").resize((c.width, c.height), Image.Resampling.BILINEAR),
            dtype=np.int16,
        )
        digest = hashlib.sha256(
            repr((source_size, image.size)).encode() + image.tobytes()
        ).hexdigest()
        if self.buffer and self.buffer[-1].digest == digest:
            image = self.buffer[-1].image
            gray = self.buffer[-1].gray
        return Snapshot(image, timestamp, digest, gray, source_size)

    def _ratio(self, a, b):
        if a.source_size != b.source_size:
            return 1.0
        return float(np.mean(np.abs(a.gray - b.gray) >= self.config.pixel_threshold))

    def _changed(self, a, b):
        if a.source_size != b.source_size:
            return True
        mask = np.abs(a.gray - b.gray) >= self.config.pixel_threshold
        count = int(mask.sum())
        if count / mask.size >= self.config.changed_ratio:
            return True
        if count < self.config.min_changed_pixels:
            return False
        h, w = mask.shape
        integral = np.pad(mask.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        ys = np.arange(0, h, 8)
        xs = np.arange(0, w, 8)
        ye = np.minimum(ys + 16, h)
        xe = np.minimum(xs + 16, w)
        sums = (
            integral[ye[:, None], xe]
            - integral[ys[:, None], xe]
            - integral[ye[:, None], xs]
            + integral[ys[:, None], xs]
        )
        return bool(
            np.any(
                sums / ((ye - ys)[:, None] * (xe - xs))
                >= self.config.local_changed_ratio
            )
        )

    def _clean_events(self, events, timestamp):
        """Prepare bounded hints and dedup state without mutating the gate.

        Metadata conversion may raise (for example from an invalid container
        or a value's __str__). A rejected frame must not advance its timebase,
        replace retained images, or expire previously accepted hint entries.
        """
        if events is None:
            events = []
        elif not isinstance(events, (list, tuple)):
            raise ValueError("semantic_events must be a list or tuple")
        clean = []
        seen = {k: v for k, v in self._semantic_seen.items() if timestamp - v < 2.0}
        for e in events[:100]:
            # Preserve existing tolerance for ignored non-dictionary hints.
            if not isinstance(e, dict):
                continue
            item = {
                k: str(e[k])[:256]
                for k in ("type", "source", "role", "priority", "name")
                if k in e
            }
            if not item:
                continue
            key = repr(sorted(item.items()))
            if key not in seen:
                clean.append(item)
                seen[key] = timestamp
                while len(seen) > 1024:
                    seen.pop(next(iter(seen)))
        return clean, seen

    def process(self, image, timestamp, semantic_events=None):
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise TypeError("timestamps must be finite, numeric and monotonic")
        try:
            valid_time = math.isfinite(timestamp) and timestamp >= self.last_timestamp
        except OverflowError:
            valid_time = False
        if not valid_time:
            raise ValueError("timestamps must be finite, numeric and monotonic")
        # Finish all caller-input work before committing any state. Snapshot
        # construction is isolated, including masking and image conversions.
        current = self._snapshot(image, timestamp)
        events, seen = self._clean_events(semantic_events, timestamp)
        previous = self.buffer[-1] if self.buffer else None
        self.last_timestamp = timestamp
        self.frames_seen += 1
        self.buffer.append(current)
        self._semantic_seen = seen
        if previous is None:
            self.anchor = current
            if events:
                self._begin(current, current, events, 0)
                return self._emit(current, "semantic", False)
            return None
        ratio = self._ratio(previous, current)
        meaningful = (
            self._changed(previous, current)
            or (
                not self.active
                and self.anchor is not None
                and self._changed(self.anchor, current)
            )
            or bool(events)
        )
        if not self.active and meaningful:
            if timestamp - self.last_emit < self.config.cooldown_seconds and not events:
                return None
            before = self.anchor or previous
            self._begin(
                before, current, events, max(ratio, self._ratio(before, current))
            )
        elif self.active:
            if meaningful:
                self._remember_diverse(current)
            self.events.extend(events)
            self.events = self.events[-100:]
            displacement = self._ratio(self.before, current)
            if displacement > self.peak_ratio:
                self.peak_ratio, self.peak = displacement, current
            self.stable_count = (
                self.stable_count + 1
                if ratio <= self.config.stable_ratio
                and not self._changed(previous, current)
                else 0
            )
        if self.active:
            if any(e.get("priority") == "critical" for e in events):
                return self._emit(current, "critical_semantic", False)
            elapsed = timestamp - self.onset.timestamp
            if (
                elapsed >= self.config.min_event_seconds
                and self.stable_count >= self.config.stable_frames
            ):
                return self._emit(current, "stable_change", True)
            if elapsed >= self.config.max_event_seconds:
                return self._emit(current, "motion_timeout", False)
        return None

    def _begin(self, before, current, events, ratio):
        self.before, self.onset, self.peak = before, current, current
        self.peak_ratio = ratio
        self.stable_count = 0
        self.events = list(events)
        self._diversity_candidates.clear()
        self._remember_diverse(before)
        self._remember_diverse(current)

    def _remember_diverse(self, snapshot):
        """Keep at most four distinct meaningful states, without copying pixels.

        Repeated identical states do not evict a short-lived distinct state.
        Beyond the fixed budget, newer states replace older candidates. Original
        before/onset/peak references remain available as lower-priority context.
        """
        if not any(old.digest == snapshot.digest for old in self._diversity_candidates):
            self._diversity_candidates.append(snapshot)

    def flush(self, reason="stream_end"):
        return (
            self._emit(self.buffer[-1], reason, False)
            if self.active and self.buffer
            else None
        )

    def _emit(self, current, reason, stable):
        candidates = [
            ("before", self.before),
            ("onset", self.onset),
            ("peak", self.peak),
        ]
        roles = {
            snapshot.digest: role
            for role, snapshot in candidates
            if snapshot is not None
        }
        candidates.extend(
            (roles.get(snapshot.digest, "intermediate"), snapshot)
            for snapshot in self._diversity_candidates
        )
        candidates.append(("after", current))
        unique = []
        seen = set()
        for role, s in reversed(candidates):
            if s is not None and s.digest not in seen:
                unique.append(
                    Keyframe(role, s.image.copy(), s.timestamp, s.digest, s.source_size)
                )
                seen.add(s.digest)
        unique.reverse()
        unique = unique[-self.config.max_keyframes :]
        unique.sort(key=lambda keyframe: keyframe.timestamp)
        self.sequence += 1
        event = PerceptionEvent(
            self.sequence,
            reason,
            self.onset.timestamp,
            current.timestamp,
            self.peak_ratio,
            list(self.events),
            unique,
            stable,
        )
        self.emitted_keyframes += len(unique)
        self.last_emit = current.timestamp
        self.anchor = current
        self.before = self.onset = self.peak = None
        self._diversity_candidates.clear()
        self.events = []
        self.peak_ratio = 0.0
        self.stable_count = 0
        return event

    def stats(self):
        return {
            "frames_seen": self.frames_seen,
            "events_emitted": self.sequence,
            "keyframes_emitted": self.emitted_keyframes,
            "buffered_frames": len(self.buffer),
            "active": self.active,
            "recommended_fps": self.recommended_fps,
        }
