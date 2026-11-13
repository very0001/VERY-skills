# Wayland screen-sharing consent

This document and its implementation were reconstructed from the reviewed task
context after the workspace files were lost. They are not claimed to be
byte-identical to the earlier implementation. See [recovery notes](recovery-portal.md).

`semantic_cu.portal.PortalSession` is an **opt-in launcher**, not a permission
bypass. Importing or constructing it does not access the desktop. Only `start()`
connects to the session bus and asks the user's existing ScreenCast portal to
authorize one temporary stream. Initiate this only after an explicit user share
action. The desktop backend controls its picker and consent UI.

No root access, system security changes, compositor-private APIs, saved restore
tokens, automatic permission retries, or X11 fallback are used. Cancellation
and denial are errors, not authorization to switch to another capture method.

## Runtime requirements

- Run in the user's actual graphical desktop session and its session D-Bus.
- The desktop must provide `xdg-desktop-portal`, a compatible ScreenCast backend,
  and PipeWire. Backend availability depends on the desktop.
- Install optional `dbus-next` in the application Python environment. The code
  uses its asyncio low-level interface with Unix FD negotiation enabled.
- `PipeWireCaptureSource` additionally requires PyGObject with `Gst` and
  `GstVideo` 1.0 plus `pipewiresrc`, `videorate`, `videoconvert`, and `appsink`.

Installing a Python dependency does not create a desktop, portal backend, or
GStreamer installation. Do not copy another user's session-bus address, run as
root, or change security settings to bypass a denied connection. Headless
protocol tests cannot validate a real consent dialog or frame delivery.

## Same-process use

```python
from semantic_cu.capture import PipeWireCaptureSource
from semantic_cu.portal import PortalSession

# Run only in response to the user's explicit share action.
with PortalSession(source_types=1, timeout=120) as portal:
    fd, node_id = portal.start()
    with PipeWireCaptureSource(fd, node_id, max_fps=12) as source:
        while True:
            portal.check_alive()
            frame = source.next_frame()
            if frame is None:
                break
            # Process frame.image and frame.timestamp locally.
```

Close the capture source before the outer portal context. The portal owns the
returned FD and keeps its connection alive on a dedicated asyncio thread. The
capture adapter duplicates the FD and owns only its duplicate. FD numbers are
process-local: printing one and passing its number to a different command is
not an FD transfer.

`start()` is synchronous; a GUI should call it outside its UI thread. Repeating
it on an active instance returns the same pair without another consent flow.
A failed or closed instance cannot restart. Creating a new instance is for a
new explicit user request. Entering a context does not call `start()`.

`close()` is idempotent and can interrupt pending `start()` from another thread.
It releases the local FD, attempts `Request.Close` for any pending dialog and
`Session.Close`, then disconnects the dedicated bus connection. The portal
specification treats that client disconnection as closing its sessions. Portal
cleanup calls are bounded, and no helper thread is deliberately left running.

## Options and errors

- `source_types=1`: MONITOR. WINDOW is `2`; VIRTUAL is `4`. Masks may combine
  types only if the backend advertises every requested type. `multiple` is
  always false, so exactly one stream must be granted.
- `cursor_mode=None`: retain the portal's default, Hidden. Explicit values are
  Hidden `1`, Embedded `2`, or Metadata `4`, and must be advertised. This module
  does not interpret cursor metadata.
- `parent_window=""`: no parent. GUIs may supply a valid exported portal window
  identifier. A raw toolkit pointer is not a Wayland exported surface handle.
- `timeout=120`: positive finite overall startup timeout, including bus calls
  and user interaction. Expiration cancels the flow and never assumes consent.
- `fd`, `node_id`: borrowed active results, also returned by `start()`.
- `stream_properties`: metadata with D-Bus Variants converted to Python values.
  Optional size/position properties can use compositor rather than pixel units.

`PortalError` derives from `CaptureError`. `PortalUnavailable` also derives from
`CaptureUnavailable`; it covers missing dependency/bus/service or unsupported
capabilities. `PortalCancelled` means portal response code 1. `PortalTimeout`
means the startup budget expired. `PortalClosed` reports explicit close,
revocation, or bus disconnection. Other response codes, D-Bus denial, malformed
streams and bad FD replies remain explicit `PortalError` failures.

