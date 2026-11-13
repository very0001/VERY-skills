"""Read-only frame sources. Replay timestamps use recording seconds; live uses
monotonic seconds. Optional platform dependencies load only when selected.

Explicit semantic hints: start ATSPIMonitor from semantic_cu.accessibility,
pass events=monitor to a live source, and close the monitor separately. Sources
never enable accessibility or request screen permission themselves.

PipeWire accepts only an already-authorized FD in THIS process and its granted
node ID. Keep the caller's FD and portal session alive. Integer descriptor numbers
from another process require real FD transfer or inheritance, not copying digits.
"""

from __future__ import annotations

import importlib
import math
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageOps


class CaptureError(RuntimeError):
    """The source could not produce a usable frame."""


class CaptureUnavailable(CaptureError):
    """Missing dependency, display, or previously granted capability."""


class CaptureTimeout(CaptureError):
    """No live frame arrived within the bounded sample timeout."""


@dataclass
class Frame:
    image: Image.Image
    timestamp: float
    semantic_events: list[dict] = field(default_factory=list)


class EventSource(Protocol):
    def drain(self) -> list[dict]: ...


def _positive_finite(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    return value


class FrameRateLimiter:
    """First acquisition is immediate. Never burst to catch up after a stall."""

    def __init__(self, max_fps=12.0, *, clock=time.monotonic, sleep=time.sleep):
        self.set_max_fps(max_fps)
        self._clock = clock
        self._sleep = sleep
        self._last = None

    def set_max_fps(self, max_fps):
        self.max_fps = _positive_finite(max_fps, "max_fps")
        self.interval = 1.0 / self.max_fps

    def wait(self, cancelled: Callable[[], bool] | None = None) -> bool:
        if cancelled is not None and cancelled():
            return False
        now = self._clock()
        if self._last is not None:
            remaining = self._last + self.interval - now
            while remaining > 0:
                self._sleep(remaining)
                if cancelled is not None and cancelled():
                    return False
                now = self._clock()
                remaining = self._last + self.interval - now
        self._last = now
        return True


class CaptureSource(ABC):
    """One consumer thread. next_frame returns None at EOF/after stop or close.

    Event sources are borrowed: the caller starts/closes them. Events keep their
    original clock, so live AT-SPI timestamps are not remapped onto replay time.
    """

    live = False

    def __init__(
        self, *, max_fps=12.0, realtime=True, events: EventSource | None = None
    ):
        self._stop_event = threading.Event()
        self._limiter = FrameRateLimiter(max_fps, sleep=self._stop_event.wait)
        self._realtime = realtime
        self._events = events
        self._closed = False

    def _pace(self):
        if self._stop_event.is_set():
            return False
        if self._realtime:
            return self._limiter.wait(cancelled=self._stop_event.is_set)
        return True

    def _frame(self, image, timestamp):
        return Frame(
            image, timestamp, self._events.drain() if self._events is not None else []
        )

    @abstractmethod
    def next_frame(self) -> Frame | None:
        """Return an RGB frame or None at EOF/after stop."""

    def request_stop(self):
        """Interrupt pacing promptly; caller must still close in its finally.

        This does not forcibly interrupt a native call already running. Appsink
        waits have their configured timeout; X11 and decoder calls have no
        proven hard bound. Strict process termination needs process isolation.
        """
        self._stop_event.set()

    def close(self):
        self.request_stop()
        self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def __iter__(self):
        while (frame := self.next_frame()) is not None:
            yield frame


class LiveCaptureSource(CaptureSource):
    live = True

    @property
    def max_fps(self):
        return self._limiter.max_fps

    def set_max_fps(self, max_fps):
        """Change pace between acquisitions, e.g. 2 FPS idle / 10 FPS active.

        Retains previous acquisition time; a change does not cause a burst.
        Replay deliberately does not expose this API or alter its timebase.
        """
        if self._closed:
            raise CaptureError("Cannot change frame rate on a closed source")
        requested = _positive_finite(max_fps, "max_fps")
        if requested != self.max_fps:
            self._limiter.set_max_fps(requested)


def _natural_key(path):
    return [
        int(p) if p.isdigit() else p.casefold() for p in re.split(r"(\d+)", path.name)
    ]


class DirectoryReplaySource(CaptureSource):
    """Natural filename order, nonrecursive; accepts a single image too.

    fps defines recording time AND maximum real-time delivery rate.
    realtime=False explicitly disables pacing for offline analysis.
    """

    EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"})

    def __init__(self, path, *, fps=12.0, realtime=True, events=None):
        self.fps = _positive_finite(fps, "fps")
        super().__init__(max_fps=self.fps, realtime=realtime, events=events)
        path = Path(path)
        if path.is_dir():
            self.paths = sorted(
                (
                    p
                    for p in path.iterdir()
                    if p.is_file() and p.suffix.lower() in self.EXTENSIONS
                ),
                key=_natural_key,
            )
        elif path.is_file() and path.suffix.lower() in self.EXTENSIONS:
            self.paths = [path]
        else:
            raise CaptureUnavailable(f"No image file or replay directory at {path}")
        if not self.paths:
            raise CaptureUnavailable(f"No supported replay images in {path}")
        self._index = 0

    def next_frame(self):
        if self._closed or self._index >= len(self.paths):
            return None
        if not self._pace():
            return None
        path = self.paths[self._index]
        try:
            with Image.open(path) as original:
                image = ImageOps.exif_transpose(original).convert("RGB")
        except (OSError, ValueError) as exc:
            raise CaptureError(f"Cannot decode replay image {path}: {exc}") from exc
        timestamp = self._index / self.fps
        self._index += 1
        return self._frame(image, timestamp)


class VideoFileSource(CaptureSource):
    """Optional OpenCV, local file only, sample at no more than max_fps.

    Uses nominal FPS/CFR timestamps, not VFR PTS. Supply fps explicitly if the
    file has invalid/missing nominal FPS. Excess decoded frames are discarded.
    OpenCV may report decode failure as EOF; it cannot always distinguish them.
    """

    def __init__(self, path, *, max_fps=12.0, fps=None, realtime=True, events=None):
        max_fps = _positive_finite(max_fps, "max_fps")
        if fps is not None:
            fps = _positive_finite(fps, "fps")
        path = Path(path)
        if not path.is_file():
            raise CaptureUnavailable(f"Video file does not exist: {path}")
        try:
            self._cv2 = importlib.import_module("cv2")
        except ImportError as exc:
            raise CaptureUnavailable(
                "Video replay needs optional dependency opencv-python-headless"
            ) from exc
        self._capture = None
        try:
            self._capture = self._cv2.VideoCapture(str(path))
            if not self._capture.isOpened():
                raise CaptureUnavailable(f"OpenCV could not open video: {path}")
            self.fps = (
                fps
                if fps is not None
                else float(self._capture.get(self._cv2.CAP_PROP_FPS))
            )
            if not math.isfinite(self.fps) or self.fps <= 0:
                raise CaptureUnavailable(
                    "Video has no usable nominal FPS; provide fps explicitly"
                )
        except Exception as exc:
            if self._capture is not None:
                self._capture.release()
            if isinstance(exc, CaptureError):
                raise
            raise CaptureUnavailable(f"Cannot initialize video decoder: {exc}") from exc
        self.output_fps = min(max_fps, self.fps)
        super().__init__(max_fps=self.output_fps, realtime=realtime, events=events)
        self._decode_index = 0
        self._sample_index = 0

    def next_frame(self):
        if self._closed or not self._pace():
            return None
        target_time = self._sample_index / self.output_fps
        while True:
            try:
                success, array = self._capture.read()
            except Exception as exc:
                self.close()
                raise CaptureError(f"Video decoding failed: {exc}") from exc
            if not success:
                self.close()
                return None
            timestamp = self._decode_index / self.fps
            self._decode_index += 1
            if timestamp + 1e-9 >= target_time:
                break
        self._sample_index += 1
        try:
            rgb = self._cv2.cvtColor(array, self._cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb).convert("RGB")
        except Exception as exc:
            raise CaptureError(f"Cannot convert decoded video frame: {exc}") from exc
        return self._frame(image, timestamp)

    def close(self):
        if not self._closed:
            self._capture.release()
        super().close()


class X11CaptureSource(LiveCaptureSource):
    """Pillow X11 capture with no screenshot-command/permission fallbacks.

    Declared Wayland sessions reject Xwayland by default because it may omit
    native Wayland windows. allow_xwayland=True acknowledges this limitation.
    """

    def __init__(
        self,
        *,
        display=None,
        max_fps=12.0,
        bbox=None,
        allow_xwayland=False,
        events=None,
    ):
        super().__init__(max_fps=max_fps, events=events)
        self.display = display or os.environ.get("DISPLAY")
        if not self.display:
            raise CaptureUnavailable(
                "No X11 DISPLAY is available; use replay or an authorized PipeWire source"
            )
        wayland = os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(
            os.environ.get("WAYLAND_DISPLAY")
        )
        if wayland and not allow_xwayland:
            raise CaptureUnavailable(
                "Wayland session detected: Xwayland may omit native windows; use authorized PipeWire"
            )
        if bbox is not None and (
            len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]
        ):
            raise ValueError("bbox must have positive dimensions")
        self.bbox = bbox
        self._image_grab = importlib.import_module("PIL.ImageGrab")

    def next_frame(self):
        if self._closed or not self._pace():
            return None
        try:
            image = self._image_grab.grab(bbox=self.bbox, xdisplay=self.display)
        except Exception as exc:
            raise CaptureUnavailable(
                f"Cannot capture X11 display {self.display}: {exc}"
            ) from exc
        return self._frame(image.convert("RGB"), time.monotonic())


