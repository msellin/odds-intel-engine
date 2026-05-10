# ML Model Registry — Architecture & Reusable Design

> **Purpose**: durable, versioned storage for ML model bundles with weekly-or-faster
> retraining cadence, when your runtime is on an ephemeral platform (Railway, Fly.io,
> Vercel, Heroku, Cloud Run, etc) and you don't want to stand up MLflow/SageMaker.
>
> **Status**: shipped 2026-05-10 in this repo. Reusable in any project that already
> uses Supabase or has S3-compatible object storage available.

## The problem this solves

Modern PaaS containers (Railway included) are immutable. Every deploy resets the
filesystem. So if you train a model bundle inside the running container — or use a
weekly retrain cron that writes `data/models/v_2026_05_17/*.pkl` — those files
**get deleted on the very next deploy**. Worse, if your code references them by
filename (`MODEL_VERSION=v_2026_05_17`), the loader silently falls back to defaults
and you don't notice for days.

Standard answers people reach for, and why none are great:
- **Persistent volume**: vendor-locked, doesn't sync to local dev for offline eval, can't be accessed from CI.
- **Commit bundles to git**: bloats the repo (5MB × 52 weeks × N years = hundreds of MB), git operations slow down, hits LFS or repo size limits.
- **Train on every deploy**: 10-15 minutes added to every deploy; can't ship without training succeeding; can't easily test old versions.
- **Bake into Docker image**: image grows monotonically with every retrain; CI/CD slows; can't roll back without rebuilding.
- **MLflow / SageMaker / W&B**: real model registry, but add a service to manage and a learning curve. Overkill if you have one model family.

## The architecture

Two pieces, both leaning on infrastructure you almost certainly already have:

```
                ┌────────────────────────────────────┐
                │  Postgres (e.g. Supabase)          │
                │  ┌──────────────────────────────┐  │
                │  │ model_versions table         │  │
                │  │  - version, trained_at,      │  │
                │  │  - cv_metrics, promoted_at,  │  │
                │  │  - storage_prefix, …         │  │
                │  └──────────────────────────────┘  │
                └────────────────────────────────────┘
                              │ metadata
                              │
                              ▼
            ┌──────────────────────────────────────┐
            │ Object storage (Supabase Storage,    │
            │ S3, R2, GCS — anything with PUT/GET) │
            │   models/                            │
            │     v_2026_05_17/feature_cols.pkl    │
            │     v_2026_05_17/result_1x2.pkl      │
            │     v_2026_05_24/...                 │
            └──────────────────────────────────────┘
                              │ binaries
                              │
              ┌───────────────┴────────────────┐
              ▼                                ▼
    ┌──────────────────┐           ┌──────────────────────────┐
    │ Local dev / CI   │           │ Production runtime       │
    │  - run train.py  │           │  - on first prediction:  │
    │  - run eval.py   │           │    1. check local disk   │
    │  - same bundles  │           │    2. else download      │
    │    as prod       │           │    3. cache for lifetime │
    └──────────────────┘           │       of container       │
                                   └──────────────────────────┘
```

**Two writes on training, two reads on serving.** That's it.

### The registry table

```sql
CREATE TABLE model_versions (
    version                TEXT PRIMARY KEY,
    trained_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    training_window_start  DATE,
    training_window_end    DATE,
    n_training_rows        INTEGER,
    feature_cols           JSONB,        -- list of column names model expects
    cv_metrics             JSONB,        -- {1x2: {log_loss, acc}, over_25: {...}}
    storage_bucket         TEXT NOT NULL DEFAULT 'models',
    storage_prefix         TEXT NOT NULL,  -- e.g. 'v_2026_05_17/'
    promoted_at            TIMESTAMPTZ,    -- set when this version becomes primary
    demoted_at             TIMESTAMPTZ,    -- set when superseded
    notes                  TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Why every column matters:**
- `version` is your only identifier. Use a date-stamp like `v_YYYY_MM_DD` or a content hash. **Don't reuse names** — that's how rollbacks get destroyed.
- `cv_metrics` is the bridge between training output and operator decision-making. Without it you have no way to compare candidates without re-running eval.
- `feature_cols` is denormalized but invaluable: catches schema drift between what was trained and what's available at inference.
- `promoted_at` / `demoted_at` give you an audit trail. *"What model was actually live last Tuesday at 14:00 UTC when bot X lost €40?"* — answerable from this table alone.
- `storage_prefix` decouples the registry from the storage layout. Move buckets without re-keying rows.

### The storage layout

One prefix per version. All files for that version live under it. No file-naming conventions buried in code:

```
models/
    v9a_202425/
        feature_cols.pkl
        result_1x2.pkl
        over_under.pkl
        home_goals.pkl
        away_goals.pkl
    v_2026_05_17/
        feature_cols.pkl
        ...
        platt.pkl          # optional — calibration
