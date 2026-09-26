Parent: PRIORITY_QUEUE.md #191 OWN-BOT-LINEUP-AND-ADMIN-BOTS-CLEANUP-2026-09-26 — task (a2), instruments brainstorm

# #191 brainstorm — does an "Instruments & history" block earn its place?

**Recommendation: no block.** Every candidate either (a) is already a single reference
number on another block, (b) belongs under its parent as a variant, or (c) hits its locked
stop within days to three weeks and then retires. History is already one tab
(`Retired`). What is left is a single footer line, and even that disappears on its own.

Measured 2026-09-26 (read-only: `bot_scoreboard`, `scripts/sharp_tight_slope.py`,
`scripts/inplay_slowstate_eval.py`, `python3 -m scripts.picks_forward_test_checkpoint --twins`).

---

## 1. Devil's advocate: who reads each one, and what decision does it feed?

| Candidate | Who reads it today (grep) | Decision it feeds | Where it stands vs its locked rule | Verdict |
|---|---|---|---|---|
| **Junk control** (`junk_anchor` arm, `control_junk_anchor`) | Web: `bot-board-model.ts` (Δ vs control, `t_Δ`), `control-strip.tsx` (reference strip), `bot-viz.tsx` (dashed amber line), `how-to-read.tsx`. Engine: twin checkpoint (twin − junk). | **Yes, a real one:** every forward-test verdict and both twin readouts are "does this beat junk?". | n 706 (343 in 7 d), sharp CLV **−2.43% (n 611, t −15.2)**, ROI −0.7%. Working as intended: a clearly-negative floor. | **Keep running. Never a bot row.** It is already a strip + dashed line; that is the right form. |
| **Twin arms** (`bot_sharp_aligned_v1`, `bot_consensus_pinconf_v1`, migration 464) | Checkpoint script `--twins`; they also show today as ordinary rows in the forward-test section. | **Yes, PICKS:** adopt the extra gate into the parent as a new `rule_version`, or close it. | Pinconf: 82 settled, interim n=50 passed, readout at **n=100 ≈ 2026-09-27**. Aligned: 7 settled at ~7/day → **n=100 ≈ 2026-10-09**. | **Keep. Show as a variant line UNDER its parent**, not as a peer bot and not in a separate block. |
| **`bot_trigger_1x2_sharp_tight_v1`** | Engine scripts only (`sharp_tight_slope.py`, slice analysis); web shows it as just another row in "Sharp triggers" with its description text. | Nominally OWN promotion. **In practice none** — superseded by `bot_own_1x2_v1` (#182: last-3-h sharp 1X2 at our books). | Fresh legs **244/300**; mean mc-CLV **−4.06 pp**; slope **−0.43 [−1.28, +0.42]**. Both locked rules point to **RETIRE** (CLV < −2%; slope CI includes 0). ~22 fresh legs/day → n=300 **≈ 2026-09-29**. Getting from −4.06 to above −2 in 56 legs is not realistic. | **Retire at its checkpoint (≈3 days).** No page row meanwhile. |
| **`bot_unified_gate_1x2_paper_v1`** | Engine only (`pick_generator`, `bot_configs`, backtest/slice scripts). Web: floor table only; on the page it sits in "Model · paper" beside real candidates. | OWN/model gate: "may draws/aways join the 10% model gate at odds ≥ 2.80?" | Every selection negative: away mc-CLV **−8.6% (n 240)**, home **−6.0% (169)**, draw **−7.6% (52)**; sharp CLV −11.1% t −8.6, ROI −24%. Lock is n ≥ 300 **per selection**: away ≈ 09-27, home ≈ 09-30, **draw ≈ 10-18**. | **Keep collecting until draw n=300 (costs nothing), then close as a negative result per its own stopping rule.** No page row. One query answers it; no dedicated readout script exists, so add a verify-queue entry. |
| **`bot_inplay_slowstate_v1` + `_afctl_v1`** | Engine only (`inplay_collector`, `inplay_slowstate_eval.py`, `observatory_metrics`). Web: labels only. The board cannot even show its metric (family `inplay` → "lift, not computed yet" → NO CLV pill). | "Reopen in-play for OWN?" In-play betting is retired (2026-08-21) and Coolbet own-betting is pre-match only, so a positive answer would itself need a new project. | Live arm **801/1000**, lift **−0.84 pp [−3.61, +1.94]**; control +2.41 pp; fresh-board value **−3.25 pp**. Locked: n ≥ 1000 and lift < 0 → **STOP**. ~76/day → **≈ 2026-09-29**. | **Let it reach its STOP (≈3 days), then retire both arms together.** No page row. The page could never show its one number anyway. |
| **Retired bots** | `retired-list.tsx`, already a `Retired (N)` tab (`?view=retired`), grouped by month, default filter "Had picks", zero-pick bots collapsed. | History and audit only. | 91 retired in `bots`, 70 of them on the scoreboard. | **Already solved.** Keep the tab and rename nothing. |

### What the devil's advocate concludes

1. **Only two of the six feed a decision anyone is still waiting on.** Those are the junk control and the twins, and both are about **PICKS** rules. Neither is an OWN instrument.
2. **Three of the six are about to end.** sharp-tight, in-play and unified-gate each have a pre-registered stop, and each is heading into it with a negative number. For roughly a week, a block built for them would show three bots that are all about to be retired. After that it would be empty.
3. **What actually clutters the page is not the missing block but misfiling.** sharp-tight sits among "Sharp triggers" and unified-gate among "Model · paper", looking like candidates. The only thing that marks them as instruments is prose in their description. Fixing that with an `instrument` flag in the registry is cheaper and cleaner than adding a block.
4. **"Replace with a single number" applies twice:**
   * junk control → the dashed line and the one-line strip it already is;
   * for the OWN block, the equivalent reference is **"a random pick at our books"**: own-book mc-CLV of a randomly chosen leg, ≈ **−7.2%** in the sharp-tight pre-registration. Show it as a computed reference line on the OWN block, not as a bot. An OWN bot is only interesting if it sits clearly above that line.
5. **The value of an instrument is its readout, not its row.** The failure mode we have actually seen is a checkpoint arriving with nobody reading it. That is a verify-queue job (`ops/verify/*.yml` → Telegram on due/mismatch), not a UI job.

---

## 2. The minimal clean design

### No "Instruments & history" block. Instead, four small moves.

**(1) Junk control stays a reference, on the PICKS block only.**
Keep the existing strip on top of PICKS. Give it one plain sentence:

> *Junk control: the same rules fed a deliberately wrong fair price. A published bot that cannot beat this line is showing luck, not skill.* (sharp CLV −2.4%, n 611)

**(2) Twins nest under their parent in PICKS.**
The parent row gets a small `1 variant under test` chip. Expanding it shows one line:

> *`+ own-book quote ≤ 5 min old`: same rule with one extra gate. 7 / 100 settled · readout ≈ 9 Oct · never published.*

Pinconf's line carries the same text with its own gate. When the readout closes a twin, the chip goes away. If the twin is SUPPORTED, the parent gains a new `rule_version`, which is a new row by the existing rule.

**(3) One footer line under OWN, visible only while something is running.**

> *3 time-boxed experiments running. None can stake or publish. Next readout ≈ 29 Sep.* `show`

`show` expands a table with at most four rows. Each row gets one plain sentence, a progress bar to its locked decision n, and the date it is expected:

| Experiment | One-sentence explanation | Progress to decision |
|---|---|---|
| Sharp 1X2, tight gate | *Tests whether a narrower sharp gate at our books beats the closing price. It currently doesn't (−4.1%).* | 244 / 300 fresh legs · ≈ 29 Sep |
| Draws & aways at 10% | *Tests whether the model's draw and away picks at odds ≥ 2.80 are any good. So far every side loses to the close.* | away 240/300 · home 169/300 · draw 52/300 · ≈ 18 Oct |
| In-play late-game rig (live + control) | *Tests whether a fresh in-play price at Epicbet picks winners better than the bookmaker expects. So far it doesn't (−0.8 pp).* | 801 / 1000 · ≈ 29 Sep |

Rules for the line:
* It is driven by a registry flag (`instrument=True`, with `decide_at_n` and a readout command) rather than hand-kept.
* A bot flagged as an instrument never appears in PICKS or OWN.
* When the last experiment reaches its stop and is retired, the line renders nothing.

If the owner would rather have zero UI, drop (3) entirely. The verify queue carries the readouts, and nothing is lost except a glance.

**(4) History = the existing `Retired (91)` tab.**
Keep it as the only route to history. It is already the "single link" pattern: one tab, grouped by month, "Had picks" by default, zero-pick bots collapsed into one line. Two adjustments:
* When an instrument retires, write its readout into `retired_reason`, e.g. *"STOP at n=1000: lift −x pp, CI [..]"*. The history row then shows the answer, not only that the bot stopped.
* Move the junk control's and the twins' retired variants there too, when they close.

---

## 3. What this implies for (c)/(d) — for the synthesis, not decided here

* **Registry:** add `instrument: bool` (plus `decide_at_n` and a readout command) to `BotSpec`. Flag sharp-tight, unified-gate and both in-play arms. Treat the twins as `variant_of=<parent>` rather than as instruments. The board filters on the flag, so instruments leave the strategy sections.
* **Verify queue:** add one `ops/verify/<bot>-checkpoint.yml` per instrument and twin, each carrying the readout command, the locked rule and the due date above. That is what makes each decision actually happen.
* **Retirements to expect within ~3 weeks, if the locked rules fire as the numbers indicate:**
  * sharp-tight (≈ 09-29);
  * in-play live + control (≈ 09-29);
  * unified-gate (≈ 10-18, after draws reach 300).

  That removes 4 active rows. In each case the owner confirms at the readout. Nobody pre-empts a locked rule.
* **Side observation, not investigated:** unified-gate's settled draws show a mean pick price of **9.69**, and aways **8.75**. That is long for a 1X2 draw and may mean the gate is mostly buying longshots. Worth one look before the draw readout, so a "negative result" isn't really a composition artefact.

## One-line answer to the owner

An instruments block would show three bots that are about to be retired, plus two that belong under their parent. Keep the junk control as the reference line it already is, and nest the twins under their parent. Let the three experiments finish into the `Retired` tab, with a single footer line while they run, or no UI at all if you prefer.
