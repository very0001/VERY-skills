# 辅助输入接口（历史持久模式）

本页为非默认的实验性辅助设备接口；普通桌面任务使用共享输入，参见 [项目README](../README.md)。下文保留持久接口及历史验证记录；当前光标统一为10×10黄色方块，不再使用青色十字或箭头。

# Experimental auxiliary X11 input

## Current safety status

**Not yet approved for normal simultaneous use.** In the latest shared-screen
attempt the user reported their physical keyboard was unusable. Its device was
still enabled and attached to the core keyboard; removing the auxiliary pair
restored the normal device topology. X11 focus IDs alone do not establish working
application input. MPX, Electron and input-method compatibility remain unproven.

Live start is disabled unless `SEMANTIC_CU_ENABLE_AUX=1` is explicitly set for an
agreed test. No real-device tests have been run after the user's stop instruction.
Current validation: 64 offline tests, with native events mocked and DISPLAY unset.
The current machine has one DP-2 2560x1440 monitor. Dual-monitor use is unverified.

## Interface

The auxiliary interface exposes 20 tools, including desktop_aux_start/status/action/stop.
Actions require host authorization, --allow-actions, and a fresh X11 observation.
Pointer coordinates remain inside the observed window. No physical devices are
reattached; input methods, clipboard contents and core keymaps are not rewritten.

Supported action payloads:

- move: {x,y}
- click: {x,y,button=1}; buttons 1..9, including side buttons 8/9
- key: {key}; one X keysym
- text: {text,unicode_mode?}; max 4096 characters, existing 13 ms character pacing
- chord: {keys,hold=0}; max 8 distinct keysyms, hold 0..2 seconds
- scroll: {x,y,direction,steps=1}; up/down/left/right, 1..30 steps
- drag: {x,y,to_x,to_y,button=1,duration=.3}; .02..2 seconds
- key_down: {key,lease_seconds=5}; lease .2..10 seconds
- key_up: {key}
- button_down: {x,y,button,lease_seconds=5}
- button_up: {button}
- release_all: {}

Use canonical X keysyms such as Control_L, Shift_L, Return and Left. A held input
is bound to the observed window; release it before text/chord/drag or changing
window scope. Duplicate downs and implicit release through key/click are rejected.
Status reports held inputs and their release reason.

`unicode_mode="fcitx"` is an explicit, app-specific Unicode entry route, at most
256 non-ASCII characters per call. It worked in an earlier foreground VS Code test,
but is NOT evidence of reliability while another app has physical keyboard focus.
Reference: https://fcitx-im.org/wiki/Unicode

The auxiliary pointer uses a native 10x10 yellow square on its root/initial target window.
Actual visibility and independent dual-screen interaction still require joint tests.

## Lifetime, exclusion and recovery

A separate supervisor owns a per-user/per-X-server lease. Local display aliases
including :1, :1.0 and unix/:1 share one lock. Remote display identities are refused.
Participating gateways hold this same lock for legacy actions, so another gateway
cannot silently mix legacy input with an active auxiliary session.

Held inputs expire automatically. Parent EOF, worker failure and session timeout
cancel the worker process group, release held inputs and remove the owned pair.
Partial request lines do not block the lease timer. An experimental session lasts
30 seconds by default; SEMANTIC_CU_AUX_SESSION_SECONDS may explicitly set 5..300.

Failed cleanup retains cleanup_pending and a persistent unclean-owner marker.
Other gateways refuse input until recovery is confirmed. Normal stop is permitted
while paused. For an unlocked recorded orphan, explicit host-approved
`desktop_aux_stop` with `recover_stale=true` attempts cleanup. It never creates a
pair, refuses unexpected attached physical devices, and only clears the marker
after inactive status is confirmed. A live supervisor's lock cannot be bypassed.

Do not replay uncertain actions. Native workers and their xinput children use Linux parent-death signals. A
headless process-only check confirmed child exit on owner death, without opening
an X display. Force-killing every supervisor/process still cannot provide an
absolute immediate-release guarantee; the persistent marker prevents
subsequent cooperating gateways from proceeding silently. Uncoordinated external
X11 clients are not protected by this protocol. Same-window application carets
remain shared, and Wayland is unsupported.
