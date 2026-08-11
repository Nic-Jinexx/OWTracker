# OWTracker — updates

Running log of tasks and findings. Newest at the top.
Current version: **v0.2.0**

## 🔄 WIP

- 🔄 **Nobody has looked at the new pages in a browser.** The Overall page, the endorsement
  dots, the notes affordance and the inline match screenshots are all built, wired and
  covered by passing tests, but the Chrome extension shows an error page for
  `localhost`/`127.0.0.1` (it needs site permission, which cannot be granted from here).
  Every selector, template id, route handler and helper was cross-checked statically
  instead. **Please open `http://127.0.0.1:8770/#/overall` and look.**
- 🔄 **36 of 49 hero clusters are unlabelled.** `data/hero_labels/portraits.png` is a
  contact sheet, one row per cluster; `labels.json` sits beside it. 13 clusters were
  pre-filled and are marked `confirmed_by: assistant guess` — those need checking, not
  trusting. Name the rest and re-run `tools/build_hero_hashes.py --write`.
  (`tools/build_hero_hashes.py` was fixed during wrap-up: regenerating `labels.json` used
  to drop `confirmed_by`, laundering every guess into a confirmation on the next run.)

## 🚫 BLOCKED

- 🚫 **Tab-shot roster reading** — the one in-game sample has its red block cut off
  mid-block (3 of 6 rows in frame). The localizer reads the blue block's 6 rows correctly
  and warns that the red one is clipped, but a clipped sample cannot validate roster
  reading either way. Worth capturing a complete one. The chrome (bans, map, clock, rank)
  is done and does not depend on this.
- 🚫 **A second in-game sample.** Every chrome threshold in `app/extract/tab.py` is fitted
  to one screenshot, and one sample cannot show whether a constant generalizes — it can
  only fail to contradict it. The endgame constants earned their confidence from seven
  shots across four board widths; these have earned none yet. A second Tab capture, at a
  different aspect ratio and ideally from Quick Play (no bans, no rank range, so absence
  gets exercised), is what would turn the guesses into measurements.

## 📋 TODO

- [ ] Label the remaining hero clusters (see WIP above) — hero, comp and map-hero stats
      stay thin until this is done
- [x] ~~Build the Tab localizer: ban row, mode/map header, clock and rank range are all
      sitting in `ingame_tab_2511x1006.png`~~ Done in **v0.2.0** — `app/extract/tab.py`.
- [ ] **Readers for what the Tab localizer found.** The boxes are located; nothing turns
      them into values. Bans can go straight to `heroes.identify` once the clusters are
      labelled; the map/mode name needs a word reader or a manual field; rank badges need
      their own hash library. `LocalExtractor` still refuses `in_game_scoreboard` until
      then, deliberately — a draft of empty fields would look like a shot that was read.
- [ ] Hand-write `expected.json` for more samples (2 of 7 done)
- [ ] Capture a 5v5 sample and a win — the corpus is all 6v6 losses
- [x] ~~`matches.py:DELETE` will fail on any match a committed draft points at:
      `drafts.committed_match_id` has no `ON DELETE`. Hit this while wiping test data.~~
      Fixed in **v0.2.0** by migration `003` — see DONE.

## ✅ DONE

### v0.2.0 — update 1.2

- ✅ **The Tab localizer** — `app/extract/tab.py` finds the in-game board and, above it,
  the four things an endgame report cannot tell you: the 5 hero bans, the mode/map header,
  the amber match clock and the two rank-range badges. All correct on the one sample; see
  `tools/tab_report.py` for the overlay. It **locates and does not read**, same contract as
  `localize.py`, so a ban box goes straight to `heroes.identify` and the map name waits for
  a reader. Chrome is screen-anchored rather than board-anchored — the map header sits
  2851 canonical units right of the board on a 2.5:1 screen and would sit far closer on
  16:9 — so nothing keys off a fixed offset; elements are found by colour, by regular
  spacing, and by which side of screen centre they fall on. Every one is optional, because
  Quick Play has no bans and no rank range.