```

**Required vs optional files** is a contract. Document it in `storage.py`:
```python
REQUIRED_FILES = ("feature_cols.pkl", "result_1x2.pkl", "over_under.pkl",
                  "home_goals.pkl", "away_goals.pkl")
OPTIONAL_FILES = ("btts.pkl", "platt.pkl")
```
Loader fails fast if required missing; skips optional silently.

### The loader's two paths

```python
def _load_models() -> dict:
    if _model_cache:
        return _model_cache              # warm cache

    model_path = MODELS_DIR / MODEL_VERSION
    if not (model_path / "feature_cols.pkl").exists():
        # Cold: not on local disk → pull from Storage
        if not ensure_local_bundle(MODEL_VERSION, MODELS_DIR):
            return {}                    # neither local nor Storage → fall back
    # Now bundle is on disk; load each file
    ...
    return _model_cache
```

**Three states a container can be in:**
1. **Warm** (most predictions): module-level dict cache hit.
2. **Cold but bundle on disk** (after first prediction): joblib loads from disk, fills cache.
3. **Cold and bundle missing** (fresh deploy): `ensure_local_bundle()` downloads from Storage, then loads. **Adds ~1-3s to the first prediction after each deploy.** All subsequent predictions are warm.

### The training-side hook

After `train_all()` writes the bundle to local disk:

```python
upload_bundle(version, output_dir)       # push every .pkl to Storage
register_version(                        # insert metadata row
    version,
    training_window_start=...,
    training_window_end=...,
    n_training_rows=...,
    feature_cols=feature_cols_list,
    cv_metrics={"1x2": {"log_loss": 0.34, "acc": 0.87}},
    notes="Auto-uploaded by train.py train_all()",
)
```

Both calls are idempotent — re-uploading replaces; UPSERT on the row.

## Operator workflow

```bash
# Train a new candidate (auto-uploads + auto-registers)
python3 workers/model/train.py --version v_2026_05_24

# Inspect the registry
python3 scripts/list_models.py
# → table of every version with cv_metrics + promotion status

# Compare candidates (no shadow deploy needed)
python3 scripts/offline_eval.py v_2026_05_24 v_2026_05_17 --include-v9
# → side-by-side log_loss / Brier / hit_rate per market

# Promote (in psql or via a dashboard)
UPDATE model_versions SET promoted_at = NOW() WHERE version = 'v_2026_05_24';
UPDATE model_versions SET demoted_at  = NOW() WHERE version = 'v_2026_05_17';

# Switch live (Railway dashboard → Variables)
MODEL_VERSION=v_2026_05_24       # then redeploy
# → new container starts, finds bundle missing, auto-downloads, ~3s pause
#   on first prediction, then steady-state

