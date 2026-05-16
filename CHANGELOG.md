# Changelog

## 0.4.5 — Hotfix: default base URL points at the wrong host

**Critical fix.** Every call from a freshly-installed v0.4.4 client (with no
`base_url=` argument and no `TALYXION_BASE_URL` env var) failed with
``TalyxionConnectionError: [Errno 8] nodename nor servname provided, or not
known``. The default was hard-coded to ``https://api.talyxion.com`` — a
subdomain that does not exist. The Talyxion deployment runs on the single
origin ``talyxion.com`` (web + REST + WebSocket all under one host).

### What changed

- ``talyxion._config.DEFAULT_BASE_URL`` → ``https://talyxion.com``.
- README "Configuration" table already documents the corrected default.
- ``tests/test_client.py`` ``test_default_base_url`` updated to assert the
  new default (and the corresponding ``wss://talyxion.com``).
- ``tests/conftest.py`` switched its fake test host to
  ``https://test.local`` so nothing in the test suite references a
  Talyxion subdomain that doesn't exist.
- ``docs/PUBLISHING.md`` smoke-test block no longer references a fictitious
  ``staging.talyxion.com`` — there is no staging host, only production.
- README override example now points at ``http://localhost:8000`` (Django
  runserver) instead of a fake staging URL.

### Migration

If you're on 0.4.4 and don't want to upgrade yet, set the env var:

```bash
export TALYXION_BASE_URL=https://talyxion.com
```

or pass it explicitly:

```python
tlx = Talyxion(api_key="tk_...", base_url="https://talyxion.com")
```

## 0.4.4 — Windows support: first-class macOS / Linux / Windows parity

Every Phase 2.1 + 2.2 feature now runs natively on Windows 10+ in
addition to macOS and Linux. The CLI auto-picks the right backend per
OS — no flags, no environment variables.

### What changed

- **`/dashboard`** — the htop-style TUI used POSIX-only ``termios`` +
  ``tty.setcbreak`` + ``select()`` on stdin. Refactored to dispatch on
  ``sys.platform``: Windows reads keys via ``msvcrt.kbhit()`` +
  ``getwch``, POSIX keeps the ``termios.tcgetattr`` + ``setcbreak`` +
  ``select`` path. Two-byte function/arrow keys on Windows are
  consumed and ignored so the dispatcher never sees half-encoded
  input. The earlier "not yet supported on Windows" guard is gone.
- **`/run -d` + `talyxion-runner --background`** — POSIX detach uses
  ``start_new_session=True`` (which calls ``setsid``); that kwarg is a
  no-op-error on Windows. Now Windows uses
  ``creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`` for
  the same "survive the parent terminal" semantics. The stop hint
  prints ``taskkill /PID <pid> /F`` on Windows and ``kill <pid>`` on
  POSIX.
- **Critical: `os.kill(pid, 0)` Windows footgun closed.** Python's
  ``os.kill`` on Windows maps any non-CTRL_* signal to
  ``TerminateProcess(handle, sig)``, so the daemon-alive probe was
  literally killing the daemon on Windows. New
  ``talyxion.cli.state.is_pid_alive(pid)`` uses ``OpenProcess`` +
  ``GetExitCodeProcess`` on Windows and ``signal.0`` on POSIX. Every
  caller (``/runstate``, ``/dashboard`` header, REPL toolbar refresh)
  switched over.
- **`/logs -f`** — replaced the ``subprocess.run(["tail", ...])``
  shellout with a pure-Python polling loop that detects file rotation
  by shrinking size. Works the same on every OS; Ctrl-C exits
  cleanly.

### Tests

- New regressions: ``is_pid_alive(os.getpid()) is True`` and
  ``is_pid_alive(99_999_999) is False`` — the latter is the safety
  test that the Windows path doesn't accidentally call
  ``TerminateProcess``.
- ``test_dashboard_imports_on_every_supported_platform`` confirms the
  module loads on macOS, Linux, and Windows test runners.

### Docs

- ``/trading/setup/`` "What's new in 0.4" gains a 4th card explaining
  the Windows port (msvcrt vs termios dispatch, CREATE_NEW_PROCESS_GROUP
  vs setsid, pure-Python tail-f).
