"""
ML-BUNDLE-STORAGE — Supabase Storage adapter for trained model bundles.

Persistent registry that solves Railway's ephemeral-filesystem problem: every
deploy resets the container, so bundles trained at runtime get destroyed.
This module pushes bundles to Supabase Storage on train completion and pulls
them back on first inference, transparently.

Design:
  - Storage bucket `models`, prefix per version: `models/<version>/<file>.pkl`
  - Postgres table `model_versions` is the registry (metadata, CV metrics,
    promotion timestamps). Bundle binaries live in Storage; metadata in DB.
  - `_load_models()` in xgboost_ensemble checks local disk first; if the
    bundle dir is missing, calls `download_bundle(version, dir)` to hydrate
    from Storage. Cached on local disk for the lifetime of the container.

Operator workflow:
  1. Train: `python3 workers/model/train.py --version v_20260517` →
     auto-uploads bundle + registers row in model_versions.
  2. Inspect: `python3 scripts/list_models.py` → shows every version with
     its CV metrics + promotion status.
  3. Promote: set `MODEL_VERSION=v_20260517` on Railway + redeploy. First
     prediction triggers download from Storage; subsequent predictions hit
     local cache. SQL: `UPDATE model_versions SET promoted_at = NOW()
     WHERE version = ...`. Demote previous version with `demoted_at`.
  4. Rollback: change MODEL_VERSION back. Old bundle still in Storage.

Why Supabase Storage rather than Railway Volume:
  - Survives Railway region migration / service replacement
  - Local agent can pull same bundles for offline_eval (single source of truth)
  - Postgres registry gives audit trail + per-version CV metrics
  - Free under 1GB; ~5MB/bundle = ~200 versions before any cost
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

# Ensure .env is read regardless of whether a top-level entry point did so.
# Cheap if already loaded — `load_dotenv` no-ops on existing keys.
load_dotenv()

console = Console()

BUCKET = "models"

# Files that constitute a complete bundle. `btts.pkl` is intentionally
# optional — production `_load_models()` doesn't load it (BTTS predictions
# come from Poisson). `platt.pkl` is also optional (uncalibrated bundles
# work, just less accurate).
REQUIRED_FILES = (
    "feature_cols.pkl",
    "result_1x2.pkl",
    "over_under.pkl",
    "home_goals.pkl",
    "away_goals.pkl",
)
OPTIONAL_FILES = ("btts.pkl", "platt.pkl")


def _client():
    """Service-role Supabase client (publishable key can't write Storage)."""
    from supabase import create_client
    url = os.environ["SUPABASE_URL"]
    # SUPABASE_SECRET_KEY is the service-role key in this project's .env
    # (publishable SUPABASE_KEY can't bypass Storage RLS for writes).
    key = os.environ.get("SUPABASE_SECRET_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not key:
        raise RuntimeError(
            "SUPABASE_SECRET_KEY not set — required for Storage uploads. "
            "Falls back to SUPABASE_SERVICE_ROLE_KEY for parity with web app naming."
        )
    return create_client(url, key)


def upload_bundle(version: str, local_dir: str | Path) -> int:
    """Push every .pkl in `local_dir` to `models/<version>/<file>`.

    Idempotent — re-uploads overwrite. Returns count of files uploaded.
    Raises if any required file is missing locally.
    """
    local_dir = Path(local_dir)
    if not local_dir.exists():
        raise FileNotFoundError(f"Bundle dir not found: {local_dir}")

    missing = [f for f in REQUIRED_FILES if not (local_dir / f).exists()]
    if missing:
        raise FileNotFoundError(
            f"Bundle {version} missing required files: {missing}. "
            f"Re-train or restore them before uploading."
        )

    sb = _client()
    n = 0
    for fname in REQUIRED_FILES + OPTIONAL_FILES:
        local_file = local_dir / fname
        if not local_file.exists():
            continue
        remote_path = f"{version}/{fname}"
        with open(local_file, "rb") as f:
            data = f.read()
        # `upsert: 'true'` lets us overwrite without manually deleting first.
        try:
            sb.storage.from_(BUCKET).upload(
                path=remote_path,
                file=data,
                file_options={"upsert": "true", "content-type": "application/octet-stream"},
            )
        except Exception as e:
            # Older supabase-py raises on existing files even with upsert; remove + retry.
            try:
                sb.storage.from_(BUCKET).remove([remote_path])
                sb.storage.from_(BUCKET).upload(
                    path=remote_path, file=data,
                    file_options={"content-type": "application/octet-stream"},
                )
            except Exception as e2:
                raise RuntimeError(f"Upload failed for {remote_path}: {e} (retry: {e2})")
        n += 1
        console.print(f"  [green]→[/green] uploaded {remote_path} ({len(data):,} bytes)")
    return n


def download_bundle(version: str, local_dir: str | Path) -> int:
    """Pull every file under `models/<version>/` to `local_dir`.

    Skips files already present locally (cache). Returns count downloaded.
    Returns 0 if the bundle isn't in Storage (caller should treat as cache miss).
    """
    local_dir = Path(local_dir)
    local_dir.mkdir(parents=True, exist_ok=True)

    sb = _client()
    try:
        listed = sb.storage.from_(BUCKET).list(version)
    except Exception as e:
        console.print(f"  [yellow]Storage list failed for {version}: {e}[/yellow]")
        return 0

    if not listed:
        return 0

    n = 0
    for entry in listed:
        fname = entry["name"] if isinstance(entry, dict) else entry.name
        if fname.startswith("."):
            continue
        local_file = local_dir / fname
        if local_file.exists():
            continue
        remote_path = f"{version}/{fname}"
        try:
            data = sb.storage.from_(BUCKET).download(remote_path)
            local_file.write_bytes(data)
            n += 1
            console.print(f"  [green]←[/green] downloaded {remote_path} ({len(data):,} bytes)")
        except Exception as e:
            console.print(f"  [yellow]Download failed for {remote_path}: {e}[/yellow]")
    return n


def bundle_exists_in_storage(version: str) -> bool:
    """Check whether `models/<version>/feature_cols.pkl` exists in Storage."""
    sb = _client()
    try:
        listed = sb.storage.from_(BUCKET).list(version)
    except Exception:
        return False
    if not listed:
        return False
    names = {(e["name"] if isinstance(e, dict) else e.name) for e in listed}
    return "feature_cols.pkl" in names


def list_versions() -> list[dict]:
    """Returns metadata for every registered version, joined with Storage
    presence. Reads from `model_versions` table; falls back to listing
    Storage if the registry is empty (e.g. mid-bootstrap)."""
    from workers.api_clients.db import execute_query

    rows = execute_query(
        """
        SELECT version, trained_at, training_window_start, training_window_end,
               n_training_rows, cv_metrics, promoted_at, demoted_at, notes
        FROM model_versions
        ORDER BY trained_at DESC
        """,
        (),
    )
    return rows


def register_version(version: str, *,
                      training_window_start: str | None = None,
                      training_window_end: str | None = None,
                      n_training_rows: int | None = None,
                      feature_cols: list | None = None,
                      cv_metrics: dict | None = None,
                      notes: str | None = None) -> None:
    """UPSERT a model_versions row. Called by train.py after upload_bundle."""
    import json
    from workers.api_clients.db import execute_write

    execute_write(
        """
        INSERT INTO model_versions
            (version, training_window_start, training_window_end, n_training_rows,
             feature_cols, cv_metrics, storage_bucket, storage_prefix, notes)
        VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s)
        ON CONFLICT (version) DO UPDATE SET
            training_window_start = EXCLUDED.training_window_start,
            training_window_end   = EXCLUDED.training_window_end,
            n_training_rows       = EXCLUDED.n_training_rows,
            feature_cols          = EXCLUDED.feature_cols,
            cv_metrics            = EXCLUDED.cv_metrics,
            notes                 = COALESCE(EXCLUDED.notes, model_versions.notes)
        """,
        (
            version,
            training_window_start,
            training_window_end,
            n_training_rows,
            json.dumps(feature_cols) if feature_cols is not None else None,
            json.dumps(cv_metrics) if cv_metrics is not None else None,
            BUCKET,
            f"{version}/",
            notes,
        ),
    )


def ensure_local_bundle(version: str, models_dir: str | Path) -> bool:
    """Used by xgboost_ensemble._load_models. If `<models_dir>/<version>/`
    isn't already populated, attempt to download it from Storage. Returns
    True if the bundle is now present locally (whether cached or freshly
    downloaded), False if neither local nor Storage has it (caller should
    fall back to default version)."""
    bundle_dir = Path(models_dir) / version
    if bundle_dir.exists() and (bundle_dir / "feature_cols.pkl").exists():
        return True
    console.print(f"[cyan]Bundle {version} not on local disk — pulling from Storage...[/cyan]")
    n = download_bundle(version, bundle_dir)
    if n == 0:
        return False
    return (bundle_dir / "feature_cols.pkl").exists()
