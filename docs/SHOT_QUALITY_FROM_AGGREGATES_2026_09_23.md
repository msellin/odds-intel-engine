# Building a shot-quality metric from aggregate counts — what the literature supports

**2026-09-23 · research input for [[#078]] / [[#079]] / [[#080]] · no code shipped from this**

We hold team-level counts — shots in box, shots outside box, shots on target,
shots off target, blocked shots, corners — for ~56k matches, and **no shot
coordinates**. This records what is and is not known about turning that into a
goal-expectation estimate.

---

## 1. footballxg.com does not compute xG either

Worth settling, since it prompted the question. Their own [data dictionary](https://footballxg.com/wp-content/plugins/footballxg-data-dictionary/docs/FootballXG-Data-Dictionary.pdf)
(free, no login, 69 pages, 1,496 field paths) shows:

* **~90% of the advertised "1000+ columns" are odds snapshots, fair odds, CLV,
  steam/drift, form and derived rollups.** Actual per-match xG is **five fields**,
  and they are **Pro-tier only** — Core and Free get no realised xG at all.
* **No shot data of any kind.** No shots, no on-target, no in/outside box, no
  blocked. Not one field. Also no possession, passes, PPDA, xA, xGOT, or
  player-level data.
* **They buy the feed and never name the provider.** Three independent tells:
  *"(FYI – data feeds are not cheap!)"*; xG arrives **provisional and is revised
  within a week** (*">98% of games have final xG numbers"*); and a dedicated
  `xG Status` field exists to mark it. You do not get revise-later xG unless you
  are ingesting someone else's.
* Their model layer is team-level xG rolling averages → power rating → **Poisson**
  → value vs odds. That is the same architecture we already have.
* **Their only published performance evidence is a single JPEG uploaded in
  October 2022 and never updated.** Its "All" row, n = 86,101: **Home −5%,
  Draw −5%, Away −6%, O1.5 −5%, O2.5 −5%, O3.5 −11%.** To their credit they say
  *"The ROIs are low? That is correct."*

**So the product is someone else's unnamed xG, repackaged.** Nothing there is a
methodology we could copy, and nothing suggests a moat we would be crossing.

---

## 2. Conversion rates by zone — the numbers to fit against

| source | sample | inside box | outside box |
|---|---|---|---|
| [Opta / The Analyst](https://theanalyst.com/articles/premier-league-2024-25-shot-data), PL 2024-25 | one season | **14.7%** | **4.2%** |
| [StatsBomb](https://blogarchive.statsbomb.com/articles/soccer/premier-league-shot-benchmarks/), PL 2008-13 | ~55,000 shots | **13.1%** | ~2.7% ("1 in 37") |
| Michael Caley, EPL 2009-13 | 50,754 shots | **13.2%** | **3.0%** |

**Shots on target:** Caley — **35% in box vs ~13% outside**. ASA (MLS, 49,811
shots) — 29.2% overall including posts.

⚠️ **The rates have drifted and a 2013 coefficient is not safe to import.**
Outside-box share of all shots fell from **48.2% (2005-06) to 31.7% (2024-25)**
while outside-box conversion rose ~55% relative. **Fit our own rates per
league-season across the 56k matches** rather than hard-coding any of the above.

Other useful anchors: penalties ≈ **0.78** xG (Opta 0.79 / StatsBomb 0.78 /
Wyscout 0.76); direct free kicks **6.6%**; corners **3–4%** of corners end as a
goal; headers convert far below foot shots at the same distance.

---

## 3. Prior art — thin, but it exists and one piece is peer-reviewed

* **Rathke (2017)**, *An examination of expected goals and shot efficiency in
  soccer*, J Human Sport & Exercise 12(2proc) — [free PDF](https://www.redalyc.org/pdf/3010/301052437005.pdf).
  **The only peer-reviewed precedent for exactly this estimator:** Opta data,
  EPL + Bundesliga 2012-13, 18,218 shots, 8 zones, *"shots per zone multiplied by
  its corresponding %goals per shots"*.
  ⚠️ **Trap:** his "SoT" column implies a 55–60% on-target rate against ~31–36%
  everywhere else, so it almost certainly includes blocked shots. **Use his
  per-shot rates; never his per-SoT rates.**
* **[ASA "Calculating Expected Goals 2.0"](https://www.americansocceranalysis.com/home/2014/05/08/calculating-expected-goals-2-0)** — a published logistic
  regression on zone dummies **with full coefficients**: intercept −0.19; zone
  penalties 0.0 / 0.93 / 2.37 / 2.68 / 3.55 / 3.06; header −0.95; corner −0.74
  (*negative* — a shot from a corner is worse than the same zone in open play).
* **Wheatcroft (2020)**, [arXiv:2001.09097](https://arxiv.org/abs/2001.09097) — forecasts from **shots on target, shots
  off target and corners**, i.e. exactly our columns. *"shots off target and
  corners do not provide much information when considered individually but add a
  great deal of information when combined with the number of shots on target."*
* **Eastwood (2013)** — season-aggregate r² vs goals: **SoT 0.76, total shots
  0.62, blocked 0.59, shots wide 0.32**, with an explicit warning that these
  weaken sharply at match level.

**Nine xG repositories on GitHub were checked: every one is shot-level with x,y
coordinates. No project doing this from aggregate counts was found.** There is no
prior art to beat — which also means no published baseline to hide behind.

---

## 4. What it costs in accuracy

**[Robberechts & Davis (2020)](https://dtai.cs.kuleuven.be/static/sports/blog/how-data-availability-affects-the-ability-to-learn-good-xg-models/)** is the most relevant experiment, and it is
encouraging: Brier on EPL 2018/19, **basic** (x, y, distance, angle, body part)
**0.0806** vs **advanced** (47 features incl. two preceding actions) **0.0783**.
**42 extra contextual features buy a 3.6% relative improvement** — location
dominates everything else.

The ladder: geometry-only ≈ Brier 0.086 · event-data models AUC 0.772–0.80 ·
+ tracking data AUC 0.823–0.878. **We would sit below the geometry floor.**

But the honest comparator is not perfection: commercial providers correlate only
**0.92–0.96 with each other at match level** and **disagree on which team won the
xG in 24% of matches**. Landing near that band is defensible.

---

## 5. Warnings that must shape the design

1. **Within-zone heterogeneity is the fundamental objection.** "Inside the box"
   averages ~0.05 (edge of area) to 0.90+ (six-yard tap-in) — an ~18× spread.
   When Opta moved xG 1.0 → 2.0, one shot went **0.035 → 0.656** purely on
   goalkeeper position. That variance is signal our feed structurally cannot carry.
2. **`Blocked Shots` is the most dangerous column — provider definitions
   genuinely conflict.** Opta counts a shot blocked by a *last-line* defender as
   ON TARGET; Sportmonks counts a blocked shot as one that *"was going to hit the
   target"* and explicitly **not** on target, and states outright that providers
   differ. Inter-operator reliability *within* Opta: shots ICC 1.00, **blocks ICC
   0.90**. **There is no published operational definition for API-Football's
   fields at all — an internal audit against a second source is a prerequisite,
   not a nicety.**
3. **Watch the overlap or you will double-count.** SoT ⊂ total shots;
   in-box + out-box ≈ total shots; blocked sits inside total shots and (mostly)
   outside on-target. The two clean non-collinear parameterisations are
   **(a) SoT-in-box + SoT-out-box**, or **(b) all-in-box + all-out-box + blocked**.
   Pick one explicitly.
4. **Rebounds cannot be corrected from marginals.** Opta's own example: a
   penalty + header + volley sequence sums to **1.84 cumulative xG, correctly
   adjusted to 0.95 — a 48% overstatement from three shots.** A goalmouth
   scramble adds 4 to the in-box count while being one chance, and we hold
   marginals only, so we cannot condition on sequence at all.
5. **Defensive pressure is unrecoverable and large.** Stats Perform, 2017/18 PL,
   8,909 shots: high pressure **8%**, low pressure **15%**, **low pressure × high
   clarity 33%**. None of it is in a count.
6. **Never call it xG.** Binning has a legitimate pedigree, and the published
   objection is not to binning — it is to the coarseness of a two-bucket split,
   the loss of the per-shot join, and the destroyed sequence structure.
   **Frame the target as "beats total-shots-ratio", never as "approximates xG".**

---

## 6. What could not be found, stated plainly

* **No published model anywhere that builds an xG-like metric from aggregate team
  counts with no coordinates.** No paper, no blog, no repo.
* **No published per-match R² or log-loss for any aggregate-count model** — every
  such figure in the literature is season-aggregated over ~20 teams.
* **No published correlation between a binned/zonal xG and a coordinate xG.** The
  direct "what does binning cost" number does not exist publicly.
* **Nothing on API-Football field quality** — no audit, no error rate, no
  operational definitions.

⚠️ One number to keep out of any accuracy claim: Eastwood's famous **r² = 0.9883**
is a curve fitted to *binned average conversion rates by distance*, not to
shot-level outcomes. Binned averages always fit near-perfectly.
