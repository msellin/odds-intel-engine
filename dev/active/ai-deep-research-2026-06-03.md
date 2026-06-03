# OddsIntel AI Deep Research — 2026-06-03

> Brief: where can AI (LLMs, transformers, RL, modern ML) materially improve our picks OR
> deliver more user value, beyond what Poisson+XGBoost+Gemini already do? Augmentation only —
> the existing model stays in production.

---

## TL;DR — Five highest-conviction opportunities

1. **LLM news extractor → structured signal pipeline (RAG-style).** Already half-built via `news_checker.py`. Adding a retrieval-augmented loop over X/Twitter, Reddit r/soccer team threads, and local-language press conferences would let us catch lineup news + rotation hints 1–3 hours before bookmakers price them. This is the single most defensible model improvement available — it directly attacks "information speed", which the team's own MODEL_ANALYSIS already names as the #6 source of edge.
2. **Live (in-play) gradient-boosting model at fixed minute checkpoints.** All four of OddsIntel's prior external evaluators flagged this as the highest untapped ROI vector, and the data is now arriving (LivePoller running 24/7). Lift target: +2–5% ROI on a new market segment, plus an obvious "live picks" product feature for Elite.
3. **A conversational match-research chat ("Ask OddsIntel about this fixture").** Elite-tier product feature. Built on the existing Supabase signal store via a tool-using Gemini 2.5 Flash agent. Cost <$10/mo at current traffic, retention impact is the main win — directly answers users' "tell me what is interesting about this match" without us prewriting every angle.
4. **CLV-targeted meta-model with text-derived features.** Frame the meta-model (already on the roadmap as B-ML3) as "predict P(this bet beats Pinnacle closing line)" with the LLM-derived injury/lineup/news features as inputs. CLV — not binary win/loss — is what they already track as the EV ground truth, and LLM features can give it information bookmakers don't yet have.
5. **Personalised Telegram/Email digest powered by LLM bet selection.** They already have Telegram alerts. Adding an LLM layer that picks 1–3 bets per user from the daily slate, framed for that user's bankroll size and risk tolerance, is a low-cost differentiator vs. "every bet, blasted to everyone". Pure retention/upsell play.

The first two are model improvements. The last three are product features that depend on the same data they already collect.

---

## Current AI footprint

OddsIntel already uses AI more than the brief suggested. Confirmed integrations from the codebase:

- **Gemini 2.5 Flash — `workers/jobs/news_checker.py`**: structured-JSON extraction of injuries, suspensions, manager changes, lineup confidence. 4 runs/day. Output writes `news_impact_score`, `lineup_confirmed`, `injury_severity_score_*`, `players_doubtful_*` into `match_signals`. These are wired into both alignment scoring and the meta-model feature set.
- **Gemini 2.5 Flash — `match_previews.py` / `wc_match_previews.py`**: 200-word match previews for the top ~10 fixtures/day + every World Cup fixture. Customer-facing on the match detail page.
- **Gemini Flash-Lite — `src/app/api/bet-explain/route.ts`**: Elite-tier "Why this pick?" explanations. Generated lazily on first request, cached on `simulated_bets.ai_explanation`. Bounded to ~$0/mo at current bet volume.
- **Gemini — `settlement.run_post_mortem()`**: daily loss-categorisation post-mortem.
- **Gemini — `scripts/resolve_team_names.py`**: one-shot LLM team-name resolution against canonical lists.
- **No transformer / deep-learning prediction model**. Core 1X2/OU/BTTS/AH heads are Poisson + XGBoost. Calibration is Platt sigmoid (1X2) and 2-feature logistic (OU). Meta-model is logistic-regression → planned XGBoost.

So the AI usage today is exclusively **LLM-as-feature-extractor** and **LLM-as-product-skin**. Nothing currently uses an LLM or neural net to *score* a bet.

---

## Q1 — Model improvements

