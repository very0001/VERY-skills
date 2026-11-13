"""Cooperative, bounded local observation; rebuilt from reviewed task context.

Factories may accept ``cancel_event=``. Legacy no-argument factories remain
supported, but arbitrary native or legacy blocking calls cannot be preempted.
"""
from __future__ import annotations

import inspect
import math
import threading
import time
from collections import deque

from .perception import EventGate


class SourceCancelled(RuntimeError):
    """A cooperative source factory stopped before producing a source."""


def _is_critical_event(event):
    # The first frame may carry critical hints with the generic 'semantic'
    # reason. Queue priority must preserve the hints, not just that reason.
    return event.reason == 'critical_semantic' or any(
        hint.get('priority') == 'critical'
        for hint in getattr(event, 'semantic_events', ())
    )


class WatchService:
    def __init__(self, source_factory, config=None, queue_size=8):
        if type(queue_size) is not int or not 1 <= queue_size <= 128:
            raise ValueError('queue_size must be an integer in 1..128')
        self.source_factory = source_factory
        self.config = config
        self.queue_size = queue_size
        self.gate = EventGate(config)
        self.queue = deque()
        self.lock = threading.RLock()
        self.stop_event = threading.Event()
        self.thread = None
        self.source = None
        self.error = None
        self.reason = None
        self.finished = False
        self.dropped_events = 0
        self._stop_reason = None

    def start(self, duration=3600):
        if (isinstance(duration, bool) or not isinstance(duration, (int, float))
                or not math.isfinite(duration) or not 0 < duration <= 25200):
            raise ValueError('duration must be >0 and <=25200 seconds')
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise RuntimeError('already watching')
            self.queue.clear()
            self.gate = EventGate(self.config)
            self.stop_event.clear()
            self.error = self.reason = self._stop_reason = None
            self.finished = False
            self.dropped_events = 0
            self.source = None
            self.thread = threading.Thread(target=self._run, args=(duration,),
                                           name='semantic-cu-watch', daemon=True)
            self.thread.start()
        return self.status()

    def _record_error(self, message):
        with self.lock:
            self.error = f'{self.error}; {message}' if self.error else message

    def _put(self, event):
        if event is None:
            return
        with self.lock:
            if len(self.queue) >= self.queue_size:
                index = next((i for i, old in enumerate(self.queue)
                              if not _is_critical_event(old)), None)
                if index is None:
                    if not _is_critical_event(event):
                        self.dropped_events += 1
                        return
                    index = 0
                del self.queue[index]
                self.dropped_events += 1
            self.queue.append(event)

    def _make_source(self):
        if self.stop_event.is_set():
            raise SourceCancelled('capture startup was stopped')
        # Never retry after catching a factory's own TypeError: it may already
        # have created resources or displayed a consent dialog.
        try:
            inspect.signature(self.source_factory).bind(cancel_event=self.stop_event)
        except (TypeError, ValueError):
            return self.source_factory()
        return self.source_factory(cancel_event=self.stop_event)

    def _request_stop(self, reason):
        with self.lock:
            if self._stop_reason is None:
                self._stop_reason = reason
            self.stop_event.set()
            source = self.source
        if source is not None and hasattr(source, 'request_stop'):
            try:
                source.request_stop()
            except Exception as exc:  # noqa: BLE001 - report arbitrary source stop failures without skipping cleanup.
                self._record_error(f'request_stop failed: {type(exc).__name__}: {exc}')

    def _run(self, duration):
        source = None
        deadline = time.monotonic() + duration
        timer = threading.Timer(duration, self._request_stop, args=('deadline',))
        timer.name = 'semantic-cu-watch-deadline'
        timer.daemon = True
        timer.start()
        try:
            source = self._make_source()
            with self.lock:
                self.source = source
            while not self.stop_event.is_set() and time.monotonic() < deadline:
                frame = source.next_frame()
                if self.stop_event.is_set():
                    break
                if frame is None:
                    self.reason = 'end_of_stream'
                    break
                with self.lock:
                    event = self.gate.process(frame.image, frame.timestamp, frame.semantic_events)
                self._put(event)
                if getattr(source, 'live', False) and hasattr(source, 'set_max_fps'):
                    source.set_max_fps(self.gate.recommended_fps)
            if self.reason is None:
                self.reason = self._stop_reason or 'deadline'
            with self.lock:
                event = self.gate.flush(self.reason)
            self._put(event)
        except SourceCancelled as exc:
            if self.stop_event.is_set():
                self.reason = self._stop_reason or 'stopped'
                for note in getattr(exc, '__notes__', ()):
                    self._record_error(note)
            else:
                self._record_error(f'{type(exc).__name__}: {exc}')
                self.reason = 'error'
        except Exception as exc:  # noqa: BLE001 - source worker exceptions must remain observable via status.
            self._record_error(f'{type(exc).__name__}: {exc}')
            self.reason = 'error'
        finally:
            timer.cancel()
            timer.join()
            if source is not None:
                try:
                    source.close()
                except Exception as exc:  # noqa: BLE001 - resource cleanup failure is reported without losing finished status.
                    self._record_error(f'close failed: {exc}')
            with self.lock:
                self.source = None
                self.finished = True

    def next_event(self):
        with self.lock:
            return self.queue.popleft() if self.queue else None

    def status(self):
        with self.lock:
            return {'running': bool(self.thread is not None and self.thread.is_alive()),
                    'finished': self.finished, 'queued_events': len(self.queue),
                    'dropped_events': self.dropped_events, 'reason': self.reason,
                    'error': self.error, 'perception': self.gate.stats()}

    def stop(self, timeout=5):
        self._request_stop('stopped')
        thread = self.thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        status = self.status()
        if status['running']:
            status['warning'] = 'capture is still unwinding; do not start a second watcher'
        return status