- ✅ **The two localizers can no longer be confused for each other** — and the old reason
  they couldn't was wrong. See the finding below.
- ✅ **Deleting a match works again** — migration `003` gives
  `drafts.committed_match_id` an `ON DELETE SET NULL`, so `DELETE /api/matches/{id}` no
  longer dies on a foreign key. SET NULL rather than CASCADE: a committed draft is the
  record of an ingest that really happened, and its payload holds the per-field sources
  that produced the match. The draft still refuses a re-commit, which would resurrect a
  match the operator deliberately deleted. Verified on a copy of the real database — 7
  drafts and 6 committed links survived the table rebuild. The version test now derives
  the expected number from `migrations/` instead of hardcoding it.
- ✅ **Overall page** — `#/overall` and `#/overall/{id}`. A sticky rail that stays on *your*
  numbers whoever is selected (top-5 win rates, top-5 most-banned, needs-work), and a
  panel grid of maps, won/lost-with, heroes, roles, modes, format, bans, comps, allied and
  opposing heroes, opponents, averages and a hero-by-map cross-tab. Every panel shows its
  top rows as bars with the full table one native `<details>` away. The two heaviest
  queries load only when opened.
- ✅ **Stats from anybody's point of view** — `app/analysis.py` parameterises the subject.
  `GET /api/stats` is byte-identical; `/api/stats/overview`, `/top`, `/comps`, `/crosstab`
  and `/allies` are new.
- ✅ **Wilson ranking** — 2-0 scores 0.342 and 27-13 scores 0.520, so a small perfect record
  cannot outrank a large good one, and because the score is a probability a map, a hero and
  a teammate share one leaderboard honestly.
- ✅ **Comps, three ways** — role shape (`2-2-2`, `3-1-2`) as the headline, hero duos and
  exact lineups behind "see more". A roster missing anyone is counted as unclassified
  rather than shaped.
- ✅ **Nameplate recognition** — type a name once and it fills itself in forever. Proven end
  to end: four names typed on one screenshot, all four auto-recognized on a different one
  with nothing typed. `app/extract/nameplates.py`.
- ✅ **Hero identification** — `app/extract/heroes.py` + `tools/build_hero_hashes.py`, which
  crops every portrait in the corpus, clusters them, and emits a contact sheet to label.
  10 heroes confirmed so far; mirror picks resolve on both teams.
- ✅ **Player notes and endorsement dots** — `migrations/002_player_tags.sql`. Two sets of
  three; the role set's codes are literally `tank`/`damage`/`support` so they join against
  what a player actually plays. Notes are invisible until written.
- ✅ **Match detail** shows every player's numbers, roles, ban list, notes and the
  screenshots **inline** — the screenshot link had been a 404 on every match ever committed.
- ✅ **6v6 is the default.** 5v5 is a filter and a breakdown, never an assumption.
- ✅ **Repackaged** — `dist/OWTracker.zip` (72 MB) now contains the localizer, atlas,
  reader, analysis engine and both hash libraries. Verified running with `PATH=""`.
- ✅ **214 tests**, up from 130.

### v0.1.0


- ✅ **Reader wired into the app** `v0.1.0` — `LocalExtractor` now localizes, reads all 72
  stat cells, and returns a draft fragment stamped `source='template'` with a real
  confidence per field. Proven end to end against an isolated database: screenshot →
  draft → committed match → **72/72 statistics in SQLite matching the hand-written
  truth**, readable with plain `sqlite3`. Unread cells are emitted blank, which the merge
  rules ignore, so extraction can never overwrite something typed. 15 new tests, 130 total.
