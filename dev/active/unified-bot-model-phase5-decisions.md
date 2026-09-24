Parent row: #139 in PRIORITY_QUEUE.md

# Phase 5: six decisions for the owner before we merge the pick tables

*Written 2026-09-24. Reading time is about 5 minutes. The technical detail is in
`dev/active/unified-bot-model-phase5-schema-draft.md`.*

## What Phase 5 is, in one paragraph

Today a bot's picks are saved in one of three tables, and which one depends on the kind of bot.
Every page reads a different subset, so the same bot can show different numbers on different
pages. A fresh example from today: the new **EV5/EV8 bots** save their picks in the "model" table,
and the Shadow-bots page reads only the "paper" table, so it cannot see them at all. Phase 5 moves
every pick into **one table**, with one set of rules for who can see it and what counts. Real-money
bets stay in their own table, as they do today, and link back to the pick that caused them.

**What you will notice:** nothing, for about two weeks. After that, the admin pages and the public
pages switch to the new table one at a time. Some admin numbers will move a little, and each
decision below says which ones. **Cost:** about 3 to 3½ weeks of work, then 2 weeks of both systems
running side by side before anything old is deleted.

---

## Decision 1: What do we do with the "timing copies"?

**The question:** every 30 minutes the system re-checks each bot and saves the same pick again, so
157,000 of the 168,000 paper-pick rows are repeats. Should the new table keep them?

| Option | What it means in practice | What could go wrong | Extra days |
|---|---|---|---|
| **A. Keep them in the new table, marked "copy"** | Nothing is lost. Every page must remember to skip the copies. | One forgotten filter counts a pick 10–40 times. This has happened before: winning picks had about 3× more copies than losing ones, so one bot looked like "+122%" on 18 real picks. | 0 |
| **B. Keep the first sighting as the pick; move the repeats to a separate archive table** | The pick table holds one row per real pick. The repeats (the price history) are kept in the archive for later study. | Almost nothing. One archive table to look after. | +1 |
| **C. Stop saving repeats from now on** | Smaller database, and history is kept in the archive. | We lose the price path from now on. For some bots the "repeat" *is* their only pick record, so this needs care. | +0.5, plus a behaviour change |

