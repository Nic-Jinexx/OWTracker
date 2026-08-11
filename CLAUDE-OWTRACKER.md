# PROJECT: OWTRACKER — Local Overwatch Match and Player Tracker

## Mission
Build a local-only desktop tool that ingests Overwatch 2 scoreboard screenshots, extracts match and per-player statistics, and stores them in a personal SQLite database. The operator double-clicks a launcher, a local web server starts, the browser opens, screenshots are dropped in, extracted values are reviewed and corrected, and the match is committed. Over time the database answers questions like "what is my win rate on Antarctic Peninsula", "how often do I win when LECHEFFEUR is on my team", and "which heroes do I perform best against".

The finished artifact is a zip folder. Unzip it, double-click `run.bat`, and it works — on a machine that has never had Python installed and is not connected to the internet.

Work autonomously. When a decision is ambiguous, choose the option that makes bad extraction visible rather than silently wrong.

## Non-negotiables
- **No AI, no models, no network.** There is no API key, no `.env`, no vision service, no telemetry, no cloud storage, no account system, and no outbound network call of any kind. Extraction is deterministic template and glyph matching. Running the tool costs nothing.
- Python 3.12, FastAPI, uvicorn, SQLite. Third-party dependencies limited to `fastapi`, `uvicorn`, `numpy`, `Pillow`, `opencv-python-headless`, and their transitive deps. No OCR engine, no PyTorch, no separately-installed binaries.
- Frontend is plain HTML, CSS, and vanilla JS served as static files. No build step, no npm, no framework.
- Ships as a self-contained folder: an embedded CPython runtime with dependencies pre-installed beside it. `run.bat` invokes the bundled interpreter, binds uvicorn to 127.0.0.1 on a fixed port, and opens the default browser. No installer, no PATH edits, no admin rights, no first-run download.
- All operator data under `./data/`: `owtracker.db`, `screenshots/`.
- Reference libraries (`seed/glyphs/`, `seed/hero_hashes.json`) are static build artifacts committed to the repo and shipped with the app — not operator data.

## Invariants (do not violate at any milestone)
1. No match is written to the database without passing through the human review screen.
2. `result` (win, loss, draw) is a required non-null column. It is not present in any screenshot and must be set by the operator. It is the *only* required field — every other column is nullable and a sparse match is a valid match.
3. Every extracted field carries a `source` (`template`, `manual`) and a `confidence` float. For template matches, confidence is the normalized match score. Fields below the configured threshold render highlighted in the review UI.
4. Display names are logged verbatim as they appear on the scoreboard. No discriminator, no normalization, no alias merging. `players.display_name` is unique and serves as the identity. A row that cannot be attributed to a name has `player_id` NULL — it still records team, hero, and stats.
5. Screenshots are archived on commit and never auto-deleted. Store the SHA-256 of every ingested file and refuse to create a second draft from an already-committed file without an explicit override.
6. Team size is never hardcoded. Support 5v5 and 6v6, and tolerate incomplete rosters.
7. A hero appears at most once per team but may appear on both teams in the same match. Identical portraits in the blue block and the red block are expected and correct. Never deduplicate heroes across blocks, and never treat a repeat as an extraction error. Team assignment comes from which block the row sits in, not from the hero.
8. Absolute pixel coordinates from a single reference screenshot are never used for cell location. Everything is anchor-relative and scale-normalized.
9. Commit is a single database transaction. A failed commit leaves no partial match.
10. There is exactly one commit path. Manual entry and extraction both populate a draft; `POST /drafts/{id}/commit` is the only code that writes a match.
11. At most one `match_players` row per match may have `is_me` set, enforced by a unique partial index. If a draft has any player rows, exactly one must be marked, enforced at commit.

## Data Model

### heroes
`id`, `name`, `role` (tank, damage, support). Seeded from `seed/heroes.json`. Must be trivially extendable when new heroes ship.

### maps
`id`, `name`, `mode` (control, escort, hybrid, push, flashpoint, clash). Seeded from `seed/maps.json`.

### ranks
`id`, `tier` (Bronze…Champion), `division` (5…1), `ordinal` (int, globally sortable), `name` (display, e.g. "Gold 3"). Seeded from `seed/ranks.json`. Sorting and range comparisons use `ordinal`; display uses `name`.

