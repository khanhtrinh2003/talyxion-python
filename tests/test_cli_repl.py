"""Unit tests for the interactive Talyxion REPL.

These cover the pure-Python pieces — slash parsing, dispatch routing,
completer construction — without spinning up a real prompt_toolkit
session or touching the OS keyring. The end-to-end interactive flow is
exercised manually (no good headless harness for prompt_toolkit + rich).
"""
from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest import mock

import pytest

# All of the cli.* submodules import the OS keyring eagerly. Make sure
# that's still OK on a clean test runner — keyring's "null" backend is
# fine because none of the tests below actually read/write secrets.
pytest.importorskip("keyring")
pytest.importorskip("prompt_toolkit")

from talyxion.cli import repl  # noqa: E402


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_has_every_user_facing_slash():
    slugs = {c.slug for c in repl.REGISTRY}
    # Spot-check the headline commands the trading_setup page advertises.
    for required in {"login", "logout", "add", "remove", "creds",
                     "profiles", "show", "positions", "portfolio",
                     "balance", "run", "runstate", "logs",
                     "whoami", "tier", "doctor", "update"}:
        assert required in slugs, f"/{required} missing from REGISTRY"


def test_registry_includes_phase_2_commands():
    """Phase 2.1 + 2.2 commands must be reachable from the slash menu."""
    slugs = {c.slug for c in repl.REGISTRY}
    for required in {"dashboard", "order", "tape"}:
        assert required in slugs, f"/{required} missing from REGISTRY"


def test_order_subcommands_registered():
    """The Click group behind /order must expose place/cancel/list/cancel-all."""
    from talyxion.cli.main import _internal_cli
    order_group = _internal_cli.commands["order"]
    assert hasattr(order_group, "commands"), "/order is not a Click group"
    for sub in {"place", "cancel", "list", "cancel-all"}:
        assert sub in order_group.commands, f"/order {sub} missing"


def test_tape_url_encodes_since_timestamp():
    """ISO timestamps contain ``+`` for tz offset — must be URL-encoded
    before going into the query string, otherwise the server reads it
    as a space and returns HTTP 400 bad_since on every follow-up poll.
    """
    from urllib.parse import quote
    iso = "2026-05-15T14:26:11.123456+00:00"
    assert "%2B" in quote(iso)
    # Sanity: the unencoded form has the raw +, which is exactly the
    # bug we're guarding against.
    assert "+" in iso


def test_dashboard_imports_on_every_supported_platform():
    """The dashboard module must import cleanly on macOS, Linux, and
    Windows. macOS / Linux use ``termios`` + ``select``; Windows uses
    ``msvcrt``. Either way ``_HAVE_KEYBOARD`` ends up True; the legacy
    ``_HAVE_TERMIOS`` alias also stays exposed for old callers."""
    from talyxion.cli import dashboard as dash
    assert hasattr(dash, "_HAVE_TERMIOS")
    assert hasattr(dash, "_HAVE_KEYBOARD")
    assert hasattr(dash, "dashboard_cmd")
    assert hasattr(dash, "_IS_WINDOWS")


def test_pid_alive_helper_basics():
    """Portable PID-alive check must agree on the current process and
    refuse obviously-bogus pids. The Windows footgun this guards against
    is ``os.kill(pid, 0)`` which actually terminates the process on
    Windows — never use it as a liveness probe."""
    import os
    from talyxion.cli.state import is_pid_alive
    assert is_pid_alive(os.getpid()) is True
    assert is_pid_alive(0) is False
    assert is_pid_alive(-1) is False
    # 99,999,999 is well above any kernel pid cap (Linux default 4.2M,
    # Windows uses 32-bit but never assigns numbers this large in
    # practice). Pid lookup must therefore return False.
    assert is_pid_alive(99_999_999) is False


def test_dashboard_keyboard_backend_loaded_for_current_platform():
    """The dashboard module must expose a working keyboard backend on
    whichever OS the test runner is on. _HAVE_KEYBOARD == True is the
    proof that either msvcrt (Windows) or termios+select (POSIX)
    imported cleanly."""
    from talyxion.cli import dashboard as dash
    assert dash._HAVE_KEYBOARD is True
    # Backwards-compat alias retained for older callers.
    assert dash._HAVE_TERMIOS == dash._HAVE_KEYBOARD


