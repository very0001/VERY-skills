"""CLI rebuilt from the reviewed implementation; see docs/recovery-portal.md."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from .perception import EventGate, GateConfig
from .storage import EventStore
from .watch import SourceCancelled


def _close_source_resources(source, resources):
    error = None
    for resource in ([source] if source is not None else []) + list(reversed(resources)):
        try:
            resource.close()
        except Exception as exc:  # noqa: BLE001 - close every independent resource before re-raising the first error.
            if error is None:
                error = exc
            else:
                error.add_note(f'additional resource cleanup failure: {exc}')
    if error is not None:
        raise error


class OwnedSource:
    def __init__(self, source, resources=()):
        self.source, self.resources = source, resources
        self.live = getattr(source, 'live', False)

    def next_frame(self):
        return self.source.next_frame()

    def set_max_fps(self, fps):
        if hasattr(self.source, 'set_max_fps'):
            self.source.set_max_fps(fps)

    def request_stop(self):
        if hasattr(self.source, 'request_stop'):
            self.source.request_stop()

    def close(self):
        _close_source_resources(self.source, self.resources)


def _check_source_cancelled(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise SourceCancelled('capture startup was stopped')


def _start_portal(portal, cancel_event):
    if cancel_event is None:
        return portal.start()
    from .portal import PortalClosed
    _check_source_cancelled(cancel_event)
    finished = threading.Event()
    close_errors = []

    def cancel_pending():
        while not finished.wait(.02):
            if cancel_event.is_set():
                try:
                    portal.close()
                except Exception as exc:  # noqa: BLE001 - forward helper-thread cleanup failures to the caller.
                    close_errors.append(exc)
                return

    monitor = threading.Thread(target=cancel_pending, name='semantic-cu-portal-cancel', daemon=True)
    monitor.start()
    startup_error = None
    try:
        try:
            result = portal.start()
        except PortalClosed as exc:
            if cancel_event.is_set():
                raise SourceCancelled('portal consent was stopped') from exc
            raise
    except BaseException as exc:
        startup_error = exc
        raise
    finally:
        finished.set()
        monitor.join()
        if close_errors:
            if startup_error is None:
                raise close_errors[0]
            startup_error.add_note(f'portal cancellation cleanup also failed: {close_errors[0]}')
    _check_source_cancelled(cancel_event)
    return result


def make_source(args, cancel_event=None):
    from .capture import (
        DirectoryReplaySource,
        PipeWireCaptureSource,
        VideoFileSource,
        X11CaptureSource,
    )
    resources, events, source = [], None, None
    try:
        _check_source_cancelled(cancel_event)
        if args.atspi:
            if args.source not in ('x11', 'pipewire', 'portal'):
                raise ValueError('--atspi needs a live capture source')
            from .accessibility import ATSPIMonitor
            events = ATSPIMonitor().start()
            resources.append(events)
        if args.source == 'directory':
            source = DirectoryReplaySource(args.input, fps=args.fps, realtime=not args.fast)
        elif args.source == 'video':
            source = VideoFileSource(args.input, max_fps=args.fps, realtime=not args.fast)
        elif args.source == 'x11':
            source = X11CaptureSource(max_fps=args.fps, events=events)
        elif args.source == 'pipewire':
            source = PipeWireCaptureSource(fd=args.pipewire_fd, node_id=args.pipewire_node,
                                           max_fps=args.fps, events=events)
        elif args.source == 'portal':
            from .portal import PortalSession
            portal = PortalSession()
            resources.append(portal)
            fd, node_id = _start_portal(portal, cancel_event)
            source = PipeWireCaptureSource(fd=fd, node_id=node_id, max_fps=args.fps, events=events)
        else:
            raise ValueError('no capture source configured')
        _check_source_cancelled(cancel_event)
        return OwnedSource(source, resources)
    except BaseException as exc:
        try:
            _close_source_resources(source, resources)
        except Exception as cleanup_error:  # noqa: BLE001 - preserve the original startup failure and report cleanup too.
            exc.add_note(f'capture startup cleanup also failed: {cleanup_error}')
        raise


def add_capture(parser):
    parser.add_argument('--source', choices=['none', 'directory', 'video', 'x11', 'pipewire', 'portal'], default='directory')
    parser.add_argument('--input', type=Path)
    parser.add_argument('--atspi', action='store_true', help='explicitly attach existing accessibility event bus; no settings changes')
    parser.add_argument('--fps', type=float, default=5)
    parser.add_argument('--fast', action='store_true', help='unpaced replay; never valid for live sources')
    parser.add_argument('--pipewire-fd', type=int)
    parser.add_argument('--pipewire-node', type=int)
    parser.add_argument('--mask', action='append', default=[], help='normalized left,top,right,bottom; masks capture BEFORE persistence')


def config_for(args):
    return GateConfig(masks=tuple(tuple(float(x) for x in value.split(',')) for value in args.mask))


def parser():
    root = argparse.ArgumentParser(description='Local-first semantic desktop perception for computer-use-linux')
    sub = root.add_subparsers(dest='command', required=True)
    watch = sub.add_parser('watch')
    add_capture(watch)
    watch.add_argument('--duration', type=float, default=60, help='cooperative runtime budget seconds (max 7h); native capture calls cannot be forcibly interrupted')
    watch.add_argument('--output', type=Path, help='optional private event recordings, disabled by default')
    watch.add_argument('--max-events', type=int, default=100)
    watch.add_argument('--max-bytes', type=int, default=268435456)
    mcp = sub.add_parser('mcp')
    add_capture(mcp)
    mcp.set_defaults(source='none')
    mcp.add_argument('--backend', required=True, help='explicit executable path for computer-use-linux')
    mcp.add_argument('--allow-actions', action='store_true', help='host remains responsible for user consent')
    doctor = sub.add_parser('doctor')
    doctor.add_argument('--backend')
    return root


def _watch_command(args, config):
    """Synchronous event delivery; the deadline includes source construction."""
    gate = EventGate(config)
    store = EventStore(args.output, args.max_events, args.max_bytes) if args.output else None
    cancel_event, source_lock = threading.Event(), threading.Lock()
    source, stop_errors, primary_error = None, [], None
    reason, exit_code = 'deadline', 0
    deadline = time.monotonic() + args.duration

    def emit(event):
        if event is not None:
            item = event.metadata()
            if store is not None:
                item['stored_at'] = str(store.save(event))
            print(json.dumps(item, ensure_ascii=False), flush=True)

    def request_stop():
        cancel_event.set()
        with source_lock:
            current = source
        if current is not None and hasattr(current, 'request_stop'):
            try:
                current.request_stop()
            except Exception as exc:  # noqa: BLE001 - propagate timer-thread stop failures after cleanup.
                stop_errors.append(exc)

    timer = threading.Timer(args.duration, request_stop)
    timer.name, timer.daemon = 'semantic-cu-cli-deadline', True
    timer.start()
    try:
        try:
            created = make_source(args, cancel_event=cancel_event)
            with source_lock:
                source = created
            if cancel_event.is_set():
                request_stop()
            while not cancel_event.is_set() and time.monotonic() < deadline:
                frame = source.next_frame()
                if frame is None:
                    reason = 'deadline' if cancel_event.is_set() or time.monotonic() >= deadline else 'end_of_stream'
                    break
                emit(gate.process(frame.image, frame.timestamp, frame.semantic_events))
                if source.live:
                    source.set_max_fps(gate.recommended_fps)
        except SourceCancelled as exc:
            if not cancel_event.is_set() or getattr(exc, '__notes__', None):
                raise
            reason = 'deadline'
        except KeyboardInterrupt:
            request_stop()
            reason, exit_code = 'interrupted', 130
        emit(gate.flush(reason))
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        timer.cancel()
        timer.join()
        try:
            if source is not None:
                source.close()
        except Exception as exc:
            if primary_error is None:
                primary_error = exc
                raise
            primary_error.add_note(f'capture cleanup also failed: {exc}')
        finally:
            if stop_errors:
                if primary_error is None:
                    raise stop_errors[0]
                for error in stop_errors:
                    primary_error.add_note(f'capture stop also failed: {error}')
    print(json.dumps({'summary': gate.stats(), 'reason': reason}, ensure_ascii=False), flush=True)
    return exit_code


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'doctor':
            data = {'display': bool(os.getenv('DISPLAY')), 'wayland': bool(os.getenv('WAYLAND_DISPLAY')),
                    'session_bus': bool(os.getenv('DBUS_SESSION_BUS_ADDRESS')), 'python': sys.version.split()[0]}
            if args.backend:
                from .backend import StdioMCPBackend
                with StdioMCPBackend(args.backend) as backend:
                    data['upstream'] = backend.call_tool('doctor', {})
            print(json.dumps(data, ensure_ascii=False, indent=2))
            return 0
        if args.source in ('directory', 'video') and args.input is None:
            raise ValueError('--input is required for replay')
        if args.fast and args.source in ('x11', 'pipewire', 'portal'):
            raise ValueError('--fast is replay only')
        config = config_for(args)
        if args.command == 'mcp':
            from .backend import StdioMCPBackend
            from .mcp_server import Gateway, serve
            from .desktop_env import resolve_desktop_environment
            environment_error = None
            try:
                effective, _ = resolve_desktop_environment()
                os.environ.update(effective)
            except RuntimeError as exc:
                # Protocol discovery/read-only diagnosis still works. Never
                # silently skip the shared-input lock because Host lost XDG.
                environment_error = str(exc)
            backend = StdioMCPBackend(args.backend)
            factory = None if args.source == 'none' else lambda cancel_event=None: make_source(args, cancel_event=cancel_event)
            gateway = Gateway(backend, source_factory=factory, config=config, allow_actions=args.allow_actions)
            gateway.environment_error = environment_error
            serve(gateway)
            return 0
        if not 0 < args.duration <= 25200:
            raise ValueError('--duration must be >0 and <=25200 seconds')
        return _watch_command(args, config)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # noqa: BLE001 - CLI boundary converts all application failures to stderr/exit 1.
        print(f'semantic-cu: {exc}', file=sys.stderr)
        for note in getattr(exc, '__notes__', ()):
            print(f'semantic-cu: {note}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
