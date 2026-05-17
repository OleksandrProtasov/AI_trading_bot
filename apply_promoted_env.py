"""Apply promoted EV parameters from reports into .env (safe key allowlist)."""
from __future__ import annotations

import argparse
from pathlib import Path

from core.research_artifacts import EV_ENV_KEYS, merge_env_file, parse_env_file
from core.runtime_paths import repo_root


def main() -> None:
    p = argparse.ArgumentParser(description="Merge promoted EV params into .env")
    p.add_argument(
        "--promoted-env-path",
        type=str,
        default="reports/promoted_best.env",
    )
    p.add_argument(
        "--target-env-path",
        type=str,
        default=".env",
        help="Target env file (default: repo .env)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print keys that would be updated without writing",
    )
    args = p.parse_args()

    promoted_path = Path(args.promoted_env_path)
    if not promoted_path.is_absolute():
        promoted_path = repo_root() / promoted_path

    target_path = Path(args.target_env_path)
    if not target_path.is_absolute():
        target_path = repo_root() / target_path

    updates = parse_env_file(promoted_path)
    if not updates:
        print(f"[apply_promoted_env] no updates found in {promoted_path}")
        return

    if args.dry_run:
        keys = sorted(k for k in updates if k in EV_ENV_KEYS)
        print(f"[apply_promoted_env] dry_run keys={keys}")
        return

    updated, appended = merge_env_file(target_path, updates, allowed_keys=EV_ENV_KEYS)
    print(
        "[apply_promoted_env] "
        f"target={target_path} updated={updated} appended={appended}"
    )


if __name__ == "__main__":
    main()