- ✅ **M3 (digit half) + the digit reader** `v0.1.0` — `seed/glyphs/` holds 11 greyscale
  templates cut from `kingsrowloss` by `tools/build_glyph_atlas.py`, labelled from the
  hand-written ground truth rather than by eye. `app/extract/cells.py` (cell geometry,
  glyph segmentation), `app/extract/glyphs.py` (atlas + NCC matching),
  `app/extract/reader.py` (cell → number). **Held-out accuracy: 72/72 cells, 100%, zero
  wrong**, on a sample at a different resolution that the atlas never saw.
  `tools/score_digits.py` is the accuracy harness. 25 new tests, 115 total.
- ✅ **`git init`** `v0.1.0` — repository created, 49 files in one initial commit on
  `master`. `data/`, `dist/`, `.venv/`, `__pycache__/` and `RESUME.md` are ignored; the
  `samples/` corpus is tracked. No remote configured.
- ✅ **M2 hard stop PASSED** `v0.1.0` — four more screenshots arrived, three of them
  **native full-screen captures** at 2402×1349, 2502×1364 and 2329×1312. All three localize
  correctly with the board found inside the game's own chrome. Board widths across the
  corpus now span **950–1034 px over four distinct capture geometries**, and the six column
  centres stay inside **1.7 canonical units of 1920**. That is the gate: the game does lay
  the scoreboard out proportionally, so width normalization is a measurement now, not an
  assumption.
- ✅ **Corpus manifest** `v0.1.0` — `samples/manifest.json` records kind, capture type, size
  and board width per image; an undocumented screenshot fails the suite. Tests split the
  corpus by declared kind instead of globbing, so endgame geometry is never asserted
  against a Tab shot.
- ✅ **M2 — localizer, overlay, debug endpoints** `v0.1.0` — `app/extract/localize.py`
  finds the board, header strip, both team blocks, the six column centres and every row
  separator; `app/extract/overlay.py` draws it; `GET /debug/overlay/...`,
  `/debug/localize/...` and `/debug/samples` serve it. 30 new tests, 90 total, all green.
  Verified by eye on all four samples and over real HTTP.
- ✅ **samples/ corpus** `v0.1.0` — four screenshots moved out of the project root, plus
  `samples/README.md` and a hand-written `kingsrowloss.expected.json` (all 12 rows of
  stats, names, titles, roles). Two rules encoded and tested: assert only what the image
  shows, and list what was not verified with a reason.

- ✅ **M9 — polish** `v0.1.0` — dark theme, Enter walks down a stat column (continues into
  the enemy block), `1`/`2`/`3` set result, `Ctrl+Enter` saves.
- ✅ **M8 — packaging** `v0.1.0` — `tools/package.py` builds `dist/OWTracker.zip` (72 MB):
  embedded CPython 3.12 with deps pre-installed. Verified running with `PATH=""`.
- ✅ **M7 — filters, CSV export, backup** `v0.1.0` — nine composing filters, per-table and
  denormalized CSV, SQLite-backup-API copy (safe under WAL).
- ✅ **M6 — aggregates and player pages** `v0.1.0` — with/against win rates, breakdowns by
  map/mode/hero/teammate/opposing hero. Rates under 5 games render greyed with sample size.
- ✅ **M2 (ingest half)** `v0.1.0` — upload, SHA-256, duplicate refusal + explicit override,
  archival to `drafts/<id>/`, kind correction, malformed-input handling, DCT pHash.
- ✅ **M1 — manual-only skeleton** `v0.1.0` — schema + numbered SQL migrations, seeds
  (44 heroes, 31 maps, 40 ranks), settings table, drafts as JSON payloads, single commit
  transaction, manual entry, browse pages. Gate passed: 5 matches logged via the real HTTP
  API, survived restart, DB readable standalone.
- ✅ **Spec rewritten** `v0.1.0` — removed the Claude vision extractor and all network
  paths. Decision: no AI, no models, no network, no cost. Template matching only.

## 🐛 Findings

### From update 1.2

