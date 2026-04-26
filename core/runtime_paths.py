"""Resolve paths relative to the repository root (works regardless of cwd)."""
from __future__ import annotations

from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolved_database_path() -> str:
    """Same SQLite file as the main bot, even if cwd is not the repo root."""
    try:
        from config import config

        p = Path(config.database.db_path)
        if not p.is_absolute():
            p = repo_root() / p
        return str(p)
    except Exception:
        return str(repo_root() / "crypto_analytics.db")