- ``/dashboard`` row in the command reference no longer says "POSIX
  terminals only" — explicitly lists macOS / Linux / Windows 10+.

### What still requires the right environment

- ``/dashboard`` needs a TTY (any OS — bare Python script invocations
  without a console are refused with exit code 2, same as before).
- The OS keyring backend on Windows is the built-in Credential
  Manager via the ``keyring`` package — no extra install needed.

## 0.4.3 — Audit follow-up #2: data quality, rate limits, heartbeat resilience

Wraps the six pen-test follow-ups from the 0.4.x audit:

- **`/tier` shows correct numbers again.** The command was reading
  ``sim_profile_quota`` / ``live_profile_quota`` / ``credential_quota``
  / ``max_book_usd`` — keys the server never sent — so every value
  rendered as ``?``. Switched to the real server keys
  (``max_active_profiles``, ``max_live_profiles``, ``max_credentials``,
  ``max_book_usd_per_profile``) and added ``allowed_exchanges`` +
  ``live_mode_enabled`` rows. ``None`` (unlimited) now renders as ``∞``.
- **`/show` heartbeat row now distinguishes CLI vs server cycle.**
  ``last_app_version`` is either a semver (CLI heartbeat) or the
  literal ``"server"`` (Celery-driven). The row was just dumping it
  raw; now it labels the source explicitly.
- Web ``positions.html`` mirrors the same change.

Server-side hardening:

- **`_capture_server_heartbeat` mirrors `last_app_outbound_ip`** from
  the credential's last-validated IP, so the web dashboard's "Last IP"
  column has something to render for server-mode rows (was blank).
- **Per-call timeout on heartbeat fetch.** The vnquants client's
  ``get_*_account_info`` had no timeout parameter, so a hung exchange
  could burn the cycle's whole 600 s soft-time-limit before the wrap
  caught it. Now wrapped in a ``ThreadPoolExecutor.submit`` with a
  20 s ``result(timeout=...)`` budget. Failure path logs
  ``heartbeat_fetch_timeout=20s`` instead of stalling.
- **Bulk device-token endpoints rate-limited.** ``/devices/bulk/``,
  ``/devices/revoke-all/``, and ``/devices/purge-revoked/`` now cap at
  10 / 3 / 10 calls per user per minute respectively via Django cache
  ``incr``. Damage is bounded to the caller's own tokens, but limits
  the blast radius if a session is stolen.
- **Hard-delete confirmation uses Unicode NFC + trim.** Profile names
  are still validated server-side to ASCII alnum + ``_-``, but the
  JS prompt normalises both sides before comparison so future
  validator relaxations don't silently let through whitespace- or
  encoding-only mismatches.

Tests: 132 pass (75 SDK + 57 server). Two stale tier-quota tests
updated to match the new ``FREE = 1 sim profile`` business rule
introduced in 0.3.

Docs: ``/trading/setup/`` gains a "What's new in 0.4" hero
(``/dashboard``, ``/order``, ``/tape``, friendly diagnostics) plus
two new sections in the command reference covering the live TUI and
manual-trading slashes.

## 0.4.2 — `/dashboard` crash fix: tolerate string-typed monetary fields

Hot-fix for ``/dashboard`` crashing with::

    Error: '>=' not supported between instances of 'str' and 'int'

The server's ``/trading/profiles/`` serialises ``last_app_wallet_usd``
and ``last_app_unrealized_pnl`` as Decimal-stringified JSON
(``"37006.10"``), and ``last_app_positions[*]`` carries Decimal
fields the same way. Two issues:

- The profile + position panels were doing ``(upnl or 0) >= 0``, which
  TypeError'd against ``"7.74"``.
- The dashboard was reading the wrong heartbeat keys
  (``notional_usd`` / ``upnl`` / ``pnl_pct``) — production stores them
  as ``signed_notional_usd`` / ``unrealized_pnl_usd`` /
  ``unrealized_pnl_pct``.

Both fixed; a small ``_as_float`` helper centralises the coercion so
every numeric comparison and ``%f`` format goes through one safe path.
Added a regression test that feeds the production payload shape into
every panel builder.

## 0.4.1 — Audit follow-up: bug-fix sweep on the 0.4.0 features