- **A refusal that fires for the wrong reason still looks like it works.** The endgame
  localizer had correctly refused the Tab shot since M2, and the manifest, a code comment
  and `RESUME.md` all explained why: the Tab layout's extra columns sit outside the header
  strip, so the blue block overruns it by ~467 canonical units. None of that was true. The
  Tab board aligns with its header strip to within 2 units, exactly like an endgame report;
  the 467 units were Junker Queen's blue hair in the hero-info panel 140px to the right,
  swallowed because a block's horizontal extent was measured as min-to-max of every
  qualifying column instead of its widest contiguous run. The right answer was passing for
  three documented-but-false reasons, and the same latent bug would have mis-measured any
  endgame shot with something blue beside the board. The real discriminator turned out to
  be the column signature: the seven endgame samples agree within **1.6** canonical units
  and the Tab board is off by **74.5**, a 45x margin that was sitting in the data the whole
  time. Caught only by measuring the claim instead of reading it.
- **"Constant pitch" is vacuous for two items.** The ban-row detector looked for equal-width
  runs at a constant pitch, which two runs always satisfy — one pitch is trivially
  consistent with itself — so any pair of similar red marks anywhere on screen read as a
  ban row. Fixed by requiring adjacency (pitch ≤ 1.6x the width; bans are a contiguous
  strip). Found by a unit test on synthetic runs, which is the only place a negative case
  exists: a corpus of real Tab shots contains no examples of things that are *not* bans.
- **A provenance marker that a round-trip erases is worse than no marker.**
  `build_hero_hashes.py` regenerated `labels.json` carrying forward only the hero name, so
  the `confirmed_by: assistant guess` flags written to distinguish unverified guesses from
  human decisions were gone by the time the very next command finished — and two docs
  claimed they were still there. Caught only by re-reading the file during wrap-up rather
  than trusting that writing it had been enough.
- **`matches.result` is stored from the operator's perspective.** A player on the enemy
  team of a match I lost *won* that match. Every subject-relative tally has to remap the
  outcome; get it wrong and every enemy-side player's page is exactly inverted, which looks
  entirely plausible and would never be noticed. For `is_me = 1` the remap provably
  collapses to `m.result`, so the pre-existing test suite staying green *is* the proof the
  generalisation was a no-op.
- **"Teammate" is not "not me".** The old `by_teammate` excluded the operator with
  `is_me = 0` — right only while the subject is the operator. For anyone else the operator
  is an ordinary ally.
- **Leave-one-out testing hid a false-positive problem.** Nameplate recognition scored
  27/27 at a 60-bit tolerance, which looked like proof. It was not: leave-one-out always
  has the right answer somewhere in the library, and most people in a lobby have never been
  typed in. Testing against *strangers* found one matched to the nearest acquaintance at 49
  bits. The gap that matters is nearest-same-player (≤35 for 23 of 24) versus
  nearest-different-player (never under 44); the threshold is 35.
- **A perceptual hash cannot see through an animated nameplate.** Same player 24 bits
  apart, different players 16 — overlapping. NCC on the raw crop is worse and *inverted*:
  two glowing plates both reduce to a bright blob. Masking to bright-and-unsaturated keeps
  the letters and throws the glow away, and 480 bits of that is separable.
- **A settings default is not an operator assertion** (again, differently). Stamping
  `team_size` as `manual` made a 6v6 screenshot raise a conflict against a `5` nobody
  typed. Restamping it `default` then broke commit, because `field_provenance.source` is
  CHECK-constrained to `template`/`manual` — caught only by running the real thing, not by
  any unit test.
- **Blank draft rows are never committed**, so an unrecorded player leaves *fewer rows*,
  not rows full of NULLs. Roster completeness has to be measured against `team_size`, or a
  five-player team with one missing reads as a `1-2-1` comp instead of an incomplete one.
- **`paths.screenshot_url` assumed the data directory lives under the project root.**
  `portable()` had always tolerated a relocated data dir; the URL builder raised. They
  disagreed exactly when the paths were least obvious.