**Recommendation: B.** It makes double-counting impossible by design instead of by discipline,
and it throws nothing away. Whether to *stop* saving repeats (option C) can be decided later, once
the bot analysis (#140) shows whether anyone uses the price path.

---

## Decision 2: Which "beat the sharp closing price" number counts for the model bots?

**The question:** there are two versions of the Pinnacle closing-price score (CLV). One is a raw
score that looks about 4½ points too rosy. The other is fair ("de-vigged"). Which one is the
official score?

What changed today: the settler now writes the fair version on every new pick (fixed 2026-09-24).
This decision is now only about **old picks from before 7 September**. Of those 4,194 settled model
picks, about 3,000 have the fair number, and the rest have only the raw one or nothing.

| Option | What it means | What could go wrong | Extra days |
|---|---|---|---|
| **A. Fair number is official. Recompute it for old picks where the price data still exists; leave the rest blank** | One honest number everywhere, the same one the paper bots use. | Some old picks show "no score" instead of a number. | 0.5 |
| **B. Fair number is official; fill the gaps with the raw number** | No blanks. | It quietly mixes two different numbers, and old picks look better than they were. | 0.25 |
| **C. Show both, labelled** | Nothing hidden. | Two numbers on one row confuse; people quote the nicer one. | 0.5 |

**Recommendation: A.** A blank is honest, while a mixed number is not. The raw number is kept in
the database under its own clear name, so nothing is deleted.

---

## Decision 3: Which price do we use to work out profit?

**The question:** when we say a bot made +X%, is that at the price it *recorded* when it decided,
or at the price that was *actually on offer* at that moment?

These can differ. Before 2 September the recorded price was sometimes the best price seen all day,
which nobody could actually have taken. The public figures already use the "actually on offer"
price. Most admin figures use the recorded one.

| Option | What it means | What could go wrong | Extra days |
|---|---|---|---|
| **A. Keep two numbers: admin uses the recorded price, public uses the offered price** | No change from today. | The same bot shows two different ROIs depending on the page, which is one of the things this project is meant to end. | 0 |
| **B. Offered price everywhere; the recorded price is kept only as a stored fact** | One ROI per bot on every page. | Some admin ROIs drop, mostly for old picks. About 15% of settled model picks and 4% of paper picks have no "offered" price; those fall back to the recorded price and are marked. | 0.5 |
| **C. Recorded price everywhere** | Simple. | The public numbers would go *up* for reasons that are not true. We should not do this. | 0.5 |

**Recommendation: B.** It gives one honest number per bot, and it matches what we already publish.

---

## Decision 4: What happens to the old "model top-pick log" (`published_picks`, 50,000 rows)?

**The question:** there is a log of the model's favourite outcome for every match. It is not a
bot, it has no stakes, and no page reads it. Should it join the new table?

| Option | What it means | What could go wrong | Extra days |
|---|---|---|---|
| **A. Leave it alone** | Keeps running as it is. | Nothing. It stays a separate thing. | 0 |
| **B. Fold it in as a pretend bot** | One table for everything. | 50,000 non-bets land next to real picks, and every count needs one more filter. | 1 |
| **C. Archive it and switch it off** | Less clutter. | It is the independent dataset an earlier staking test used, and we would lose that. | 0.25 |

**Recommendation: A.** It costs nothing and risks nothing.

---

## Decision 5: Can we retire the old customer bet-tracker (`user_picks`)?

**The question:** there are 6 rows from May, from a customer feature whose page was deleted in
June. Only the settler still touches it. Can it go?

| Option | What it means | Extra days |
|---|---|---|
| **A. Retire it:** save the 6 rows to the archive, remove the table and its two settle steps | Less code to maintain. | 0.25 |
| **B. Leave it** | Harmless but dead. | 0 |

**Recommendation: A**, done as its own small task outside Phase 5. The operator's pick ticks on
/picks (`user_pick_marks`, 715 rows) are a different, live feature and are **kept**.

---

## Decision 6: One rule for "what counts in our public track record"

**The question:** which picks count toward the public record (the /performance headline, the
public API, the landing-page comparison table and the signed daily ledger)? Today there are
**five slightly different rules**, so these numbers do not quite agree with each other.

**This overlaps with the new VIP tier (#148).** You have already decided the following:
- the VIP bot is `bot_combined_1x2_ev5_v1`;
- each VIP pick carries an EV5 or EV8 label;
- VIP picks appear on /performance **only after they settle**;
- live VIP picks go only to a private channel.

The new table makes "public / VIP / not published" a setting on each bot. It also **stamps the
setting on each pick at the moment it is made**, so the website and Telegram always agree.

| Option | What it means | What could go wrong | Extra days |
|---|---|---|---|
| **A. A pick counts if its bot was published (public or VIP) when the pick was made.** Public picks show when they are made; VIP picks show only after they settle; in-play picks and paper bots never count. The same rule applies on every page. | One number everywhere. Switching a bot off later does **not** remove its past picks, so losers cannot be hidden. | The headline numbers change once when we switch. We say so on /methodology. The signed daily ledger gets a new version marker rather than rewriting old files. | 1.5 |
| **B. Keep today's rule** (the bot's quality label: "calibrated" or "beta") | Numbers stay as they are. | The label also controls five other things, including which bots feed other bots. Changing one bot's label would move the public record. The five rules stay out of sync. | 1 (unify only the wording) |
| **C. Let each page keep its own rule** | No work. | The pages keep disagreeing. | 0 |

**Recommendation: A.** It is the only option where "what we offered" and "what we report" are
decided in one place, and it already fits your VIP decisions.

---

## Two things the design assumes you have already decided (tell us if not)

1. **Retired bots keep collecting paper picks** so they can be re-checked later (your decisions of
   05-20 and 09-18). The new design keeps that behaviour, with a clear per-bot "collecting / stopped"
   setting.
2. **The real-money switches stay exactly as built today** (pause, arming, per-bot eligibility,
   audit log). Phase 5 does not move or merge them. It only changes where the placer *reads picks
   from*.

## Summary of recommendations

| # | Decision | Recommendation | Extra days |
|---|---|---|---|
| 1 | Timing copies | B: first sighting is the pick, repeats go to an archive table | +1 |
| 2 | Official CLV for old model picks | A: fair number, blank where it cannot be computed | 0.5 |
| 3 | Price used for profit | B: price actually on offer, everywhere | 0.5 |
| 4 | Model top-pick log | A: leave alone | 0 |
| 5 | Old customer tracker | A: retire, as a separate small task | 0.25 |
| 6 | Public track-record rule | A: "published when made", with VIP shown after settling | 1.5 |