A professional-tester pass over Phase 2.1 + 2.2 surfaced several
P0/P1 issues. All listed bugs are fixed; the existing 0.4.0 surface
is unchanged.

### Security / correctness (P0)

- **`/dashboard` was hitting the wrong URL for positions.** The poll
  thread called ``/api/v1/talyxion/trading/profiles/<pk>/positions/api/``
  which is the *web* URL (session-auth), not on the API surface — every
  request 404'd and was silently swallowed, so the positions pane was
  always empty. Positions now come from ``last_app_positions`` on the
  ``/trading/profiles/`` response (the heartbeat snapshot), normalised
  to the layout's expected shape including a derived mark price and
  PnL %.
- **`/order place SYM buy 100 --qty` (market order) silently
  mis-priced the order.** The conditional read
  ``amount_dec if not as_qty else amount_dec`` was a typo (both
  branches identical) so passing ``--qty`` for a market order routed
  the qty value into the adapter's ``usd_amount`` parameter as if it
  were notional. The CLI now refuses the combination and tells the
  user to convert qty → USD themselves.
- **`tape --follow` always 400'd after the first poll.** ISO
  timestamps contain ``+`` for the timezone offset; without
  URL-encoding the server's ``urlencoded`` reader saw it as a space
  and rejected the request with ``HTTP 400 bad_since``. Both
  ``/tape --follow`` and ``/dashboard`` now ``urllib.parse.quote``
  the ``since=`` value.
- **`dashboard.py` imported ``termios`` / ``tty`` at module top**, so
  on Windows the entire REPL failed to load (repl.py imports
  dashboard.py). Imports are now guarded; calling ``/dashboard`` on
  Windows prints a clear "not yet supported" message instead of
  crashing the REPL.

### Robustness (P1)

- **Manual-order audit no longer blocks the REPL.** ``_record_audit``
  now POSTs in a daemon thread so a slow or 404'ing endpoint never
  stalls the user after their order has already landed at the
  exchange. The audit payload also caps ``raw_response`` to ~4 KB.
- **Server-side input validation tightened** on
  ``POST /trading/profiles/<pk>/orders/manual/``: ``side`` is now an
  enum (``buy|sell|long|short``); ``usd_amount`` must be ≥ 0; symbol
  must be printable; oversize ``raw_response`` (>8 KB) is replaced
  with a ``{_truncated: true}`` marker so a single row can't bloat
  the DB.
- **Fat-finger guard on ``/order place``.** Mainnet orders ≥ $10k
  USD prompt the user to re-type the amount before submitting; a
  mismatch aborts. Testnet bypasses the prompt.