def test_dashboard_panels_handle_string_typed_decimals():
    """Regression: server returns monetary fields as Decimal-stringified
    JSON ("7.74" not 7.74). Earlier code did ``(upnl or 0) >= 0`` which
    crashed with TypeError: '>=' not supported between instances of
    'str' and 'int'. All four panel builders must coerce safely."""
    from talyxion.cli.dashboard import (
        _as_float, _build_header, _build_positions_panel,
        _build_profiles_panel, _build_tape_panel,
    )
    snap = {
        "profiles": [
            {"id": 85, "name": "p1", "exchange": "binance", "mode": "live",
             "status": "active",
             "last_app_wallet_usd": "37006.10",
             "last_app_unrealized_pnl": "7.74",
             "last_app_positions": []},
        ],
        "positions_by_profile": {
            85: [{"symbol": "BTCUSDT", "side": "long", "qty": "0.42",
                  "entry_price": "60000", "mark_price": "60500",
                  "unrealized_pnl": "180.5", "pnl_pct": "0.83"}],
        },
        "tape": [], "last_refresh": None, "last_error": None,
        "show_archived": False, "notice": None,
    }
    # Must not raise.
    _build_header(snap)
    _build_profiles_panel(snap)
    _build_positions_panel(snap)
    _build_tape_panel(snap)
    # Coercion sanity:
    assert _as_float("7.74") == 7.74
    assert _as_float(None) == 0.0
    assert _as_float("garbage") == 0.0
    assert _as_float("0") == 0.0


def test_binance_adapter_implements_phase_2_methods():
    """create_limit_order, cancel_order, fetch_open_orders should be
    implemented (not the base NotImplementedError stub)."""
    from talyxion.cli.exchanges.binance import BinanceAdapter
    from talyxion.cli.exchanges._base import ExchangeAdapter
    for name in ("create_limit_order", "cancel_order", "fetch_open_orders"):
        adapter_fn = getattr(BinanceAdapter, name)
        base_fn = getattr(ExchangeAdapter, name)
        assert adapter_fn is not base_fn, (
            f"BinanceAdapter.{name} still inherits the NotImplementedError stub"
        )


def test_registry_argv_targets_resolve_to_click_commands():
    """Every registered slash must dispatch to a real Click subcommand."""
    from talyxion.cli.main import _internal_cli
    for cmd in repl.REGISTRY:
        cur = _internal_cli
        for token in cmd.argv:
            assert hasattr(cur, "commands"), (
                f"/{cmd.slug} argv={cmd.argv} bottomed out at non-group"
            )
            assert token in cur.commands, (
                f"/{cmd.slug} argv={cmd.argv}: token {token!r} not found"
            )
            cur = cur.commands[token]


# ---------------------------------------------------------------------------
# Slash dispatcher — parse()
# ---------------------------------------------------------------------------


@pytest.fixture()
def dispatcher() -> repl.SlashDispatcher:
    return repl.SlashDispatcher()


def test_parse_blank_line_returns_none(dispatcher):
    assert dispatcher.parse("") is None
    assert dispatcher.parse("   ") is None


@pytest.mark.parametrize("line", ["exit", "quit", "help", "?", "clear"])
def test_parse_bare_word_meta_aliases(dispatcher, line):
    argv = dispatcher.parse(line)
    assert argv == ["__meta__", line]


def test_parse_slash_meta_routes_to_meta_bucket(dispatcher):
    assert dispatcher.parse("/exit") == ["__meta__", "exit"]
    assert dispatcher.parse("/help /add") == ["__meta__", "help", "/add"]
    assert dispatcher.parse("/clear") == ["__meta__", "clear"]


def test_parse_unknown_slash(dispatcher):
    assert dispatcher.parse("/totally-not-a-cmd") == [
        "__unknown__", "totally-not-a-cmd",
    ]


def test_parse_bare_word_routes_to_unknown(dispatcher):
    # Non-slash, non-meta words shouldn't accidentally dispatch — we
    # preserve the entire stripped input as the "what they typed" hint.
    assert dispatcher.parse("rm -rf /") == ["__unknown__", "rm -rf /"]