def _pipewire_rate_caps(max_fps):
    rate = Fraction(_positive_finite(max_fps, "max_fps")).limit_denominator(1000)
    if rate.numerator == 0:
        raise ValueError("max_fps is too small for GStreamer (minimum 0.001)")
    return f"video/x-raw,framerate={rate.numerator}/{rate.denominator}"


def build_pipewire_pipeline(max_fps=12.0):
    caps = _pipewire_rate_caps(max_fps)
    return (
        "pipewiresrc name=source do-timestamp=true ! "
        "queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
        "videorate drop-only=true ! "
        f"capsfilter name=rate caps={caps} ! "
        "videoconvert ! video/x-raw,format=RGB ! "
        "appsink name=sink emit-signals=false max-buffers=1 drop=true sync=false"
    )


class PipeWireCaptureSource(LiveCaptureSource):
    """Consume an already-authorized stream; never open a screen-share portal.

    Requires GI Gst/GstVideo 1.0 and pipewiresrc/videorate/videoconvert/appsink.
    Duplicates the caller FD and owns only that duplicate. Keepalives, when
    supported, repeat the last real buffer on static desktops, not new renders.
    Disconnect errors are enabled if the plugin supports on-disconnect.
    """

    def __init__(
        self, fd: int, node_id: int, *, max_fps=12.0, timeout=2.0, events=None
    ):
        super().__init__(max_fps=max_fps, events=events)
        self.timeout = _positive_finite(timeout, "timeout")
        if isinstance(fd, bool) or not isinstance(fd, int) or fd < 0:
            raise ValueError("fd must be an open descriptor in this process")
        if isinstance(node_id, bool) or not isinstance(node_id, int) or node_id < 0:
            raise ValueError(
                "node_id must be a nonnegative integer from the authorized stream"
            )
        try:
            os.fstat(fd)
        except OSError as exc:
            raise CaptureUnavailable("PipeWire FD is not open in this process") from exc
        try:
            gi = importlib.import_module("gi")
            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
            self._gst = importlib.import_module("gi.repository.Gst")
            self._gst_video = importlib.import_module("gi.repository.GstVideo")
        except (ImportError, ValueError) as exc:
            raise CaptureUnavailable(
                "PipeWire capture needs PyGObject with Gst and GstVideo 1.0"
            ) from exc
        self._pipeline = None
        self._fd = None
        try:
            self._gst.init(None)
            self._fd = os.dup(fd)
            self._pipeline = self._gst.parse_launch(build_pipewire_pipeline(max_fps))
            source = self._pipeline.get_by_name("source")
            source.set_property("fd", self._fd)
            source.set_property("path", str(node_id))
            self._source_element = source
            self.keepalive_supported = (
                source.find_property("keepalive-time") is not None
            )
            self.disconnect_errors_supported = (
                source.find_property("on-disconnect") is not None
            )
            if self.keepalive_supported:
                source.set_property("keepalive-time", self._keepalive_ms(max_fps))
            if self.disconnect_errors_supported:
                # Public GstPipeWireSrcOnDisconnect: NONE=0, EOS=1, ERROR=2.
                source.set_property("on-disconnect", 2)
            self._sink = self._pipeline.get_by_name("sink")
            self._rate = self._pipeline.get_by_name("rate")
            if (
                self._pipeline.set_state(self._gst.State.PLAYING)
                == self._gst.StateChangeReturn.FAILURE
            ):
                raise CaptureUnavailable(
                    "GStreamer rejected the authorized PipeWire stream"
                )
        except Exception as exc:
            self.close()
            if isinstance(exc, CaptureError):
                raise
            raise CaptureUnavailable(f"Cannot start PipeWire pipeline: {exc}") from exc

    @staticmethod
    def _keepalive_ms(max_fps):
        return max(1, min(500, math.ceil(1000.0 / max_fps)))

    def set_max_fps(self, max_fps):
        if self._closed:
            raise CaptureError("Cannot change frame rate on a closed source")
        requested = _positive_finite(max_fps, "max_fps")
        if requested == self.max_fps:
            return
        caps_text = _pipewire_rate_caps(requested)
        try:
            self._rate.set_property("caps", self._gst.Caps.from_string(caps_text))
            if self.keepalive_supported:
                self._source_element.set_property(
                    "keepalive-time", self._keepalive_ms(requested)
                )
        except Exception as exc:
            raise CaptureError(
                f"Cannot renegotiate PipeWire frame rate: {exc}"
            ) from exc
        super().set_max_fps(requested)

    def _stream_status(self):
        message = self._pipeline.get_bus().pop_filtered(
            self._gst.MessageType.ERROR | self._gst.MessageType.EOS
        )
        if message is None:
            return None
        if message.type == self._gst.MessageType.ERROR:
            error, _debug = message.parse_error()
            raise CaptureError(f"PipeWire/GStreamer stream failed: {error.message}")
        return "eof"

    def next_frame(self):
        if self._closed or not self._pace():
            return None
        sample = self._sink.emit(
            "try-pull-sample", int(self.timeout * self._gst.SECOND)
        )
        if sample is None:
            if self._stream_status() == "eof" or self._sink.get_property("eos"):
                self.close()
                return None
            raise CaptureTimeout(
                f"No PipeWire frame within {self.timeout:g}s; verify the live portal session and static-frame keepalive support"
            )
        info = self._gst_video.VideoInfo.new_from_caps(sample.get_caps())
        buffer = sample.get_buffer()
        mapped, data = buffer.map(self._gst.MapFlags.READ)
        if not mapped:
            raise CaptureError("Could not map PipeWire frame buffer")
        try:
            meta = self._gst_video.buffer_get_video_meta(buffer)
            stride = int(meta.stride[0] if meta is not None else info.stride[0])
            offset = int(meta.offset[0] if meta is not None else info.offset[0])
            width, height = int(info.width), int(info.height)
            raw = bytes(data.data)
            required = (height - 1) * stride + width * 3
            if (
                width <= 0
                or height <= 0
                or stride < width * 3
                or offset < 0
                or len(raw) - offset < required
            ):
                raise CaptureError("Invalid RGB dimensions/stride in PipeWire frame")
            pixels = b"".join(
                raw[offset + y * stride : offset + y * stride + width * 3]
                for y in range(height)
            )
            image = Image.frombytes("RGB", (width, height), pixels)
        finally:
            buffer.unmap(data)
        return self._frame(image, time.monotonic())

    def close(self):
        super().close()
        try:
            if self._pipeline is not None:
                self._pipeline.set_state(self._gst.State.NULL)
                self._pipeline = None
        finally:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