Call `check_alive()` while using the session to expose revocation. PipeWire may
also produce EOS, an error or a frame timeout after consent ends. Close its
pipeline and never silently select an alternative capture source.

## Runtime budgets and queue behavior

Standalone `semantic-cu watch --duration SECONDS` and MCP continuous watch use
**cooperative runtime budgets covering source startup and capture**. Expiration
while the portal picker is pending sets `cancel_event`, closes the pending
portal flow, and cleans up resources. After construction, the deadline invokes
an available `source.request_stop()` to interrupt cooperative pacing.

The standalone CLI processes frames and emits events synchronously. It does
not route offline replay through a bounded queue. Its final JSON contains
`reason`: `deadline`, `end_of_stream`, or `interrupted` (Ctrl-C exits 130).
Continuous watch distinguishes `deadline` from an explicit `stopped` request.
Timers and consent-cancellation observers are joined on completion or failure.

These are **not hard wall-clock cutoffs**. A native X11 or decoder call already
running cannot be forcibly interrupted by a Python event. GStreamer reads use
the configured frame timeout, and portal cleanup has separate bounded waits.
Those operations can finish after the requested runtime budget. Legacy custom
zero-argument factories still work, but blocking startup without support for
`cancel_event` cannot be cancelled cooperatively.

The continuous watcher bounds its event queue. If a full queue contains only
critical semantic events, it drops a new ordinary event instead of evicting a
critical one. A new critical event may replace the oldest critical item. Every
drop is counted. `queue_size` must be an integer, not bool, in the range 1–128.
`request_stop` and cleanup failures remain visible in status or CLI errors.
Even a source with false truth value is still stopped and closed.

## Protocol and compatibility

The flow is `CreateSession` → `SelectSources` → `Start` →
`OpenPipeWireRemote`. Response subscriptions exist before the first request.
The implementation accepts a response arriving before its method reply,
verifies the returned request path, and pins signals to the service's unique
D-Bus owner. Low-level `h` is resolved as an index into transferred Unix FDs,
not interpreted as an OS descriptor. The selected descriptor is duplicated for
the session; all received originals are closed.

The `(fd, node_id)` API matches the repository capture adapter. **ScreenCast v6
deprecates node-ID-based targeting** and recommends `pipewire-serial` with
`PW_KEY_TARGET_OBJECT`, since IDs can be reused after node destruction. This
launcher preserves serial metadata, but the current capture adapter targets a
node ID. Do not assume continuity across monitor hotplug, display-mode changes
or suspend/resume. Serial-aware targeting needs a separate adapter improvement.

Persistence options and restore tokens are omitted, retaining the temporary
session default. Exact dialog presentation and backend support remain desktop
implementation details, not guarantees made by this module.

## Verification

```sh
.venv/bin/python -m pytest -q tests/test_portal.py tests/test_watch.py tests/test_cli_source_lifecycle.py
```

Tests use a mock D-Bus peer and real disposable local FDs. They cover ordering,
early responses, alternate handles, cancellation, denial, malformed streams and
FDs, timeouts, thread interruption, revocation, cleanup, deadline races, falsey
sources and queue priority. **No real session bus, desktop portal dialog,
compositor or PipeWire frames were exercised in these cloud tests.** Target
acceptance needs an authorized real desktop: test Cancel, successful selection
and frame delivery, stop-sharing, and cleanup after exit.

## Primary specifications

- [ScreenCast](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.ScreenCast.html)
- [Request](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Request.html)
- [Session](https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Session.html)
- [Window identifiers](https://flatpak.github.io/xdg-desktop-portal/docs/window-identifiers.html)
- [dbus-next asyncio](https://python-dbus-next.readthedocs.io/en/latest/message-bus/aio-message-bus.html)
- [dbus-next types and FD indices](https://python-dbus-next.readthedocs.io/en/latest/type-system/index.html)

These references were read during the original implementation and used for
reconstruction. Target-backend compatibility is not established by mock tests.