### players
`id`, `display_name` (unique), `first_seen`, `last_seen`, `games_seen`, `notes`. Get-or-create on exact display name. A player who changes name becomes a new row, which is acceptable.

### player_nameplates
`id`, `player_id`, `phash`, `width_px`, `first_seen`, `times_matched`. Many rows per player — different title lines, capture resolutions, and cosmetics each produce a distinct crop and each becomes another row that resolves to the same person. This is how names are recognized without OCR; see Extraction below.

### matches
`id`, `played_at`, `map_id` (nullable), `mode` (nullable), `result` (not null), `team_size`, `duration_seconds` (nullable), `rank_range_low` / `rank_range_high` (nullable, FK to `ranks`), `notes`, `created_at`.

### match_players
`id`, `match_id`, `player_id` (**nullable**), `team` (`ally` or `enemy`), `is_me` (bool), `role`, `hero_id` (nullable), `eliminations`, `assists`, `deaths`, `damage`, `healing`, `mitigation`. All stat columns nullable to tolerate hidden enemy boards. `role` is stored separately from `heroes.role` because open queue exists and because an unknown hero may still have a legible role icon.

### match_bans
`id`, `match_id`, `hero_id`, `slot_index`. Team attribution is not shown in game, so bans are match-level only.

### match_sources
`id`, `match_id`, `file_path`, `file_sha256`, `kind` (`endgame_report` or `in_game_scoreboard`), `ingested_at`.

### field_provenance
`id`, `table_name`, `row_id`, `column_name`, `source`, `confidence`. Written on commit for every non-manual field. Also records the losing value when two sources disagreed.

### drafts
`id`, `created_at`, `updated_at`, `status` (`open`, `committed`, `abandoned`), `payload` (JSON), `committed_match_id` (nullable, `ON DELETE SET NULL` — deleting a match releases the draft that produced it and leaves the ingest record standing, but a released draft still may not be re-committed). The payload holds the entire in-progress `DraftMatch`: rows, per-field source and confidence, attached files, detected kind. Real tables only ever contain committed data, so `result` stays NOT NULL and no browse or aggregate query needs a status filter. The extractor's output shape can evolve without a schema migration.

### tags
Fixed vocabulary of operator-assigned dots: `code`, `tag_set` (`role` | `free`), `color`, `label`, `sort_order`. Six rows, seeded by the migration. The `role` set's codes are literally `tank` / `damage` / `support` so `player_tags.tag_code` joins against `match_players.role` — that join is why this is a table and not six boolean columns.

### player_tags
`player_id`, `tag_code`, `created_at`. Sparse by design: most people on a scoreboard are strangers who will never be tagged.

### settings
`key` (PK), `value`. Operator configuration: `my_display_name`, `confidence_threshold`, `default_team_size`, `schema_version`. Editable in-app, captured by the one-click database backup, and readable in any SQLite browser.

### Migrations
Numbered SQL files in `migrations/` (`001_init.sql`, `002_*.sql`, …) applied in order at startup inside a transaction each, with the applied version tracked in `settings.schema_version`. Plain SQL, no dependency, no ORM. Manual matches logged during early milestones survive every later schema change.

## Screenshot Types

**Endgame report.** Two team blocks separated by a VS divider, blue block on top is the operator's team, red block below is the enemy. Columns are E, A, D, DMG, H, MIT. Each row has a role icon, a hero portrait, a nameplate, and an optional title line beneath the name. This screenshot is authoritative for all per-player statistics.

**In-game scoreboard (Tab).** Contains the hero ban row across the top, the mode and map name in the header, the match clock, the rank range, and a roster that may include ability or ultimate icons. Statistics on this shot are mid-match and stale. Use it only for map, mode, bans, rank range, and roster confirmation. Never let it overwrite a stat that came from the endgame report.

The two screenshots are captured at different moments and neither depends on the other. Each must be independently processable. Submitting both attaches both to one draft; where they disagree on a statistic, the endgame report wins. With only an endgame report, map and bans are entered manually. With only an in-game shot, statistics are recorded as-is and flagged mid-match.

## Ingestion and Draft Lifecycle

Grouping is asserted by the operator, never inferred. Whatever is in the drop zone at submit becomes one draft. A separate endpoint attaches an additional file to an existing draft later, for when the second screenshot turns up after the fact. The app never pairs files by timestamp or roster similarity — back-to-back games with the same lobby are common and a mis-pairing would fabricate a match.

