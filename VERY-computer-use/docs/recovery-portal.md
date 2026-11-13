# Portal, CLI and watcher reconstruction

The previous workspace disappeared following an execution-environment failure.
The following files were reconstructed from the retained implementation and
review context, not copied from a verified original backup:

- `src/semantic_cu/portal.py`
- `src/semantic_cu/cli.py`
- `src/semantic_cu/watch.py`
- `tests/test_portal.py`
- `tests/test_cli_source_lifecycle.py`
- `tests/test_watch.py`
- `docs/wayland.md`

Their contents and test organization may differ from the lost files. There is
no claim of original hash identity or preservation of every original test.
The original test totals do not establish the rebuilt version's correctness.

The reconstructed API preserves explicit portal start, synchronous borrowed
FD/node results, a dedicated asyncio lifetime, cancel/timeout/revocation
handling, FD cleanup, temporary permissions and no alternate capture fallback.
CLI and WatchService deadlines cover portal construction. Factory cancellation
is cooperative, legacy factories remain supported, and standalone replay still
uses synchronous output rather than the watcher's bounded event queue.

Regression checks include critical-event queue priority, strict non-bool queue
size validation, stopping/closing falsey sources, recording request_stop errors,
all-resource cleanup, preserving startup errors, and joining helper timers.

Initial focused validation on the reconstruction:

```sh
.venv/bin/python -m pytest -q tests/test_cli_source_lifecycle.py tests/test_watch.py tests/test_portal.py
```

Result: **102 passed**. This is a new run against the reconstructed files. No
real desktop, portal consent UI or PipeWire capture was exercised. Whole-project
validation and packaging are coordinated separately after all module recovery.
