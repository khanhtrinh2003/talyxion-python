"""``talyxion run | status | logs`` — daemon control + introspection."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from talyxion.cli.logger import log_path
from talyxion.cli.runner import run_loop
from talyxion.cli.state import load_state, state_path

console = Console()


@click.command(name="run")
@click.option("--once", is_flag=True, help="Run one cycle per profile, then exit (for debugging).")
@click.option("--profile", "only_profile", type=int, default=None,
              help="Limit run to this profile id (skip the others).")
@click.option("--dry-run", is_flag=True,
              help="Don't submit orders — log what would be submitted.")
@click.option("--background", "-d", is_flag=True,
              help="Detach into background via nohup. PID stored in state dir.")
def run_cmd(once: bool, only_profile: int | None, dry_run: bool, background: bool) -> None:
    """Start the trading daemon.

    Plays the cycle loop for every local-execution profile owned by the
    authenticated user. Token revoked / profile archived → daemon exits
    cleanly.
    """
    if background:
        # Spawn the headless ``talyxion-runner`` instead of re-exec'ing
        # ``sys.argv[0]`` — the user-facing entry is now the REPL, which
        # would refuse a non-TTY parent process. Fall back to invoking
        # the runner module directly if the script shim isn't on PATH
        # (e.g. when the package is run from a checkout via ``python -m``).
        import shutil
        runner_bin = shutil.which("talyxion-runner")
        if runner_bin:
            child = [runner_bin]
        else:
            child = [sys.executable, "-m", "talyxion.cli.runner_entry"]
        if once:
            child.append("--once")
        if only_profile is not None:
            child += ["--profile", str(only_profile)]
        if dry_run:
            child.append("--dry-run")

        # Detach the child so closing the REPL doesn't take the daemon
        # with it. ``start_new_session`` only exists on POSIX (calls
        # ``setsid``); Windows wants ``CREATE_NEW_PROCESS_GROUP`` +
        # ``DETACHED_PROCESS`` via ``creationflags``. Picking the right
        # kwargs at runtime keeps a single code path.
        pid_file = state_path().parent / "run.pid"
        log_file = log_path()
        with open(log_file, "ab") as out:
            popen_kwargs: dict = {
                "stdout": out,
                "stderr": out,
                "stdin": subprocess.DEVNULL,
            }
            if sys.platform.startswith("win"):
                popen_kwargs["creationflags"] = (
                    getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                    | getattr(subprocess, "DETACHED_PROCESS", 0)
                )
            else:
                popen_kwargs["start_new_session"] = True
            proc = subprocess.Popen(child, **popen_kwargs)
        pid_file.write_text(str(proc.pid))
        stop_hint = (
            f"Stop: [bold]taskkill /PID {proc.pid} /F[/bold]"
            if sys.platform.startswith("win")
            else f"Stop: [bold]kill {proc.pid}[/bold]"
        )
        console.print(f"[green]✓ Started in background (pid={proc.pid}).[/green]")
        console.print(f"  Log: [dim]{log_file}[/dim]")
        console.print(f"  {stop_hint}")
        return

    try:
        run_loop(once=once, only_profile=only_profile, dry_run=dry_run)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped.[/yellow]")


@click.command(name="status")
def status_cmd() -> None:
    """Show local state: which profiles are tracked, last outcome, next due."""
    from talyxion.cli.keyring_store import load_token_meta

    state = load_state()
    profiles = state.get("profiles", {})
    # Auth identity lives in the OS keyring (set by `talyxion auth login`),
    # not state.json — state.json is non-secret. Fall back to state.json
    # only for legacy installs from before 0.2.4.
    auth_meta = load_token_meta() or state.get("auth") or {}

    console.print(
        f"[bold]Auth:[/bold] {auth_meta.get('email', '—')} "
        f"([cyan]{auth_meta.get('tier', '—')}[/cyan]) · "
        f"token [cyan]{auth_meta.get('prefix', '—')}[/cyan] "
        f"([dim]{auth_meta.get('label', '—')}[/dim])"
    )

    pid_file = state_path().parent / "run.pid"
    if pid_file.exists():
        from talyxion.cli.state import is_pid_alive
        try:
            pid = int(pid_file.read_text().strip())
        except (OSError, ValueError):
            pid = 0
        if pid and is_pid_alive(pid):
            console.print(f"[green]Daemon running[/green] (pid={pid})")
        elif pid:
            console.print(f"[yellow]Stale PID file[/yellow] — daemon not running.")
        else:
            console.print(f"[yellow]Unreadable PID file[/yellow] — daemon state unknown.")
    else:
        console.print("[dim]Daemon not running[/dim] (use `/run -d` to start one).")

    if not profiles:
        console.print("[dim]No profile state yet.[/dim]")
        return

    table = Table(title="Local profile state", show_lines=False)
    table.add_column("Profile", style="cyan")
    table.add_column("Peak equity", justify="right")
    table.add_column("Last cycle", justify="right")
    table.add_column("Outcome")
    table.add_column("Errors", justify="right")
    table.add_column("Next due", justify="right")
    table.add_column("Last error", overflow="fold")
    for pid, ps in profiles.items():
        outcome = ps.get("last_outcome", "—")
        outcome_color = {
            "ok": "green", "auth_fail": "red", "ip_blocked": "red",
            "data_error": "yellow", "exec_error": "yellow", "conflict": "yellow",
        }.get(outcome, "white")
        last_cycle = ps.get("last_cycle_at", "")
        if last_cycle:
            try:
                last_cycle = datetime.fromisoformat(last_cycle).strftime("%H:%M:%S")
            except ValueError:
                pass
        next_due = ps.get("next_due_at", "")
        if next_due:
            try:
                d = datetime.fromisoformat(next_due)
                delta = int((d - datetime.now(timezone.utc)).total_seconds())
                next_due = f"in {delta}s" if delta > 0 else f"{-delta}s ago"
            except ValueError:
                pass
        last_err = (ps.get("last_error") or "")[:120]
        table.add_row(
            f"#{pid}",
            f"${ps.get('peak_equity_usd', 0):.2f}",
            last_cycle or "—",
            f"[{outcome_color}]{outcome}[/]",
            str(ps.get("consecutive_errors", 0)),
            next_due or "—",
            f"[dim]{last_err}[/dim]" if last_err else "—",
        )
    console.print(table)
    console.print(f"\n[dim]State file: {state_path()}[/dim]")
    console.print(f"[dim]Log file:   {log_path()}[/dim]")


def _tail_lines(p, n: int) -> list[str]:
    """Read the last ``n`` lines of file ``p`` without slurping it whole.

    Heuristic: 64 KB tail window covers ~1k log lines comfortably. If the
    file is smaller we read it all. Works identically on POSIX + Windows
    because we open in binary and decode with ``errors='replace'``.
    """
    with p.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 64 * 1024))
        return f.read().decode("utf-8", errors="replace").splitlines()[-n:]


@click.command(name="logs")
@click.option("-n", "--lines", default=50, help="How many tail lines to show (default 50).")
@click.option("-f", "--follow", is_flag=True, help="Follow the log (tail -f).")
def logs_cmd(lines: int, follow: bool) -> None:
    """Show the rolling CLI log.

    ``--follow`` is implemented in pure Python instead of shelling out
    to ``tail -f`` — that command doesn't exist on Windows. We poll the
    file size every 300 ms and print whatever bytes appended since the
    last read; Ctrl-C exits cleanly on every platform.
    """
    p = log_path()
    if not p.exists():
        console.print(f"[dim]No log yet at {p}.[/dim]")
        return

    if not follow:
        try:
            for line in _tail_lines(p, lines):
                console.print(line, highlight=False)
        except Exception as exc:  # noqa: BLE001
            console.print(f"[red]Failed to read log:[/red] {exc}")
        return

    # Follow mode: print the initial backfill, then poll for appends.
    try:
        for line in _tail_lines(p, lines):
            console.print(line, highlight=False)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Failed to read log:[/red] {exc}")
        return

    import time as _time
    try:
        with p.open("rb") as f:
            f.seek(0, 2)  # start from current EOF
            last_size = f.tell()
            while True:
                _time.sleep(0.3)
                f.seek(0, 2)
                size = f.tell()
                if size < last_size:
                    # File was rotated / truncated — re-open from start.
                    f.close()
                    f = p.open("rb")
                    last_size = 0
                    continue
                if size == last_size:
                    continue
                f.seek(last_size)
                chunk = f.read(size - last_size)
                last_size = size
                for line in chunk.decode("utf-8", errors="replace").splitlines():
                    console.print(line, highlight=False)
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Log follow failed:[/red] {exc}")