Uploaded files are hashed and written to `data/screenshots/drafts/<draft_id>/`. On commit they move to `data/screenshots/<match_id>/` and the final path is recorded in `match_sources`. Abandoned drafts leave a clearly-named folder that is safe to sweep.

**Source precedence on merge.** When a newly attached file produces a value that disagrees with an existing one:
- Field is empty → fill it.
- Field came from extraction → higher-precedence source wins (endgame report over in-game scoreboard), silently, with the losing value recorded in provenance. The in-game shot is a mid-match snapshot, so it disagrees with the endgame report on nearly every stat by design; prompting on those would mean ~60 prompts per match.
- Field was edited by the operator → **stop and ask**, per field, with both values shown. Nothing the operator typed is ever discarded without their say-so.

## Extraction

A single extractor, `LocalExtractor`, taking an image path and a declared screenshot kind and returning a `DraftMatch`. It is fully deterministic and fully offline.

Everything is template matching against reference bitmaps, not character recognition. The scoreboard is a rendered UI with a fixed font, fixed layout, and fixed hero art — after scale normalization, a `7` in the DMG column is the same bitmap every time. Matching it directly is both simpler and more accurate than OCR, and the match score is a genuine confidence value rather than an estimate.

### Stages
1. **Localize.** Find the scoreboard by anchors: the header strip containing the column labels, and the VS divider between team blocks. Compute a canonical transform and normalize to a fixed working width. Every downstream stage operates in normalized space.

   The anchors are found **structurally** — by colour and geometry — not by template matching, which is the one place the "everything is template matching" rule does not apply. The header strip is the only wide flat desaturated bar in the shot and its horizontal extent *is* the board's left and right edge, so it yields the transform directly, with no reference bitmap and no search over scale. The canonical space is the **scoreboard's own width**, not the screen's, so a crop and a full-screen capture of the same match normalize identically. Vertical extent is deliberately not normalized to a constant: a title line under a player's name makes that row taller, so block height genuinely varies between shots at the same resolution. See `app/extract/localize.py`.

   The in-game Tab board has its own localizer, `app/extract/tab.py`, sharing every anchor above. The two boards are near-identical to the anchors — same grey strip, same six labels, same two coloured blocks — and are told apart only by the *spacing* of those labels: the Tab board packs them left to reserve its right edge for the per-player mute buttons, putting MIT 74 canonical units early where the endgame corpus agrees within 1.6. Each localizer refuses the other's screenshot on that signature, which matters because a mis-classified board does not fail, it returns a full set of plausible wrong numbers. On top of the board, the Tab localizer finds the chrome the endgame report has no equivalent of: hero bans, mode and map, the match clock, the rank range. Chrome is anchored to the *screen*, not the board, so its distance from the board changes with aspect ratio and nothing may key off a fixed offset; each element is found structurally and each is optional, since Quick Play has neither bans nor a rank range.
2. **Segment teams.** Split on background hue (blue block versus red block) rather than assumed position.
3. **Segment rows.** Detect horizontal separators inside each block. Do not assume a row count. Merge the title line into its parent row rather than treating it as a seventh player.
4. **Locate columns.** Derive column x-centers from the header labels detected in stage 1, not from constants.
5. **Read numerics.** Crop the cell, threshold, split into connected glyph components, and match each against the digit atlas in `seed/glyphs/` by normalized cross-correlation. Strip thousands separators. A cell whose glyphs fall below threshold is emitted null at zero confidence rather than guessed at.

   **Only digits are matched.** The thousands comma is classified by height, not correlated against a template — measured on held-out data every digit scores 0.839-0.916 while the comma scores 0.651-0.799, because it is a 6x10 mark whose correlation is inherently noisy. Matching it turned 45 of 144 perfectly readable cells into refusals. It carries no information and is stripped anyway. Its *position* is spent on a checksum instead: every separator must have a multiple of three digits to its right and at least one to its left. That check is not optional — classifying by size means a mis-sized digit would otherwise be discarded as punctuation and the cell read confidently wrong, which is the one thing milestone 4's hard stop forbids. See `app/extract/reader.py`.

   Glyphs that touch with no blank column between them are split at their waist, and only near where a boundary must fall if the glyph is *n* digits wide. When the split does not fire the cell is unreadable, never wrong.
