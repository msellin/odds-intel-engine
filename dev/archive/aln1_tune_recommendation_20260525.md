# ALN-1-TUNE — Recommendation (generated 2026-05-25)

Window: last 30 days · Baseline NONE ROI: -2.31%

| Class | n | hit% | ROI% | current bump | recommended bump | rationale |
|-------|---|------|------|--------------|------------------|----------|
| HIGH | 27 | 22.2 | -48.68 | +0.0000 | +0.0000 | (auto) |
| LOW | 910 | 39.3 | -0.03 | +0.0100 | +0.0100 | (auto) |
| MEDIUM | 40 | 40.0 | +31.75 | +0.0000 | +0.0000 | (auto) |
| NONE | 479 | 39.7 | -2.31 | +0.0000 | +0.0000 | (auto) |

**To apply** (post-2026-06-07 only — Phase 3.5 lock):
Edit `workers/jobs/daily_pipeline_v2.py:2704`:
```python
_ALN_BUMP = {
    "LOW": 0.0100,
    "MEDIUM": 0.0000,
    "HIGH": 0.0000,
    "NONE": 0.0000,
}
```
