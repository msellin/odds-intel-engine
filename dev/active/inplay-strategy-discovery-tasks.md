# In-play strategy discovery — TASKS

- [x] Decompose the problem: state (we own it) vs price (we don't)
- [x] Confirm `live_match_snapshots` usable — 2.2M rows / 35,493 matches, minute+score fully populated
- [x] Build Epicbet in-play collector
- [x] Fix AF state matching (aliasing bug) + connection pool + detachment
- [x] Launch overnight collection (~9h, detached, verified writing with state)
- [x] Flatten 2.2M snapshots → 35,442-match state matrix
- [x] Write 14 candidate strategies (theses BEFORE numbers)
- [x] Backtest all 14 on hit rate + break-even odds, with Wilson CIs
- [ ] Edge estimate vs market price on quiet triggers (`edge.py` running)
- [ ] Write `docs/INPLAY_STRATEGY_CANDIDATES_2026_09_14.md`
- [ ] Analyse the overnight Epicbet capture (morning)
- [ ] Update `PRIORITY_QUEUE.md` + commit