6. **Identify heroes.** Perceptual hash of the portrait crop against `seed/hero_hashes.json`, with a distance threshold. Below threshold, emit unknown. Crop tightly to the portrait interior so the team-colored row background does not dominate, or normalize the background out before hashing.
7. **Recognize nameplates.** A hit resolves to a known player and fills the name at `source='template'` with the match score as confidence. A miss leaves the name blank with the crop surfaced in the review UI.

   **Not a perceptual hash.** Measured on the corpus, a 64-bit pHash cannot separate nameplates at all: an animated plate puts the same player 24 bits from himself while different players sit 16 apart. The signature is instead a fixed canonical rectangle masked to *bright and unsaturated* pixels — the white lettering, with the coloured glow discarded — downsampled to 480 bits and compared by Hamming against every stored signature, nearest-neighbour.

   **Several signatures per player, accumulating.** Every confirmed name stores that appearance, so an animated plate gradually gets covered at several frames and recall improves with use. `player_nameplates` was always one-to-many; this is what for.

   **The threshold is set against strangers, not against accuracy.** Leave-one-out always has the right answer in the library and will happily justify a loose tolerance; most people in a lobby have never been typed in. Nearest-same-player is within 35 bits, nearest-different-player never under 44. See `app/extract/nameplates.py`.

### Reference libraries
Both are built once by `tools/` scripts and committed as seed data.

- `seed/glyphs/` — one reference bitmap per digit plus the comma, cut from a clean endgame screenshot at the canonical working width. Rebuilt only if a game patch changes the scoreboard font.
- `seed/hero_hashes.json` — one reference hash per hero, built from default hero art. Scoreboard portraits are default art, not equipped skins, so this library is stable and does not grow with use.

Keep one escape hatch for hero releases: an unmatched portrait surfaces in the review UI with a crop and a hero picker, and confirming writes a new hash to the library. This is for new heroes and art refreshes, not routine operation. If unknowns appear regularly, the crop geometry or the distance threshold is wrong and should be fixed rather than papered over with more hashes. Digits need no such hatch — an unmatched number is simply typed.

### Names
Player names are the one field this approach cannot read, and the spec does not pretend otherwise. The stylized italic font over a glowing gradient nameplate defeats template matching as thoroughly as it defeats OCR.

Names are therefore **recognized, not read**. The review grid shows the cropped nameplate enlarged; the operator types the name once; the crop's hash is stored in `player_nameplates` against that player. Every later appearance of that person matches the stored hash and auto-fills. Recurring teammates — the people the interesting queries are actually about — cost one typing each, ever. Strangers stay blank, which invariant 4 already permits.

## Review UI
Single page, three sections.

**Drop zone.** Accepts one or two images. Shows a thumbnail with the detected kind and lets the operator correct the kind.

**Review grid.** Both team blocks rendered as editable tables mirroring the scoreboard layout. Every cell is an input. Low-confidence cells are highlighted amber, unknown heroes red. Each row shows the cropped nameplate beside the name field. The operator's own row is flagged from `settings.my_display_name` and can be reassigned by clicking.

**Match metadata bar.**
- Result: three large buttons, Win, Loss, Draw. Nothing saves until one is chosen.
- Map: grid of buttons grouped by mode. Pre-selected if extracted.
- Hero bans: grid of hero portraits, click to toggle. Pre-selected if extracted.
- Optional duration, rank range, and notes fields.
- Save button, disabled until result is set.

## Browse and Analysis
- Match list with date, map, result, own hero, and a link to the archived screenshots.
- Player detail page: games seen with and against, win rate with and against, average statistics, most-played heroes, first and last seen, notes and endorsement dots.
- **Overall page**, at `#/overall` for the operator and `#/overall/{player_id}` for anyone else. Everything derivable from the data, organised: maps, heroes, roles, modes, format, who you win and lose with, bans, comps, allied and opposing heroes, opponents, averages split by role, streaks, and a hero-by-map cross-tab. A sticky rail carries the top five win rates and the top five most-banned heroes and does **not** change when the subject does, so another player's page can be read against your own numbers.
- Aggregates are computed from any **subject's** point of view by `app/analysis.py`. `matches.result` is stored from the operator's perspective, so every subject-relative tally remaps the outcome; for the operator that remap collapses to `m.result`.
- Leaderboards rank on the **Wilson score lower bound**, so 68% over 40 games outranks 100% over 2 without a hand-tuned fudge factor, and a map, a hero and a teammate can share one list. A separate minimum-games gate decides what is worth showing at all.
- Aggregates page with filters for hero, map, mode, role, teammate, rank range, date range.
- Any win rate computed from fewer than 5 games renders greyed with the sample size shown. Do not display a bare percentage on tiny samples.
- CSV export of all tables and a one-click database file copy for backup.