- **``cancel_order`` treats Binance ``-2011 Unknown order sent`` as
  a no-op success.** Cancelling an order that was already filled or
  cancelled returned an error before, now it just returns True (the
  caller's intent is satisfied).

### Tests

Added focused coverage:
- ``TestManualOrderAudit`` — happy path, idempotency, cross-user
  IDOR, bad side / negative amount, raw_response cap.
- ``TestProfileOrdersSince`` — strict-after filter behaviour and
  malformed timestamp returns 400.
- CLI unit tests for the ``since=`` URL-encoding contract and the
  Windows-import path of ``dashboard.py``.

Total: 75 SDK tests + 35 server tests pass.

## 0.4.0 — Live `/dashboard` + manual order management

Two headline additions toward a Bloomberg-Terminal-style workflow.

### `/dashboard` — htop-style live portfolio TUI

A single slash command takes over the terminal with a full-screen Rich
Layout that refreshes every 2 seconds:

- Header strip with account / tier / daemon pid / CLI version + a
  "last updated N s ago" timestamp.
- Profiles pane (left) — name, exchange, mode, status, wallet, uPnL,
  with archived/paused rows dimmed.
- Positions pane (right) — symbol, side, qty, entry, mark, uPnL, %.
- Recent-fills tape (bottom) — newest first, scrolls automatically.
- Footer with keybinds.

Keybinds: ``q`` quit · ``r`` force-refresh now · ``a`` toggle archived
rows · ``/`` drop back to the REPL · Ctrl-C also exits. Terminal mode
is restored even on crash.

Data sources are the existing REST endpoints (``GET /trading/profiles/``,
``GET /trading/profiles/<pk>/positions/api/``, ``GET /trading/profiles/<pk>/orders/?since=...``).
Phase 2.3 will swap the polling thread for a WebSocket subscription so
fills land instantly.

### `/order` + `/tape` — manual trading without the cycle dispatcher

Place / cancel / list orders directly from the REPL using the
credential bound to a profile. Bypasses the alpha runner entirely —
the CLI signs the request locally and ships it to the exchange.

- ``/order place SYMBOL buy|sell AMOUNT [--limit PRICE] [--qty] [--profile ID]``
  market orders take USD notional by default; pass ``--qty`` to use
  base-currency units; ``--limit`` switches to a limit order.
- ``/order cancel ORDER_ID --symbol SYMBOL [--profile ID]`` cancels one
  pending order by exchange order id.
- ``/order list [--symbol X] [--profile ID]`` prints the pending-orders
  table for one profile.
- ``/order cancel-all [--symbol X] [--profile ID]`` confirms then
  cancels every pending order (optionally restricted to one symbol).
- ``/tape [--profile ID] [--symbol X] [--follow]`` prints recent fills
  for a profile, with ``--follow`` keeping the stream live by polling
  the new ``?since=`` filter every 2 s.

Manual orders post a fire-and-forget audit record to
``/trading/profiles/<pk>/cycle-report/`` with ``manual=true`` so they
show up in ``/tape`` alongside cycle-driven fills (server discards the
record silently if it doesn't support the flag yet — newer deployment
will pick them up automatically).

### Exchange adapter additions

The ``ExchangeAdapter`` ABC gained three optional methods, with stub
implementations that raise ``NotImplementedError`` so existing adapters
keep working until they opt in:

- ``create_limit_order(symbol, side, qty, price, client_order_id, time_in_force)``
- ``cancel_order(symbol, order_id|client_order_id)``
- ``fetch_open_orders(symbol=None)``

``BinanceAdapter`` implements all three (spot + USDⓢ-M futures).

### Server change

- ``GET /api/v1/talyxion/trading/profiles/<pk>/orders/`` now accepts a
  ``?since=<iso-timestamp>`` filter — used by ``/tape --follow`` and
  ``/dashboard`` to fetch only events newer than the last poll.

## 0.3.4 — Friendly errors for `/balance`, `/positions`, `/portfolio`, `/profiles`, `/show`

Every visibility command used to print a bare ``Request failed: Client
error '404 Not Found' for url '...'`` whenever the server replied with
anything other than 200. Now each error category has its own panel:

- **404** — names "CLI newer than server" as the most likely cause,
  hints at ``/doctor`` to confirm version skew, and relays whatever
  body the server sent back (Django's ``{"detail":"..."}``).
- **403** — points at ``/tier`` to check what the current
  subscription tier covers.
- **429** — suggests a backoff window, includes any rate-limit message
  the server attached.
- **5xx** — encourages a retry and notes that ``/doctor`` can confirm
  the server is reachable.
- **ConnectError / ConnectTimeout** — mentions VPN / connectivity and
  the ``TALYXION_BASE_URL`` env var as the next thing to verify.
- **ReadTimeout** — same panel as 5xx with a retry recommendation.

``/doctor`` is unchanged externally but now uses the same explainer
under the hood, so its FAIL rows show the same diagnostic headline
instead of "request failed".

## 0.3.3 — Actionable diagnostics for `/add` rejections

- **`/add` no longer prints a bare ``401 unauthorized``.** Binance's
  401/403 responses always carry a JSON body with a specific error
  code (``-2014`` malformed key, ``-2015`` ambiguous "key/IP/perms",
  ``-1022`` signature mismatch, ``-1021`` clock skew, etc.). The CLI
  now surfaces the body verbatim — that single line tells you whether
  the secret was typed wrong, the clock is off, or the IP isn't
  whitelisted.
- **Diagnostic panel listing the likely fixes.** When validation
  fails, we follow the error with a "What to check" panel that:
  - Detects ``-2014`` / ``-1022`` / ``-1021`` and shows the matching
    one-line fix.
  - On ambiguous ``-2015`` (or unknown 401s), lists the four real
    causes — IP whitelist, market-type mismatch (with the exact
    ``/add binance --market-type=futures`` retry command), mainnet vs
    testnet, missing permission flag — and prints **your current
    outbound IP** so you can copy-paste it into the exchange's
    whitelist.
- **`IPBlocked` errors also show the outbound IP.**

If the web flow at `/trading/credentials/` accepted the key but the
CLI doesn't, the new panel will say so — IP whitelist is the usual
culprit, because the web validates from Talyxion's server IP and the
CLI validates from your home IP.

## 0.3.2 — Live as-you-type slash menu (Claude-Code style)

- **As-you-type dropdown.** The slash menu now opens the moment you
  start typing — no Tab required. Type ``/bal`` and a single
  ``/balance — Wallet snapshot per credential`` row appears under the
  cursor; type ``/log`` and you see ``/login``, ``/logout``, ``/logs``
  with their summaries.
- **Substring + fuzzy matching.** Match works on any substring of the
  command name (``port`` → ``/portfolio``), and a fuzzy layer on top
  catches transposed letters too.
- **Inline descriptions in the menu.** Each suggestion shows its
  one-line summary on the right of the dropdown, identical to the
  ``/help`` overview.
- **History-based ghost text.** After your first session, the REPL
  auto-completes from history — type ``/run`` and last time's flags
  show as dim ghost text; right-arrow accepts.

## 0.3.1 — Did-you-mean suggestions, clickable login URL, data-quality hints

- **Did-you-mean suggestions.** Typos and partial slashes now print
  the closest valid commands instead of just "Unknown command". So
  ``/balanc`` → suggests ``/balance``; ``port`` → suggests
  ``/portfolio``; ``logn`` → suggests ``/login``.
- **Login URL is the primary affordance.** The device-flow panel now
  prints a Cmd/Ctrl-clickable hyperlink (OSC 8 via Rich) front and
  centre, with the verification code below. **Auto-opening the
  browser is off by default** — pass ``--open-browser`` if you still
  want it. Headless/SSH users no longer get a confusing "browser
  opened" message when nothing happened.
- **Data-quality hints on missing values.** ``/balance`` and
  ``/portfolio`` used to render plain ``—`` whenever a field was
  missing, which was indistinguishable from "API errored", "no
  heartbeat yet", and "validation pending". Each missing cell now
  spells out the actual reason, plus a footer line listing rows that
  need attention (e.g. ``#85 binance/main: validation pending``).
  Drawdown specifically tells you whether it's missing because no
  heartbeat has been received, or because the first cycle hasn't
  completed yet.

## 0.3.0 — Interactive REPL, slash commands, `talyxion-runner`

**Breaking — argv subcommands are removed from the shell.** The CLI is
now an interactive REPL:

- Running ``talyxion`` in a TTY drops you straight into a persistent
  prompt with banner, tab-completion, command history, and a bottom
  toolbar showing tier / token / daemon status.
- First-run automatically opens the OAuth device-flow — no separate
  ``auth login`` step. Token lands in your OS keychain as before.
- Every command is a slash: ``/login``, ``/logout``, ``/status``,
  ``/add binance --testnet``, ``/creds``, ``/remove``, ``/profiles``,
  ``/show``, ``/positions``, ``/portfolio``, ``/balance``, ``/run``,
  ``/runstate``, ``/logs``, ``/whoami``, ``/tier``, ``/doctor``,
  ``/update``. ``/help`` lists everything; ``/help /add`` shows the
  flags for one command.
- ``exit`` / ``quit`` / ``Ctrl-D`` leaves the REPL. ``Ctrl-C`` cancels
  the current input without dropping you out.
- Non-TTY invocations (pipes, cron, CI) get a friendly error and
  exit 2 — they should use the new ``talyxion-runner`` binary instead.

**New: ``talyxion-runner`` headless daemon.** Drop-in entry for
systemd, launchd, and NSSM. Same cycle loop as before, no REPL:

```
talyxion-runner [--once] [--profile ID] [--dry-run] [--background]
```

Authenticate once by running ``talyxion`` in a TTY and typing
``/login`` — both binaries share the same OS-keychain token.

**Other changes:**

- ``/run -d`` (background) now spawns ``talyxion-runner`` instead of
  re-exec'ing the REPL, which would refuse a non-TTY parent.
- Added ``prompt_toolkit>=3.0`` to the ``[cli]`` extras.
- Web onboarding (`/trading/setup/`) rewritten to advertise the new
  flow: ``pip install talyxion[cli]`` → ``talyxion`` → ``/add`` → ``/run``.
- Internal: existing Click handlers are unchanged — the REPL
  dispatcher translates slash commands into argv and reuses them, so
  every flag stays in lockstep with the documented spec.

**Migration:** any script that called ``talyxion auth login`` /
``talyxion run`` etc. directly must switch to:
- Interactive use → launch ``talyxion`` and type the slash equivalent.
- Service managers → ``talyxion-runner`` (see ``/trading/setup/`` for
  systemd / launchd / NSSM recipes).

## 0.2.8 — `portfolio`, `balance`, `whoami`, `tier`, `show`, `doctor`

New top-level visibility commands. All read-only, all powered by
existing server APIs:

- ``talyxion portfolio`` — aggregate wallet / uPnL / notional with
  drawdown vs peak. ``--by exchange|profile|mode|symbol`` slices the
  breakdown table differently; ``--json`` prints raw JSON for piping
  into jq.
- ``talyxion balance`` — wallet rolled up per exchange credential
  (one row per API key, regardless of how many profiles share it).
  Flags any key still carrying ``canWithdraw=true`` as a ⚠ warning.
- ``talyxion show <id|name>`` — full drill-down on one profile:
  config, credential metadata, last heartbeat, and every open position.
- ``talyxion whoami`` — account info as the server sees it (email,
  tier, device-token label/prefix, billing status, tier_caps bundle,
  local profile ids bound to this token).
- ``talyxion tier`` — current tier with quota usage (sim/live
  profiles used vs allowed, credentials, leverage cap, min cycle,
  max book size). Answers "can I create another live profile?".
- ``talyxion doctor`` — self-check (keychain, DNS, whoami, profiles,
  credentials, withdraw audit). Exits 0 on PASS — run this before
  opening an issue.

All of the above are also discoverable in ``talyxion --help`` and
documented at https://talyxion.com/trading/setup/.

## 0.2.7 — `list profiles` shows server-side rows, new `positions` command

- ``talyxion list profiles`` now defaults to ``--scope all`` so the CLI
  listing matches the web UI. Server-side profiles
  (``execution_mode=server``) are shown read-only with a magenta ``Exec``
  tag — they execute via the Talyxion Celery dispatcher and can't be
  ``run`` from the CLI. Pass ``--scope local`` to filter back to only
  the rows this CLI manages, or ``--scope archived`` to include archived.
- New ``talyxion positions`` (alias of ``talyxion list positions``).
  Renders open positions per profile from the last heartbeat snapshot:
  symbol, side, qty, entry, mark, notional, uPnL, plus a footer total
  across all profiles. No exchange API calls — pure server snapshot.
  ``--profile <name|id>`` filters to one desk; ``--scope local|all``
  matches the profile-list flag.
- ``list profiles`` table gains Wallet + uPnL columns (color-coded) and
  a per-execution-mode legend at the bottom.
- Server API ``GET /trading/profiles/`` now honours ``?include=local``
  (default for back-compat), ``?include=all``, or ``?include=archived``,
  and returns ``execution_mode`` + ``last_app_positions`` on every row.

## 0.2.6 — CRITICAL: honor profile.mode=simulation

- **SECURITY/SAFETY FIX**. Previous releases (0.2.0–0.2.5) submitted
  real exchange orders even when the profile was configured with
  ``mode=simulation`` in the web UI. Only the CLI's ``--dry-run`` flag
  prevented order submission. Real funds may have moved if a user
  trusted the "Simulation" web-UI label.
  Fix: the runner now treats ``profile.mode == "simulation"`` as an
  implicit ``--dry-run`` for that cycle. Balance + positions are still
  fetched so the heartbeat/drawdown gate remains accurate, but
  ``create_market_order()`` is skipped.
- All users on ≤0.2.5: upgrade immediately, or always pass
  ``--dry-run`` until upgrade.

## 0.2.5 — Force-run on explicit `--once` / `--profile`

- ``talyxion run --once`` and ``talyxion run --profile <id>`` now bypass
  the schedule check. They're explicit, debug-oriented invocations —
  running just one cycle right now is the point. Before this patch, if
  a prior cycle had scheduled the next attempt 10 minutes out, ``--once``
  would silently skip. Steady-state daemon (``talyxion run`` with no
  flags) still honours the schedule.

## 0.2.4 — Runner observability fixes

- ``talyxion status`` now reads auth metadata (email/tier/label) from
  the OS keyring (where `auth login` actually saves it) instead of
  state.json, so the header line no longer prints ``Auth: ? (?)``
  after a successful login.
- Cycle runner early-return paths (credential conflict, segment
  data-error, missing local credential) now persist
  ``last_outcome`` + ``last_error`` + ``last_cycle_at`` before
  bailing — previously the counter would tick up but ``status``
  showed the outcome column empty, making it hard to tell what
  went wrong without tailing the log.
- New ``Last error`` column in ``talyxion status`` surfacing the most
  recent failure reason (e.g. "no local keyring entry for
  binance:binance_copy — run `talyxion add binance --label
  binance_copy`").

## 0.2.3 — Relax withdraw gate on testnet

- `talyxion add <exchange> --testnet` no longer rejects keys with
  `canWithdraw=true`. Testnet exchanges (testnet.binance.vision et al)
  always grant withdraw because there's no real-fund flow to toggle
  off — refusing them blocked all testnet onboarding. CLI now warns,
  rewrites the permission payload to `canWithdraw=false` before
  registering, and prompts the user to re-validate without --testnet
  before going live. Mainnet keys with withdraw enabled remain a
  hard reject.

## 0.2.2 — Graceful keyring failures

- Catch macOS Keychain `-25308 errSecInteractionNotAllowed` (locked or
  no UI session) + Linux Secret-Service locked errors + Windows
  Credential Manager unavailability. The CLI now prints a clear
  remediation message instead of a raw Python traceback.
- New ``KeyringUnavailable`` exception type in
  ``talyxion.cli.keyring_store`` for callers that want to catch it.
- Server-side: ``BotAccessControlMiddleware.PUBLIC_NONBROWSER_PREFIXES``
  now allowlists ``/api/v1/talyxion/auth/device/`` and
  ``/api/v1/talyxion/trading/`` so the CLI's device-token Bearer doesn't
  get rejected by the ``ApiAccessKey``-only middleware. Per-endpoint
  auth still enforced via ``@device_token_required`` at the view layer.

## 0.2.1 — Cloudflare-friendly User-Agent

- Wrap HTTP requests in a ``Mozilla/5.0 (compatible; talyxion-cli/…)``
  User-Agent so the unauth device-flow endpoints don't trip Cloudflare
  bot-fight WAF (was returning 403 to ``python-httpx/x.y.z``). The CLI
  build identifier remains in the UA string + ``X-App-Version`` header
  for our own analytics.

## 0.2.0 — Trader CLI

- **NEW** `talyxion` command-line tool — thin-client trader that runs
  Talyxion alphas with the user's own exchange API keys and IP. Keys
  stay on the user's machine (OS keyring); the server only stores the
  SHA-256 fingerprint + permission flags.
- OAuth 2.0 Device Authorization Grant (RFC 8628) for pairing — no
  copy-paste of tokens.
- Native Binance REST adapter (Spot + USD-M Futures). No ccxt dep.
- Local risk gates: drawdown halt, position-size clamp, symbol
  blocklist, withdraw-permission refusal (belt + braces against
  server-side enforcement).
- Idempotent cycle reports + heartbeats; archived-on-server profiles
  auto-stop on the next loop iteration.
- ``[cli]`` optional dependency group — `pip install talyxion[cli]`.
- Commands: `auth login|logout|status`, `add`, `remove`,
  `list profiles|creds`, `run [--once|--profile|--dry-run]`,
  `status`, `logs`, `update`.

## 0.1.0 — initial release

- Sync `Talyxion` client with API key auth.
- Resources: `signals`, `screener`, `datafields`, `ticker`, `rates`, `simulations`, `status`.
- Streaming: `sim_progress`, `feed_events` via WebSocket.
- Pydantic v2 models with optional pandas conversion.
- Typed exceptions mapped from backend error codes.
- Built-in retry with exponential backoff for 5xx + connection errors.
