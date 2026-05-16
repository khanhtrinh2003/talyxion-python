"""``talyxion`` — interactive entry point.

Running ``talyxion`` (no args) in a TTY drops the user into an interactive
REPL (:mod:`talyxion.cli.repl`); every operation is then driven by slash
commands. Non-TTY invocations are refused with a hint pointing at
``talyxion-runner``, the headless daemon entry intended for systemd /
launchd / cron.

The Click command group still exists internally as ``_internal_cli`` —
the REPL dispatcher translates slash commands into argv and feeds them
into that group, so every flag/option already documented on the
individual handlers (``auth.py``, ``credentials.py``, …) is reachable
inside the REPL with no extra wiring.
"""
from __future__ import annotations

import sys

import click

from talyxion.cli._version import __cli_version__
from talyxion.cli.auth import auth as auth_cmd
from talyxion.cli.credentials import add_cmd, list_creds_cmd, remove_cmd
from talyxion.cli.dashboard import dashboard_cmd
from talyxion.cli.order_cmd import order_group, tape_cmd
from talyxion.cli.portfolio import (
    balance_cmd,
    doctor_cmd,
    portfolio_cmd,
    show_cmd,
    tier_cmd,
    whoami_cmd,
)
from talyxion.cli.profiles import list_group, positions_cmd
from talyxion.cli.run_cmd import logs_cmd, run_cmd, status_cmd
from talyxion.cli.update_cmd import update_cmd


@click.group(name="talyxion", help="Internal dispatcher (reached via the REPL, not the shell).")
@click.version_option(__cli_version__, "-V", "--version", prog_name="talyxion")
def _internal_cli() -> None:
    """Click root group used by the REPL dispatcher. Never reached from
    the shell — :func:`cli` intercepts ``argv`` first."""


_internal_cli.add_command(auth_cmd, name="auth")
_internal_cli.add_command(add_cmd, name="add")
_internal_cli.add_command(remove_cmd, name="remove")
_internal_cli.add_command(run_cmd, name="run")
_internal_cli.add_command(status_cmd, name="status")
_internal_cli.add_command(logs_cmd, name="logs")
_internal_cli.add_command(update_cmd, name="update")
_internal_cli.add_command(list_group, name="list")
list_group.add_command(list_creds_cmd, name="creds")
_internal_cli.add_command(positions_cmd, name="positions")
_internal_cli.add_command(portfolio_cmd, name="portfolio")
_internal_cli.add_command(balance_cmd, name="balance")
_internal_cli.add_command(whoami_cmd, name="whoami")
_internal_cli.add_command(tier_cmd, name="tier")
_internal_cli.add_command(show_cmd, name="show")
_internal_cli.add_command(doctor_cmd, name="doctor")
_internal_cli.add_command(dashboard_cmd, name="dashboard")
_internal_cli.add_command(order_group, name="order")
_internal_cli.add_command(tape_cmd, name="tape")


_NON_TTY_HINT = (
    "talyxion is interactive — run it inside a terminal.\n"
    "For service-managed daemons (systemd, launchd, cron) use:\n"
    "    talyxion-runner [--once] [--profile ID] [--dry-run] [--background]\n"
    "Authenticate first by opening talyxion in a TTY and running /login."
)


def cli() -> None:
    """Console-script entry registered in ``pyproject.toml``."""
    if len(sys.argv) > 1:
        # No argv subcommands are exposed to the shell anymore. Print the
        # hint and exit non-zero so scripts surface the breakage clearly
        # instead of silently doing nothing.
        print(_NON_TTY_HINT, file=sys.stderr)
        sys.exit(2)

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(_NON_TTY_HINT, file=sys.stderr)
        sys.exit(2)

    from talyxion.cli.repl import run_repl
    sys.exit(run_repl())


if __name__ == "__main__":  # pragma: no cover
    cli()
