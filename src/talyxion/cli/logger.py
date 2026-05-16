"""CLI rotating log file at ``~/.talyxion/talyxion.log`` (10 MB × 5).

The runner streams INFO+ output here; ``talyxion logs`` tails it.
Sensitive values (api_key, api_secret, raw token, Authorization header)
are scrubbed by a regex filter before writing.
"""
from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

import platformdirs

LOG_FILENAME = "talyxion.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

# Patterns that must never reach disk.
_REDACT_PATTERNS = [
    re.compile(r'(Bearer\s+)[\w\-]+', re.I),
    re.compile(r'("?api[_-]?key"?\s*[:=]\s*")([^"]+)"', re.I),
    re.compile(r'("?api[_-]?secret"?\s*[:=]\s*")([^"]+)"', re.I),
    re.compile(r'("?secret"?\s*[:=]\s*")([^"]+)"', re.I),
    re.compile(r'("?password"?\s*[:=]\s*")([^"]+)"', re.I),
    re.compile(r'("?passphrase"?\s*[:=]\s*")([^"]+)"', re.I),
]


class _Scrubber(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        for pat in _REDACT_PATTERNS:
            msg = pat.sub(lambda m: m.group(1) + "***REDACTED***" + (m.group(0)[-1] if m.group(0).endswith('"') else ""), msg)
        record.msg = msg
        record.args = ()
        return True


def log_dir() -> Path:
    p = Path(platformdirs.user_log_dir("talyxion"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_path() -> Path:
    return log_dir() / LOG_FILENAME


_configured = False


def get_logger(verbose: bool = False) -> logging.Logger:
    """Return the singleton CLI logger, configured on first call."""
    global _configured
    log = logging.getLogger("talyxion.cli")
    if _configured:
        log.setLevel(logging.DEBUG if verbose else logging.INFO)
        return log

    log.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.propagate = False

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(
        log_path(),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(fmt)
    fh.addFilter(_Scrubber())
    log.addHandler(fh)

    _configured = True
    return log