| # | Opportunity | What it is | Evidence it works | Effort | Expected lift | Risk |
|---|---|---|---|---|---|---|
| Q1-A | **RAG-style news + lineup ingestion** | Extend `news_checker.py` to pull X/Twitter "team news" accounts, local-language beat reporters (Telegram channels for ETK, RUS, GRE, TUR), and pre-match presser transcripts. Feed into existing Gemini extractor. Add `pressroom_intent` and `rotation_risk` columns. | Sports books move on injury news within 2–6 minutes of reliable reports (Pinnacle's known behaviour). OddsIntel already proved `lineup_confirmed` is worth +12pp ROI (see MODEL_WHITEPAPER §3.4 feature #9). The information speed thesis is in their own MODEL_ANALYSIS doc. | M | +1–3pp ROI on bets where signals trigger (~20% of slate) | LLM hallucinating injuries; mitigate with source-attribution requirement + post-mortem categorisation already running |
| Q1-B | **In-play GBM model at minute checkpoints** | Train LightGBM on `live_match_snapshots` at 15/30/45/60/75' with state features (live xG, score, cards, possession) + live odds. Predict P(home win), P(over 2.5), P(BTTS). Compare to live odds for edge. | All 4 OddsIntel evaluators flagged this as highest untapped ROI. Published EPL studies show +107% ROI walk-forward on long-odds value picks using LightGBM on similar features (Medium case study). Their own MODEL_ANALYSIS Tier 3 item 11.7 already specs this. | M | +2–5% ROI on in-play; opens new market segment | Calibration drift at extremes (90'+); market is highly efficient on big matches — focus T3/T4 |
| Q1-C | **Team / player embeddings via a small foundation model** | Pre-train a transformer with player-as-token, match-as-sentence (per RisingBALLER, arXiv 2410.00943). Use the resulting team-embedding as an additional XGBoost feature alongside ELO. Player embeddings can capture style / fit / role-coverage that ELO cannot. | RisingBALLER (StatsBomb Conf 2024) shows the approach is viable on event-level data. HIGFormer (arXiv 2507.10626) reports outperforming baselines on WyScout. A Foundation Model for Soccer (arXiv 2407.14558) is the closest published "big bet". | XL | Uncertain — published lifts are vs raw deep-learning baselines, not a tuned Poisson+XGB | Their data is fixture-level not event-level; without StatsBomb/Wyscout licences (~€thousands/mo) the input data is too thin |
| Q1-D | **CLV-targeted meta-model (B-ML3) with LLM features** | Already on the roadmap. Critical refinement: include `news_impact_score`, `lineup_confirmed`, `injury_severity_score_*` as features *and* use `pseudo_clv > 0` as target rather than binary profit. Target a Brier-optimised XGBoost classifier with isotonic calibration. | Their MODEL_ANALYSIS already lists CLV as the EV ground truth (Round 1 + Round 2 verdicts). Their own NEWS-LINEUP-VALIDATE work already showed `lineup_confirmed` alone is worth +12pp ROI. | S (data exists) | +2–4pp ROI uplift once threshold-tuned | Overfitting to a narrow window — gate with rolling backtest |
| Q1-E | **Odds trajectory clustering via DTW** | Cluster the per-match odds path with Dynamic Time Warping into shapes ("steady drift", "late steam move", "reversal", "stable"). Add cluster_id as a feature. | OddsIntel's MODEL_ANALYSIS Tier 3 item 11.8 (2/4 evaluators). Published precedent in financial-time-series clustering; sports-betting literature thinner but mechanistically sound. | M | +0.5–1.5pp ROI; main value is interpretability not magnitude | Cluster identity drifts as the bookmaker mix changes — requires periodic re-clustering |
| Q1-F | **LLM as ensemble member ("LLM probability")** | Give Gemini Pro the full structured match brief (form, ELO, injuries, head-to-head, weather) and ask for a 1X2 probability. Blend at 5–10% weight into the ensemble. | Hyped but weak. LLMs reason about football poorly without grounding; their probabilities are anchored to public narrative which is already in the bookmaker line. | S | Likely ~0% — possible negative | High. Listed here so you can rule it out cleanly |
| Q1-G | **Reinforcement-learning staking** | Replace fractional Kelly with an RL agent that learns to size bets given an edge estimate + recent volatility + bankroll state. | No peer-reviewed evidence RL beats fractional Kelly in sports betting. Fractional Kelly has 70 years of theory behind it and survives almost every RL benchmark in finance. | L | ~0% in expectation, large variance increase | High. Not worth the dev time given Kelly works. |
| Q1-H | **Shadow line model** | Predict where the bookmaker *should* open the line. If it opens off your prediction, fire before sharp money corrects. | OddsIntel's own MODEL_ANALYSIS item 11.10 ("most original idea across all assessments"). Blocked on systematic opening-odds storage which they now have. | M | +1–3pp ROI on first-mover bets | Time-sensitive — needs sub-minute latency from open to bet |

