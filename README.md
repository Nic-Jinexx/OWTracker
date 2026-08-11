# OWTracker

A local Overwatch match tracker. You drop in a screenshot of the endgame
scoreboard, it reads the numbers, and it keeps the history — maps, heroes,
teammates, win rates, who you actually win with.

Everything runs on your own machine. No accounts, no telemetry, no cloud, no AI
service. Nothing you put in leaves the computer, which is also why it costs
nothing to run.

## Download and run (no Python needed)

Grab `OWTracker-v0.2.0.zip` from
[Releases](../../releases/latest), unzip the **whole folder**, and double-click
`run.bat`. A console window opens and your browser goes to OWTracker; leave the
console open while you use it and close it to stop.

It bundles its own Python and every dependency, so it runs on a Windows machine
that has never had Python installed, with no internet connection and no
administrator rights. Windows may show a *"protected your PC"* box — choose
**More info → Run anyway**. That appears for any program that has not been
code-signed.

Your data lives in the `data` folder next to `run.bat`:

    data/owtracker.db      matches, players, settings — an ordinary SQLite file
    data/screenshots/      every screenshot you have submitted
    data/backups/          copies made by the Back up button

To move to a new PC, copy the whole folder. To back up, copy the `.db`.

## What it does

- **Reads the endgame scoreboard.** Eliminations, assists, deaths, damage,
  healing and mitigation for all twelve players, by template-matching the
  game's own digits — not OCR. Cells it cannot read confidently are flagged for
  you rather than guessed at.
- **Recognizes players.** Type a teammate's name once and their nameplate is
  recognized in every later screenshot, by perceptual hash.
- **Identifies heroes** from portraits, where the hero is in the reference
  library (see limitations).
- **Stats from anybody's point of view.** Not just yours — pick any player and
  the whole site re-renders around them. Maps, modes, heroes, roles, comps,
  bans, allies, opponents, and a hero-by-map cross-tab, ranked by Wilson score
  so a 2-0 does not outrank a 27-13.
- **Notes and dots.** Tag players by role or with free colours, and write notes
  on anyone.
- **CSV export and backup** of everything, any table.

## Limitations, honestly

- **Results are entered by hand.** Win/Loss/Draw appears nowhere on the
  scoreboard, so the app cannot know it. Same for score and ban attribution.
- **One hero per player per match.** The scoreboard is a single snapshot, so
  mid-match hero swaps are not captured.
- **The hero library is partial and unverified.** It currently holds 10 heroes,
  bootstrapped by labelling portraits from a sample corpus, and those labels
  have not been confirmed against reference art. Hero names may be wrong.
  Everything else either reads correctly or reports low confidence.
- **In-game (Tab) screenshots cannot be ingested yet.** The localizer finds the
  bans, map, mode, clock and rank range on them, but nothing reads those into
  values yet, so submitting one is refused rather than half-understood. Endgame
  reports work fully.
- **Windows only** in practice — it is a `run.bat` and an embedded CPython
  build. The application code itself is plain Python and has nothing
  Windows-specific in it.

## Running from source

    py -3.12 -m venv .venv
    .venv\Scripts\pip install -r requirements.txt
    .venv\Scripts\python -m app.main

Tests:

    .venv\Scripts\python -m unittest discover -s tests -t tests

The sample screenshots are **not** in this repository — they show real people's
gamertags. The corpus-backed tests skip without them, so a fresh clone runs 140
tests green and skips 96. See [`samples/README.md`](samples/README.md).

Build the distributable (needs internet once, to fetch the runtime and wheels):

    .venv\Scripts\python tools/package.py

## How it works

`CLAUDE-OWTRACKER.md` is the full specification — schema, extraction stages,
invariants and milestones. `updates.md` is the running log of what was built
and what was learned breaking it.

Extraction is deterministic template matching against reference bitmaps built
from the game's own rendering, never a model and never an OCR engine: after
scale normalization a `7` in the damage column is the same bitmap every time,
and the match score is a real confidence value rather than an estimate. The
scoreboard is located structurally, by colour and geometry, and the canonical
coordinate space is the board's own width — so a crop and a full-screen capture
of the same match normalize identically.