# Roll back (same path in reverse)
MODEL_VERSION=v_2026_05_17       # old bundle still in Storage forever
```

## Costs at typical scale

For weekly retrains, ~5MB bundles, 5 years horizon:

| Resource | Quantity | Cost |
|----------|----------|------|
| Storage | 260 versions × 5MB = 1.3GB | $0.027/mo on Supabase ($0.021/GB) |
| Postgres rows | 260 rows × <1KB | negligible |
| Egress | First-pred-after-deploy × 5MB × ~30 deploys/mo | < $0.01/mo |

**Total: under $0.05/mo for a five-year archive of weekly bundles.** This is why it beats every other option for small-to-medium teams.

## What I'd change for a brand-new project

1. **Pick the version naming scheme up front.** `v_YYYY_MM_DD` works if you retrain less than daily. For more frequent retraining, append a content hash: `v_2026_05_17_a4b2c3`. Never reuse a version string.

2. **Don't use the in-memory module cache at module load time.** This repo's `_model_cache = {}` lives in module scope and never expires. Fine for production (each container has one cache). Painful for tests (need to clear between assertions). Better: an `lru_cache(maxsize=4)` keyed on version string, so multiple versions can coexist in memory for shadow A/B.

3. **Store CV metrics per market, not per model.** A single bundle has 4-5 markets (1X2, over_under, btts, etc) — store each market's metrics separately so per-market promotion decisions are easy to query.

4. **Add a `git_sha` column.** Record which code commit produced each bundle. Lets you reproduce builds.

5. **Add a `parent_version` column.** Records which previous bundle's hyperparams / training config seeded this one. Lets you build a lineage tree for explainability.

6. **Wire CV metrics into the `cv_metrics` JSONB field at training time.** This repo's first cut left it `NULL` (TODO) — the right fix is `train_*_model()` returning a dict that gets bubbled up to `register_version()`.

7. **Add a checksum.** SHA256 each .pkl on upload, store the hash in metadata, verify on download. Catches Storage corruption (rare but happens).

8. **Don't grant the publishable/anon key write access.** Service-role only for upload + registry writes. Anon can stay read-only on `model_versions` if you want to expose model history publicly.

## Reusing this in another project

Minimum viable port (~3 hours of work):

1. **Pick your object store.** Supabase Storage, AWS S3, Cloudflare R2, GCS. The `storage.py` module is small enough to swap backends in one afternoon — only `_client()` and the upload/download/list calls touch the SDK.

2. **Run the migration.** Copy `supabase/migrations/090_model_versions.sql`. Adjust RLS to your auth model.

3. **Copy four files:**
   - `workers/model/storage.py` — the Storage adapter (swap SDK if not Supabase)
   - `scripts/list_models.py` — operator CLI
   - `scripts/bootstrap_model_storage.py` — one-shot uploader for existing bundles
   - `scripts/smoke_test.py` — port the four `ML-BUNDLE-STORAGE — …` tests

4. **Wire two hooks** in your existing model code:
   - `train_all()` end → call `upload_bundle()` + `register_version()`
   - `_load_models()` start → call `ensure_local_bundle()` if dir missing

5. **One-time setup:**
   - Create the `models` bucket (private, file-size cap ~50MB)
   - Set service-role key in production env (e.g. `SUPABASE_SECRET_KEY`)
   - Run `bootstrap_model_storage.py` to seed Storage with current bundles

6. **Set production env var:** `MODEL_VERSION=<your_chosen_version>`. Redeploy. Watch logs for the "Bundle X not on local disk — pulling from Storage..." line on first prediction. After that, you're done — every weekly retrain just writes to the same registry and Storage, and any time you set a different `MODEL_VERSION` and redeploy, the right bundle gets pulled.

## What this design doesn't do (and how to add it later)

- **Doesn't auto-promote.** Promotion is a manual SQL update. Add an `auto_promote_if_better.py` cron once you trust your eval harness.
- **Doesn't shard storage by team / tenant.** If you go multi-tenant, prefix the bucket with tenant ID: `tenant_42/models/v_.../`.
- **Doesn't sign Storage URLs for client-side download.** Server-side download from your runtime is fine for ML bundles; signed URLs only matter if you're letting external clients fetch models directly.
- **Doesn't handle model warm-up.** First prediction after deploy is ~1-3s slower because of Storage download + joblib load. Add a startup-side `_load_models()` call if you need cold-start latency below 500ms.

These are all add-ons that don't require redesigning anything above. The base architecture buys you the optionality.
