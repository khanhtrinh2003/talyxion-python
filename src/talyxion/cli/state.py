"""Local state file — ``~/.talyxion/state.json``.

Persisted across CLI restarts. Atomic-write via tempfile + ``os.replace``.
chmod 0600 on POSIX. Contents are NOT secret (no keys / tokens) — just
operational metadata the runner needs to resume cleanly after restart:

* peak_equity_usd per profile (for drawdown gate)
* last_cycle_id (to skip duplicates on retry)
* consecutive_errors counter (for exponential backoff)
* next_due_at (when this profile's next cycle should run)
* outbound_ip cache (so we don't hit api.ipify.org every cycle)
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import platformdirs

from talyxion.cli._version import __cli_version__

STATE_FILENAME = "state.json"


def state_dir() -> Path:
    """Per-user state directory. macOS Application Support / Linux XDG_STATE / Win LocalAppData."""
    # Use platformdirs.user_state_dir for proper OS-conventional location.
    # On macOS this is ``~/Library/Application Support/talyxion``; on Linux
    # ``~/.local/state/talyxion``; on Windows ``%LOCALAPPDATA%\\talyxion``.
    p = Path(platformdirs.user_state_dir("talyxion"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def state_path() -> Path:
    return state_dir() / STATE_FILENAME


def _empty_state() -> dict[str, Any]:
    return {
        "cli_version": __cli_version__,
        "auth": {},
        "profiles": {},
        "outbound_ip": {"value": None, "cached_until": None},
        "saved_at": None,
    }


def load_state() -> dict[str, Any]:
    p = state_path()
    if not p.exists():
        return _empty_state()
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
        # Backfill any missing top-level keys (forward-compat for upgrades).
        base = _empty_state()
        for k in base:
            data.setdefault(k, base[k])
        return data
    except (OSError, json.JSONDecodeError):
        # Corrupt state file — back it up + start fresh so the runner
        # doesn't crash on first cycle. User can inspect ``.bak`` later.
        try:
            backup = p.with_suffix(".bak")
            p.rename(backup)
        except OSError:
            pass
        return _empty_state()


def save_state(state: dict[str, Any]) -> None:
    """Atomically write ``state`` to disk with mode 0600."""
    state["saved_at"] = datetime.now(timezone.utc).isoformat()
    state["cli_version"] = __cli_version__
    target = state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target.parent, prefix=".state-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=False, default=str)
        os.replace(tmp_path, target)
        try:
            target.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 0600
        except OSError:
            pass
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ── Convenience accessors used by the runner ────────────────────────


def get_profile_state(state: dict[str, Any], profile_id: int) -> dict[str, Any]:
    profiles = state.setdefault("profiles", {})
    key = str(profile_id)
    if key not in profiles:
        profiles[key] = {
            "peak_equity_usd": 0.0,
            "last_cycle_id": "",
            "last_cycle_at": None,
            "last_outcome": "",
            "consecutive_errors": 0,
            "next_due_at": None,
        }
    return profiles[key]


def is_pid_alive(pid: int) -> bool:
    """Portable "is this process running?" check.

    On POSIX we send signal 0 (no-op, but raises ``OSError`` if the
    process is gone). On Windows that would actually **kill** the
    process — Python's ``os.kill`` maps non-CTRL_* signals to
    ``TerminateProcess(handle, sig)``, so calling ``os.kill(pid, 0)``
    terminates the process with exit code 0. We use ``OpenProcess``
    with ``PROCESS_QUERY_LIMITED_INFORMATION`` (0x1000) instead, which
    succeeds iff the pid exists and the caller can query it.

    Returns False for non-positive pids and for any error we can't
    confidently interpret — the dashboard / runstate caller treats a
    False result as "stale pid file" which is the safe default.
    """
    import os as _os
    import sys as _sys

    if pid <= 0:
        return False

    if _sys.platform.startswith("win"):
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid),
            )
            if not handle:
                return False
            # Confirm the process is still active — OpenProcess can
            # succeed on a recently-exited pid until the kernel cleans
            # it up. GetExitCodeProcess returns STILL_ACTIVE (259) for
            # running processes.
            exit_code = ctypes.c_ulong(0)
            ok = ctypes.windll.kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code),
            )
            ctypes.windll.kernel32.CloseHandle(handle)
            STILL_ACTIVE = 259
            return bool(ok) and exit_code.value == STILL_ACTIVE
        except Exception:  # noqa: BLE001
            return False

    # POSIX: signal 0 raises OSError(ESRCH) when the process is gone,
    # OSError(EPERM) when the caller lacks permission — but the
    # latter still proves the pid is alive.
    try:
        _os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def prune_profile_state(state: dict[str, Any], live_profile_ids: set[int]) -> list[int]:
    """Drop local state for profiles no longer in the server's local-profile list.

    Returns the list of dropped profile_ids (so the runner can log them).
    """
    profiles = state.setdefault("profiles", {})
    dropped = []
    for key in list(profiles.keys()):
        try:
            pid = int(key)
        except ValueError:
            continue
        if pid not in live_profile_ids:
            del profiles[key]
            dropped.append(pid)
    return dropped