- **`drafts.committed_match_id` had no `ON DELETE`**, so deleting a match a committed draft
  pointed at failed with a foreign-key error and `DELETE /api/matches/{id}` never worked on
  a real match. Every *downstream* table cascaded correctly; the one reference pointing the
  other way, from the ingest record back at its match, was the one nobody thought to give a
  delete action. Fixed by migration `003` (SET NULL).

### From v0.1.0


- **A settings default is not an operator assertion.** `create_draft` stamped `team_size`
  from the setting as `source='manual'`, so a 6v6 screenshot raised a field conflict
  against a `5` nobody had ever typed — and a conflict blocks commit. Now stamped
  `source='default'`, which lets extraction correct it silently and record the old value in
  `superseded`. Worth watching for anywhere else a default is dressed up as a choice.
- **The roster DOM was built once and never rebuilt.** Since the extractor reads the row
  count off the image, a 6v6 screenshot dropped into a 5v5 draft put two players in the
  payload with no cell on screen — they would have committed without ever being seen. The
  grid now rebuilds when the payload's shape stops matching the DOM.

- **Do not template-match the thousands comma.** Measured on held-out data every digit
  scores 0.839–0.916 while the comma scores 0.651–0.799, so matching it turned 45 of 144
  perfectly readable cells into refusals. It carries no information and gets stripped
  anyway. Classifying it by height instead (digits 26–29 canonical units, separators 9–11)
  took the corpus from 68.8% to 98.6% in one change. Its *position* is now spent on a
  checksum: every separator must have a multiple of three digits to its right and at least
  one to its left, which is what stops a mis-sized digit being silently discarded as
  punctuation.
- **Adjacent digits sometimes touch with no blank column**, so projection segmentation
  cannot split them — but they meet at a thin waist (2–5 ink pixels against a 27-pixel
  stroke). Splitting there, and *only* near where a boundary must be if the glyph is n
  digits wide, fixed the last 2 cells. Searching the whole glyph would cut the thin leading
  diagonal off a `4`.
- **Role composition is not 2/2/2.** `endgame_fullscreen_2329x1312.png` is 3 tank / 1
  damage / 2 support — Open Queue. Never validate role counts.
- **Averaging the atlas across 11–36 instances per character left no ghosting**, which is a
  free check that the ground-truth labelling was consistent. A blurred template would have
  meant a misaligned pairing.

- Samples are **6v6** (12 rows) and are **crops**, not full-screen captures.
- **Zero stats render dimmed grey**, not white — glyph thresholding must not drop them.
- Title lines under player names are pervasive in real captures.
- **Structural anchors beat template matching for stage 1.** The header strip measures a
  constant BGR (218, 212, 208) in every sample and its horizontal extent *is* the board's
  left and right edge — the exact measurement the transform needs. No reference bitmap, no
  scale search. The spec said `matchTemplate`; this is a deliberate deviation, documented
  in the module docstring.
- **Per-pixel derivatives are not scale-invariant.** Upscaling spreads the same 14-unit
  luminance change over more pixels, so a slope threshold that works at 1× silently drops
  separators at 1.75×. Fixed by resampling the profile into canonical units first and
  measuring peak-to-trough *range* in a sliding window, which responds to a dark separator
  line and a shading step alike. Margin across the corpus at 0.5×–2.2×: weakest separator
  7.9, loudest row interior 0.44, threshold 5.0. A test locks that gap in.
- **Some rows have no nameplate at all** — four samples have them, up to three in one shot.
  M5 must treat a blank nameplate as normal, not as a failed crop.
- **The in-game Tab layout is not a wider endgame report.** It adds ultimate-charge and
  per-player mute columns that are team-coloured but sit *outside* the grey header strip,
  so the blue block overruns the header by ~467 canonical units. The alignment guard
  catches it and the refusal now names the likely cause. A Tab shot that localized
  "successfully" would be the worst outcome available — mid-match stats landing in the
  endgame report's slot, which the merge rules treat as authoritative.
