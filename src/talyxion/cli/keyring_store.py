"""OS-keyring wrapper for the CLI.

Three categories of secrets live in the keyring (never on disk plaintext):

* Device token  → ``service="talyxion"`` username=``"device_token"``
* Exchange creds → ``service=f"talyxion:{exchange}"`` username=``label``
  Stored as JSON: ``{"api_key": "...", "api_secret": "...", "passphrase": "..."}``
* Token metadata → ``service="talyxion"`` username=``"token_meta"``
  JSON ``{user_id, email, prefix, label}`` — convenience cache so the
  CLI can show identity without an extra network call.

We deliberately keep all reads/writes routed through this module so a
future migration (e.g. encrypted file fallback for headless servers
without a Secret Service) only touches one file.
"""
from __future__ import annotations

import json
import platform
import sys
from typing import Any

import keyring  # type: ignore[import-not-found]

SERVICE_ROOT = "talyxion"
DEVICE_TOKEN_USERNAME = "device_token"
TOKEN_META_USERNAME = "token_meta"


class KeyringUnavailable(RuntimeError):
    """The OS keyring rejected the write (locked, no UI session, etc.).

    Raised with a user-actionable message; callers should print and
    exit non-zero rather than re-trying.
    """


def _diagnose_set_password_failure(exc: Exception) -> str:
    """Translate a keyring backend exception into a remediation hint.

    Different OS backends raise different errors:
      * macOS  → ``-25308 errSecInteractionNotAllowed`` when the login
        keychain is locked or the calling process isn't allowed to
        prompt for permission (common in IDE terminals, SSH, tmux).
      * Linux  → ``secretstorage.exceptions.LockedException`` when
        gnome-keyring / KWallet is locked.
      * Windows → ``WinError`` when the Credential Manager is unavailable.

    The CLI doesn't try to auto-fall back to a plaintext file — that
    would silently downgrade security. We let the user choose the fix.
    """
    msg = str(exc)
    osname = platform.system()

    if osname == "Darwin":
        if "-25308" in msg or "InteractionNotAllowed" in msg:
            return (
                "macOS Keychain is locked or this Python process isn't allowed "
                "to prompt for permission.\n\n"
                "Fix one of these:\n"
                "  1. Run in Terminal.app (not VSCode/Cursor/tmux), then retry.\n"
                "  2. Unlock first:  security unlock-keychain ~/Library/Keychains/login.keychain-db\n"
                "  3. Open Keychain Access → File → Unlock Keychain 'login'."
            )
        return (
            "macOS Keychain refused the write. Try:\n"
            "  security unlock-keychain ~/Library/Keychains/login.keychain-db\n"
            f"Underlying error: {msg}"
        )

    if osname == "Linux":
        return (
            "Linux Secret Service (gnome-keyring / KWallet) is unavailable "
            "or locked.\n\n"
            "Fix one of these:\n"
            "  1. Ensure gnome-keyring-daemon is running (most desktops auto-start it).\n"
            "  2. Headless server? Install + use a file backend:\n"
            "     pip install keyrings.alt\n"
            "     then re-run talyxion auth login.\n"
            f"Underlying error: {msg}"
        )

    if osname == "Windows":
        return (
            "Windows Credential Manager is unavailable.\n"
            f"Underlying error: {msg}"
        )

    return f"OS keyring rejected the write: {msg}"


# ── Device token ────────────────────────────────────────────────────────


def save_device_token(raw_token: str, meta: dict[str, Any]) -> None:
    """Persist the raw device token + its metadata.

    Raises :class:`KeyringUnavailable` with an actionable message if the
    OS keyring won't accept the write — callers should print + exit.
    """
    try:
        keyring.set_password(SERVICE_ROOT, DEVICE_TOKEN_USERNAME, raw_token)
        keyring.set_password(SERVICE_ROOT, TOKEN_META_USERNAME, json.dumps(meta))
    except keyring.errors.PasswordSetError as exc:
        # Roll back partial save so a half-written keyring entry doesn't
        # confuse later commands.
        try:
            keyring.delete_password(SERVICE_ROOT, DEVICE_TOKEN_USERNAME)
        except Exception:
            pass
        raise KeyringUnavailable(_diagnose_set_password_failure(exc)) from exc
    except Exception as exc:
        # Other backend errors (LockedException on Linux, RuntimeError on
        # headless, etc.) → same friendly translation.
        raise KeyringUnavailable(_diagnose_set_password_failure(exc)) from exc


def load_device_token() -> str | None:
    return keyring.get_password(SERVICE_ROOT, DEVICE_TOKEN_USERNAME)


def load_token_meta() -> dict[str, Any] | None:
    raw = keyring.get_password(SERVICE_ROOT, TOKEN_META_USERNAME)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def delete_device_token() -> None:
    for username in (DEVICE_TOKEN_USERNAME, TOKEN_META_USERNAME):
        try:
            keyring.delete_password(SERVICE_ROOT, username)
        except keyring.errors.PasswordDeleteError:
            pass


# ── Exchange credentials ────────────────────────────────────────────────


def _cred_service(exchange: str) -> str:
    return f"{SERVICE_ROOT}:{exchange.lower()}"


def save_credential(exchange: str, label: str, payload: dict[str, str]) -> None:
    """Persist exchange API key+secret(+passphrase) for ``label`` on ``exchange``.

    ``payload`` typically: ``{"api_key": "...", "api_secret": "...", "passphrase": "..."}``.
    Anything inside is JSON-serialised; only string values please.
    """
    keyring.set_password(_cred_service(exchange), label, json.dumps(payload))


def load_credential(exchange: str, label: str) -> dict[str, str] | None:
    raw = keyring.get_password(_cred_service(exchange), label)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def delete_credential(exchange: str, label: str) -> None:
    try:
        keyring.delete_password(_cred_service(exchange), label)
    except keyring.errors.PasswordDeleteError:
        pass