## Hard Problems (address explicitly, do not paper over)
- **Localization is the whole project.** With no model to fall back on, if anchor-relative detection is not solid across resolutions, nothing downstream works. This is the highest-risk component and gets the debug overlay and the earliest hard stop.
- **Mirror picks.** The same hero on both teams produces two identical portraits in one screenshot. This is normal. Any logic that assumes hero uniqueness across the match, or that uses hero identity to infer team, is wrong. Team comes from block position and background color only.
- **Stylized italic font over gradient nameplates with glow and cosmetic effects.** Unreadable by template matching. Handled by type-once hashing, not by reading.
- **Nameplate hash stability is unproven.** If the gradient animates, the same player may not hash consistently between matches. Verify before building on it: hash one player's nameplate from two different screenshots and compare distance.
- **Title lines under names** ("6v6 Champion", "Peasant", "Tourist") sit inside the row and will be misread as separate players or appended to names.
- **Resolution and aspect ratio variance.** Ultrawide, windowed, cropped, and upscaled captures all occur. Anchor-relative localization is mandatory, and glyph matching only works because everything is normalized to a canonical width first.
- **Anonymized enemy rosters.** Placeholder avatars and hidden heroes are normal. Schema tolerates nulls; the UI must not demand completion.
- **Mid-match hero swaps.** The scoreboard is a snapshot. One hero per player per match is stored. Note this limitation in the UI so the data is not over-trusted.
- **The clock is a countdown, not a duration.** Never derive match length from it.
- **Result, score, and ban attribution appear nowhere.** Manual entry, permanently.
- **Duplicate submission.** The same match commonly produces two files. File hashing plus a soft warning on near-identical rosters within a short time window.

## Milestones (in order, commit after each)

1. **Manual-only skeleton.** `run.bat`, FastAPI app, full schema via migrations, seed data (heroes, maps, ranks), settings, drafts, static page, complete manual match entry with no images involved. *Hard stop: manual entry must be fully usable and the browse pages must work before any extraction code is written.*
2. **Ingest and localize.** File upload, hashing, archival, anchor-based scoreboard detection, canonical transform. Ship a debug overlay endpoint that renders detected boxes over the source image. *Hard stop: the overlay must be correct on both sample screenshots and on at least two other resolutions before proceeding.*
3. **Reference libraries.** `tools/build_glyph_atlas.py` and `tools/build_hero_hashes.py`; commit `seed/glyphs/` and `seed/hero_hashes.json`.
4. **LocalExtractor.** Row and column segmentation, digit reading, hero identification, map/mode/ban extraction from the in-game shot, review grid wired to real output. *Hard stop: template matching must resolve every hero in the sample corpus, including mirror picks on both teams, and every stat cell must read correctly or report low confidence — never a confident wrong number.*
5. **Nameplate recognition.** Crop, hash, `player_nameplates`, type-once flow in the review grid.
6. **Aggregates and player pages.**
7. **Filters, CSV export, backup.**
8. **Packaging.** Embedded Python plus pre-installed dependencies, `tools/package.py`, produce the distributable zip and verify it on a clean machine.
9. **Polish.** Keyboard-first review flow, dark theme matching the game palette.

## Quality Bar
- Sanity tests for spawn-equivalent invariants: team sizes match between blocks or are explicitly flagged, at most one `is_me` per match and exactly one when player rows exist, no orphaned `match_players`, no committed match with null result.
- Keep a `samples/` directory of screenshots with hand-written expected-output JSON and a test that scores extractor accuracy against it. Every extractor change reports the delta.
- The server must never crash on a malformed image. Bad input produces an empty draft and a visible error, not a stack trace.
- The database must be openable in any SQLite browser and make sense without the application.
- The shipped zip must run on a machine with no Python, no internet, and no admin rights. This is verified by actually testing it there, not assumed.
