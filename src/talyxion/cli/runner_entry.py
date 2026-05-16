"""``talyxion-runner`` — non-interactive entry point for the cycle daemon.

Exists separately from ``talyxion`` (which is now an interactive REPL)
so service managers like systemd / launchd can run the daemon headlessly:

    talyxion-runner                   # foreground loop
    talyxion-runner --once            # one cycle per profile, then exit
    talyxion-runner --background      # detach (PID stored in state dir)
    talyxion-runner --profile 17      # restrict to one profile
    talyxion-runner --dry-run         # log what would be submitted

This is the *only* non-REPL surface — every other user-facing operation
happens inside ``talyxion``'s REPL via slash commands.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from talyxion.cli.logger import log_path
from talyxion.cli.runner import run_loop
from talyxion.cli.state import state_path


def _spawn_background(argv: list[str]) -> int:
    """Re-exec ourselves detached. PID lands in ``state_dir/run.pid``.

    Cross-platform detach: POSIX uses ``start_new_session=True`` so the
    child becomes its own session leader (survives the terminal closing);
    Windows uses ``CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`` flags
    in ``creationflags`` so the daemon detaches from the console.
    """
    log_file = log_path()
    pid_file = state_path().parent / "run.pid"
    forwarded = [a for a in argv if a not in ("--background", "-d")]
    popen_kwargs: dict = {
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        popen_kwargs["start_new_session"] = True
    with open(log_file, "ab") as out:
        popen_kwargs["stdout"] = out
        popen_kwargs["stderr"] = out
        proc = subprocess.Popen(forwarded, **popen_kwargs)
    pid_file.write_text(str(proc.pid))
    stop_hint = (
        f"taskkill /PID {proc.pid} /F"
        if sys.platform.startswith("win")
        else f"kill {proc.pid}"
    )
    print(f"talyxion-runner started in background (pid={proc.pid}).")
    print(f"  log: {log_file}")
    print(f"  stop: {stop_hint}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="talyxion-runner",
        description=(
            "Headless Talyxion cycle daemon. Authenticate first by running "
            "`talyxion` in a TTY, then point your service manager at this "
            "binary."
        ),
    )
    parser.add_argument("--once", action="store_true",
                        help="Run one cycle per profile, then exit.")
    parser.add_argument("--profile", type=int, default=None,
                        help="Limit run to this profile id.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Don't submit orders — log what would be submitted.")
    parser.add_argument("--background", "-d", action="store_true",
                        help="Detach into background via nohup-style spawn.")
    args = parser.parse_args()

    if args.background:
        # Re-exec the same binary without the background flag so the child
        # actually runs the loop instead of forking again.
        argv = [sys.argv[0]] + sys.argv[1:]
        return _spawn_background(argv)

    try:
        run_loop(once=args.once, only_profile=args.profile, dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