def test_parse_login_simple(dispatcher):
    assert dispatcher.parse("/login") == ["auth", "login"]


def test_parse_login_with_flags(dispatcher):
    assert dispatcher.parse("/login --label cli@laptop --no-browser") == [
        "auth", "login", "--label", "cli@laptop", "--no-browser",
    ]


def test_parse_add_with_quoted_label(dispatcher):
    assert dispatcher.parse('/add binance --label "prod main" --testnet') == [
        "add", "binance", "--label", "prod main", "--testnet",
    ]


def test_parse_run_combinations(dispatcher):
    assert dispatcher.parse("/run --once --profile 17") == [
        "run", "--once", "--profile", "17",
    ]
    assert dispatcher.parse("/run -d --dry-run") == [
        "run", "-d", "--dry-run",
    ]


def test_parse_profiles_with_scope(dispatcher):
    assert dispatcher.parse("/profiles --scope archived") == [
        "list", "profiles", "--scope", "archived",
    ]


def test_parse_runstate_is_aliased(dispatcher):
    """/runstate must NOT clash with /status (which is auth status)."""
    assert dispatcher.parse("/runstate") == ["status"]
    assert dispatcher.parse("/status") == ["auth", "status"]


def test_parse_handles_unterminated_quote(dispatcher, capsys):
    # shlex raises ValueError on bad quoting; the dispatcher should print
    # an error and return None instead of crashing.
    out = dispatcher.parse('/add binance --label "missing close')
    assert out is None


# ---------------------------------------------------------------------------
# Dispatch — Click-side error paths
# ---------------------------------------------------------------------------


def test_dispatch_swallows_unknown_command_inside_repl(dispatcher):
    """If we feed bogus argv directly, the dispatcher should print a
    Usage error via Click and stay alive (no SystemExit propagated)."""
    # We pass argv that bypasses parse() so we can hit the actual Click
    # dispatch path with a guaranteed-bad token.
    dispatcher.dispatch(["definitely-not-a-subcommand"])
    # Just reaching here without an exception is the assertion — no
    # tracebacks, no SystemExit leaks.


def test_dispatch_sets_inside_repl_flag(dispatcher):
    """The dispatcher must flip auth._INSIDE_REPL before invoking Click
    so handlers know not to ``sys.exit`` on soft errors."""
    seen: dict[str, bool] = {}

    from talyxion.cli import auth as auth_mod

    def fake_main(*, args, standalone_mode, prog_name):
        seen["inside_during_call"] = auth_mod._INSIDE_REPL

    with mock.patch.object(dispatcher._cli, "main", side_effect=fake_main):
        dispatcher.dispatch(["auth", "status"])

    assert seen["inside_during_call"] is True
    # Flag must be reset after dispatch (so non-REPL callers see False).
    assert auth_mod._INSIDE_REPL is False


# ---------------------------------------------------------------------------
# Completer
# ---------------------------------------------------------------------------


def _drive_completer(completer, text: str) -> list[str]:
    """Helper: feed ``text`` to ``completer`` and return the offered slugs."""
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document
    doc = Document(text=text, cursor_position=len(text))
    return [c.text for c in completer.get_completions(doc, CompleteEvent())]


def test_completer_offers_every_slash_when_query_is_empty():
    completer = repl.build_completer()
    offered = set(_drive_completer(completer, "/"))
    for cmd in repl.REGISTRY:
        assert f"/{cmd.slug}" in offered, f"/{cmd.slug} missing from dropdown"
    # Meta-only entries:
    assert "/help" in offered
    assert "/clear" in offered
    assert "/exit" in offered


def test_completer_filters_on_substring():
    completer = repl.build_completer()
    # Typing ``/bal`` should narrow down to /balance.
    offered = _drive_completer(completer, "/bal")
    assert "/balance" in offered
    assert "/portfolio" not in offered


def test_completer_filters_on_prefix_without_slash():
    completer = repl.build_completer()
    offered = _drive_completer(completer, "port")
    assert "/portfolio" in offered


def test_completer_hands_off_to_flag_layer_after_first_token():
    completer = repl.build_completer()
    # After the space, NestedCompleter should be offering flags for /add.
    offered = _drive_completer(completer, "/add ")
    assert "binance" in offered