- **A block with no title lines has uniform rows, and that is correct.** An early test
  demanded mixed row heights everywhere and failed on a perfectly good full-screen sample.
  The real invariants are "no row is double another" (catches a missed separator) and
  "somewhere in the corpus the taller title row shows up".
- Below about 0.6× the header glyphs stop being legible and localization fails at the
  column stage. That is the correct behaviour — it refuses rather than guessing — and the
  practical lower bound is now a test.
- Three bugs caught by tests: missing `created_at` binding in the match insert;
  `Query(...)` defaults making route functions uncallable outside FastAPI; an `ok` property
  that returned true for empty drafts because a blank draft still carries ten rows.


## update 1.2 
Okay, it looks good. A couple of things. When I submit a match, I would like to be able to click on it to see the scoreboard and my image I uploaded as well there. And because right now, when I have a match submitted and I click on it, I can't click on it and see everybody's numbers. I want to see that. I also want to see my image, so I want both of those. And then I also want a overall page. And on this page, I want the overall summaries for every player selected at the top. So at the top, I want to drop down. I want to be able to select the player's name And then have it display all of their stats I have tracked. So, how many wins and losses per map with the win-loss percentage, and then the details for that. So, I want to see I have you know a 65% win rate as this hero, as this role on this map, and I won't be able to do that for everybody in the database. You know, the names there. I also want to mention that this is primarily for 6v6 only. I do not care about 5v5, so we are focusing on 6v6 only  5v5 support is fine, but that is not default. That is whatever. This is for 6v6 I also want to be able to add a note to each player if I so desire. And the note only shows up if I type it there. If not, I don't want any field anywhere for it. And I want these notes to persist if I come across this player again. So let's say I come across this player and I type, you know, good comms in VC. I want to be able to next screenshot I upload, if he's there, I want it to be auto-filled as him is there. For example, in a lot of these screenshots, there is a player Derek, D-E-R-E-K, and I want the screenshots to be able to auto-fill these players. I believe it already does that, but if it recognizes it, I want the name auto-filled in based off of the screenshot. So the workflow loop here is: I play my games, I upload my screenshots, I fill in any extra data if needed, such as the hero band, you know, what's already there, that all is great. And then I get an overall stats page where I can see the most band heroes in my lobbies, right? According to my data, who I have the most band of, according to my data, what maps I went on the most, what players I win with the most, what players I lose with the most, what comps I win with the most, right? I want all of as many stats as we can derive from this data, which is like win-loss, all the heroes, just so much, so many numbers I can get from this. And I want this overall page to just dump all of these numbers with the options to see more. I want it well organized, well laid out, and you know, per player, so I can drop down and see the players that I already have logged in in my database to see them I also want to add my own custom endorsement system so I can give a player three different icons. We will go with an orange dot, a blue dot, and a green dot. The orange dot would be for, let's say, Tank player the blue dot will be DPS player and the green dot will be support player and then I want another set of endorsement dots. Let's go with a red dot, a pink dot, and a white dot the leave leave these dots open-ended so I can kind of keep track of them on my own, or someone else can kind of assign those to be whatever you want them to be, but we don't need to have that in app. That can just be a side outside of the app thing, just have those as dots that we can assign with no meaning So, my thought is: if I want to, I can go to my data tracker, I can open it up, I have all my data, and I can see: okay, so I win the most on this map when I play this hero. And I want to be able to see that for my teammate for all the other data I put in there. My goal is to improve myself and see that, okay, on this map, I play really well as this hero. So maybe on the overall page, have some like star stats and like the like at the top of the page, maybe the top right. Let's have a you know, like a top five stats and have that display my top five best performances, like my top five like win rates, be it a map win rate or a hero win rate or a teammate win rate. and then let's also have next to that a top five band heroes where I can see that the top five most banned heroes. you can also add cool little graphics to these we can make them like bars, like square bars And to reiterate, the goal of this app is to be a personal local only like database almost that I build up. 