### Recommendations from Q1

Do Q1-A and Q1-D in the next 1–2 weeks. They are pure extensions of work already shipped. Q1-B should be on the calendar for late June 2026 once the in-play snapshot store hits ~500 distinct matches (per the readiness query in MODEL_ANALYSIS §11.7).

Treat Q1-C (foundation models) as **research interesting, product irrelevant** until you have event-level data. Skip Q1-F and Q1-G entirely.

---

## Q2 — Product features

| # | Opportunity | What it is | Evidence it works | Effort | Expected user impact | Risk |
|---|---|---|---|---|---|---|
| Q2-A | **Conversational match research** | "Ask anything about this fixture" chat on the match detail page. Gemini 2.5 Flash with tool calls into `match_signals`, `predictions`, `injuries`, `odds_snapshots`. Cite the data. Elite-only. | Dimebot, Dimers AI, BetHarmony all ship variants. The wins are retention + session length, not pick accuracy. | M | High retention; clear Elite differentiator | LLM saying "bet the over" — explicitly forbid recommendation language, force citation of data |
| Q2-B | **Personalised AI daily digest** | LLM picks 1–3 bets per user from today's slate, framed for that user's bankroll, recent results, and league preferences. Sent via existing Telegram + email infra. | All major prediction platforms ship this. OddsIntel's `personalised` digest is plain rules today — LLM rewrite is incremental work. | S | Major upsell hook for Elite. Tighter free→pro conversion. | Stake sizing tone — frame as "research", not advice |
| Q2-C | **AI-generated short video / audio recap** | 30-sec daily recap clip auto-generated from yesterday's results + today's slate. Posted to social. Use ElevenLabs voice + a templated video (HeyGen / Synthesia free tier). | YouTube / TikTok preview channels dominate organic discovery in this niche. Their REDDIT_LAUNCH already plans social. | M | Brand reach; SEO-adjacent | Quality bar is high — invest only after retention is solid |
| Q2-D | **Anomaly alerts: "weird line move"** | Detect outsized `pinnacle_line_move_*` or `bookmaker_disagreement` deltas in real-time; surface to Pro+ as "Market is reacting to something — possibly news we missed". Optionally LLM-summarise what news exists. | They already have a `news_impact_score`. Wrapping it in an actual notification is a 2-day job. | S | Pro retention; reinforces the "intelligence" positioning | Notification fatigue — cap at 2/day |
| Q2-E | **Bankroll-history coaching** | Analyse the user's own `prediction_tracker` log: "you've gone 3-12 on away dogs in T1 leagues; consider sitting these out." LLM frames the feedback. | Differentiating and gentle-paternalist. Touchy but no competitor does it well today. | M | Stickiness for repeat users; emotional engagement | Risk of moralising / being preachy — heavily test tone |
| Q2-F | **Multi-modal lineup OCR / event detection** | Read uploaded lineup cards, identify formations, parse video for events. | Too ambitious for a 1-dev shop. API-Football already supplies confirmed lineups; this duplicates work. | XL | Marginal — they already have the data | Skip |
| Q2-G | **AI-written weekly newsletter** | Sunday recap + Monday-look-ahead emailed to free users. Build user funnel into Pro. | Standard content marketing play. They already have email infra. | S | SEO/email funnel; tier upsell trigger | Quality bar matters — LLM slop hurts brand |
| Q2-H | **LLM as a research-assistant inside the admin tools** | Internal: when you're investigating a bot's losing streak, point an LLM at the bot's `simulated_bets` history and ask "what failed?". | Already exists in primitive form (`run_post_mortem`). Promote it to a chat surface. | S | Dev efficiency, not customer-facing | None |

### Recommendations from Q2

Do Q2-D and Q2-G this month. Q2-A is the headline Elite feature when you have a free weekend. Q2-B is the highest-leverage conversion bet — wire it in as soon as the meta-model goes live (so the LLM has a meaningful confidence score to filter on).

Skip Q2-F. Defer Q2-C until you have at least 100 paying users — content production cost outweighs the brand lift before that.

---

## Three concrete experiments to run first

### Experiment 1 — RAG news ingestion against a hand-labelled gold set (Q1-A)