# ---------------------------------------------------------------------------
# Bootstrap — skip device-flow when token already present
# ---------------------------------------------------------------------------


def test_bootstrap_short_circuits_when_already_authenticated():
    with mock.patch("talyxion.cli.repl.load_device_token",
                    return_value="raw-token-value"):
        with mock.patch("talyxion.cli.repl._device_flow_login") as flow:
            assert repl.bootstrap() is True
            flow.assert_not_called()


def test_bootstrap_runs_device_flow_when_no_token():
    with mock.patch("talyxion.cli.repl.load_device_token", return_value=None):
        with mock.patch(
            "talyxion.cli.repl._device_flow_login", return_value=True,
        ) as flow:
            assert repl.bootstrap() is True
            flow.assert_called_once()


# ---------------------------------------------------------------------------
# Main entry — TTY gating
# ---------------------------------------------------------------------------


def test_cli_refuses_non_tty(monkeypatch):
    from talyxion.cli import main as main_mod
    monkeypatch.setattr(sys, "argv", ["talyxion"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    with pytest.raises(SystemExit) as exc_info:
        main_mod.cli()
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# Did-you-mean suggestions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("typo, expected", [
    ("/balanc",  "/balance"),
    ("balance",  "/balance"),    # bare word still suggests slash form
    ("port",     "/portfolio"),  # prefix fallback when difflib misses
    ("logn",     "/login"),
    ("showw",    "/show"),
    ("/exi",     "/exit"),       # meta words are in the pool
])
def test_suggest_command_finds_close_match(typo, expected):
    suggestions = repl.suggest_command(typo)
    assert expected in suggestions, f"{typo!r} did not suggest {expected!r}; got {suggestions}"


@pytest.mark.parametrize("noise", ["", "   ", "?", "xyz-nothing-close"])
def test_suggest_command_returns_empty_on_noise(noise):
    assert repl.suggest_command(noise) == []


def test_suggest_command_caps_at_n():
    # The pool has ``logs`` and ``login`` and ``logout`` — n=2 must cap.
    out = repl.suggest_command("log", n=2)
    assert len(out) <= 2


# ---------------------------------------------------------------------------
# Friendly HTTP error explainer
# ---------------------------------------------------------------------------


def test_explain_http_failure_404_names_version_skew():
    import httpx
    from talyxion.cli.device_token_client import explain_http_failure
    req = httpx.Request("GET", "https://example.com/api/v1/talyxion/trading/profiles/?include=all")
    resp = httpx.Response(404, request=req, json={"detail": "Not found."})
    err = httpx.HTTPStatusError("Not Found", request=req, response=resp)
    headline, hint = explain_http_failure(err, "/trading/profiles/?include=all")
    assert "404" in headline
    assert hint is not None
    assert "CLI is likely newer than the server" in hint
    assert "Not found." in hint  # server body relayed


def test_explain_http_failure_connect_error_suggests_network():
    import httpx
    from talyxion.cli.device_token_client import explain_http_failure
    err = httpx.ConnectError("Failed to establish a connection")
    headline, hint = explain_http_failure(err, "/trading/whoami/")
    assert "Can't reach" in headline
    assert hint is not None and "TALYXION_BASE_URL" in hint


def test_explain_http_failure_429_includes_retry_advice():
    import httpx
    from talyxion.cli.device_token_client import explain_http_failure
    req = httpx.Request("GET", "https://example.com/api/v1/talyxion/trading/profiles/")
    resp = httpx.Response(429, request=req, json={"message": "Slow down."})
    err = httpx.HTTPStatusError("Too Many", request=req, response=resp)
    headline, hint = explain_http_failure(err, "/trading/profiles/")
    assert "429" in headline
    assert hint is not None and "Slow down" in hint


def test_cli_refuses_argv(monkeypatch):
    """No shell-level subcommands anymore — any extra argv is a no-go."""
    from talyxion.cli import main as main_mod
    monkeypatch.setattr(sys, "argv", ["talyxion", "auth", "login"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    with pytest.raises(SystemExit) as exc_info:
        main_mod.cli()
    assert exc_info.value.code == 2