**Build:** Add an `external_news_sources` table with rows for ~30 high-signal X/Twitter accounts (Fabrizio Romano, David Ornstein, regional beat reporters for your T3/T4 league focus). Pull every ~30min during the morning enrichment window. Pass the last 6 hours of tweets *plus* the structured fixture context into a Gemini Flash call with the existing `news_checker.py` prompt — but with a *citations required* instruction.

Write the structured output to `match_signals` as `external_news_impact_score` and `external_news_confidence`.

**Measure:** Build a 200-match gold set (a weekend's worth) where you hand-classify what the actual lineup change / injury was. Score Gemini's hit rate, false positive rate, and **lead time** vs. the current AF-based news pipeline.

**Success:** ≥70% recall on actual lineup changes, ≥85% precision, and ≥30 min average lead time on at least 25% of detected events.

**Kill criteria:** If precision is below 75% after one prompt iteration, kill — false positives will poison the meta-model.

**Cost:** ~$5–15/mo on Gemini Flash. Roughly inside the existing news_checker budget.

### Experiment 2 — In-play LightGBM checkpoint model (Q1-B)

**Build:** Train LightGBM on every `live_match_snapshots` row at the 30', 45', 60', 75' marks for finished matches. Features: score, live odds, possession, shots, xG-so-far, cards, time-since-last-goal, league_tier, opening odds. Targets: 1X2 (after settling), Over 2.5, BTTS.

Backtest against actual in-play odds from the same window. Quote it as edge vs. live closing odds.

**Measure:** Out-of-sample log-loss vs. live market implied probabilities. Walk-forward backtest of "fire when edge > 4%" against actual in-play odds movement.

**Success:** Live log-loss ≥3% better than the live market. Backtest CLV > 0 on the held-out window.

**Kill criteria:** Live model fails to beat the in-play odds on a 1-week holdout. Means the market is too efficient at the windows you have data for.

**Cost:** $0 incremental — uses existing snapshots.

### Experiment 3 — Personalised LLM digest (Q2-B)

**Build:** Once a day, for each Elite + Pro user, run a Gemini call that takes (a) today's value bets shortlist, (b) user's last 30 days of `prediction_tracker` history, (c) user's stated risk profile from onboarding. Output: 2–3 picks, written in 1 paragraph each, framed for that user. Send via existing Telegram + email channel.

Crucially, the LLM does NOT generate the picks — it only narrates and selects from the pre-computed value bets list.

**Measure:** Open rate, click-through to the match detail page, and 30-day Elite retention vs. the un-personalised baseline cohort. Run as an A/B over 4 weeks.

**Success:** Open rate +25%, click-through +15%, retention +5pp at 30 days. Any one of those clears the bar.

**Kill criteria:** No retention lift after 6 weeks — means the personalisation is cosmetic and we should revert to deterministic rules to save the per-user LLM call.

**Cost:** ~$5–25/mo at 100 daily Elite + Pro users. Cap with the same per-user rate limit pattern as `bet-explain`.

---

## What's NOT worth doing

These are the items that look attractive in headlines but won't move OddsIntel's numbers. Listed so you can dismiss them quickly when a competitor brags about them.

- **"LLM as a predictor."** An LLM asked "who will win Arsenal vs. Chelsea?" anchors to public narrative, which the bookmaker has already priced. Empirically these systems lose to a calibrated XGBoost by 5–10% log-loss. Sites that lead with this (most of the "AI predictions" SaaS results in the search above) are marketing-first, modelling-second. Skip Q1-F.
- **Reinforcement learning for staking.** No published evidence RL beats fractional Kelly in sports betting. Fractional Kelly is a closed-form solution to a stationary problem; RL's gains live in non-stationary control problems. Your problem is stationary at the per-bet level. Skip Q1-G.
- **Foundation model for soccer (RisingBALLER, HIGFormer, etc.)** — interesting research, but the input data is event-level (StatsBomb / WyScout, multi-thousand-€/mo licences). Your data is fixture-level. Without the event data the architecture is wasted. Revisit only if you ever buy a StatsBomb feed. Skip Q1-C.
- **Multi-modal vision (lineup OCR, video event detection).** API-Football already supplies lineups, scores, and event timelines as structured JSON. Building a vision pipeline to recreate that is pure cost. Skip Q2-F.
- **TimeGPT / Lag-Llama / generic foundation time-series models.** These were trained on broad time series (retail, weather, energy). They have no priors about football. Out-of-the-box they underperform a domain-tuned XGBoost on the same dataset (confirmed in multiple academic comparisons). The data efficiency argument doesn't apply when you already have 50K labelled matches.
- **"AI agents that place bets autonomously."** Legal liability, value-of-bankroll risk, and Coolbet/Pinnacle terms-of-service exposure all dominate any modelling benefit. The current daemon-with-`--execute`-gate pattern is correct.
- **Crypto-style sentiment scraping (4chan / r/wallstreetbets analogue).** Football betting subreddits are too small and too uninformed to be a leading indicator. The only useful Reddit signal is reading official team subreddits for breaking news — which RAG (Q1-A) covers anyway.

---

## Cost and time-budget summary

| Recommendation | One-off dev | Recurring cost | Time to ship |
|---|---|---|---|
| Q1-A RAG news ingestion | 1–2 weeks | <$20/mo Gemini + X API ($100/mo Basic if needed) | 2 weeks |
| Q1-B In-play GBM | 1 week | $0 (existing snapshots) | 2 weeks once `live_match_snapshots` ≥ 500 unique matches |
| Q1-D CLV meta-model | 3–5 days | $0 | Immediate — data is ready per their own readiness queries |
| Q2-A Match research chat | 1 week | <$10/mo | 2 weeks |
| Q2-B Personalised digest | 3 days | <$25/mo | 1 week |
| Q2-D Anomaly alerts | 2 days | $0 | 1 week |
| Q2-G AI newsletter | 2 days | <$5/mo | 1 week |

Total recurring cost increment vs. today: well under $200/mo even at all of the above shipped — staying inside the constraint cleanly.

---

## Sources

- [Player-Team Heterogeneous Interaction Graph Transformer for Soccer Outcome Prediction (HIGFormer, arXiv 2507.10626)](https://arxiv.org/pdf/2507.10626)
- [RisingBALLER: A Player is a Token, a Match is a Sentence (arXiv 2410.00943)](https://arxiv.org/abs/2410.00943)
- [A Foundation Model for Soccer (arXiv 2407.14558)](https://arxiv.org/abs/2407.14558)
- [Match predictions in soccer: ML vs. Poisson approaches (arXiv 2408.08331)](https://arxiv.org/pdf/2408.08331)
- [Large-Scale In-Game Outcome Forecasting (arXiv 2511.18730)](https://www.arxiv.org/pdf/2511.18730)
- [Profitable EPL Betting Strategy with LightGBM — Medium](https://medium.com/@omnimahui/profitable-england-premier-league-betting-strategy-a-full-end-to-end-experiment-2a53b32ba16d)
- [LLM-based feature generation from text (arXiv 2409.07132)](https://arxiv.org/pdf/2409.07132)
- [Applications of LLM Reasoning in Feature Generation (arXiv 2503.11989)](https://arxiv.org/pdf/2503.11989)
- [A Systematic Review of ML in Sports Betting (arXiv 2410.21484)](https://arxiv.org/html/2410.21484v1)
- [FiveThirtyEight is Dead; Long Live Public Soccer Projection Models](https://fromthebyline.substack.com/p/fivethirtyeight-is-dead-long-live)
- [Opta Football Predictions (Analyst)](https://theanalyst.com/articles/opta-football-predictions)
- [Closing Line Value Guide — ProbWin](https://en.probwin.com/guides/closing-line-value-clv-ultimate-metric-measure-your-edge/)
- [Gemini API Pricing 2026 — TokenMix](https://tokenmix.ai/blog/gemini-api-pricing)
- [Dimebot — The Sports Betting AI](https://www.dimers.com/subscription/features/dimebot)
- [BetHarmony iGaming AI Agent (Symphony Solutions)](https://symphony-solutions.com/betharmony)

---

## Internal cross-references (relevant existing OddsIntel docs)

- `/Users/margussellin/www/odds-intel-engine/MODEL_WHITEPAPER.md` — feature set, calibration, meta-model spec
- `/Users/margussellin/www/odds-intel-engine/MODEL_ANALYSIS.md` §10–11 — AI usage roadmap (this report supersedes it)
- `/Users/margussellin/www/odds-intel-engine/SIGNALS.md` — current signal inventory
- `/Users/margussellin/www/odds-intel-engine/workers/jobs/news_checker.py` — existing Gemini news extractor (extend for Q1-A)
- `/Users/margussellin/www/odds-intel-web/src/app/api/bet-explain/route.ts` — existing per-bet LLM caching pattern (clone for Q2-A)
