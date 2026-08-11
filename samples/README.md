# samples/

Screenshots with hand-written expected output. The accuracy harness scores the
extractor against these and every extractor change reports the delta
(CLAUDE-OWTRACKER.md → Quality Bar).

## The images are not in the published repository

`samples/*.png` and `samples/*.expected.json` are gitignored. Every scoreboard
here shows the gamertags of real people who were in those lobbies and did not
agree to appear in a public repository, so the corpus stays on the machine that
captured it. `manifest.json` and this file are kept, because what the corpus
*contains* is worth documenting even where the corpus itself is absent.

The suite is written for both states: with the images present it asserts
against them, and without them the corpus-backed tests skip rather than fail
(140 run / 96 skipped on a fresh clone, all green). A contributor who wants the
harnesses has to capture their own screenshots and write the manifest entries —
which is the same work the table below records.

## Files

`manifest.json` is the index: every image needs an entry there or the suite
fails, because a screenshot nobody wrote a purpose for is not a test case.
A `<name>.expected.json` beside an image is its ground truth.

| file | size | board px | what it is |
|---|---|---|---|
| `kingsrowloss.png` | 1198x1157 | 950 | crop; **ground truth** |
| `nepalloss.png` | 1238x1185 | 950 | crop |
| `route66Loss.png` | 1258x1157 | 970 | crop; two rows with no title line |
| `blizzard worldLoss.png` | 1190x1189 | 974 | crop; three rows with **no nameplate** |
| `endgame_fullscreen_2402x1349.png` | 2402x1349 | 980 | native full-screen |
| `endgame_fullscreen_2502x1364.png` | 2502x1364 | 992 | native full-screen |
| `endgame_fullscreen_2329x1312.png` | 2329x1312 | 1034 | native full-screen; widest board |
| `ingame_tab_2511x1006.png` | 2511x1006 | — | **in-game Tab**; refused today |

All eight are 6v6. The board width spans 950-1034 px across four distinct
capture geometries, which is what makes the localizer's scale-invariance a
measurement rather than an assumption.

## Atlas sources and held-out samples

`manifest.json` marks exactly one image `atlas_source: true`. The digit atlas is
cut from that image's ground truth and from nothing else; every other sample
with an `expected.json` is a **held-out set**, and

    .venv/Scripts/python.exe tools/score_digits.py --holdout

is the only accuracy number worth quoting. Scoring the atlas against the glyphs
it was averaged from is close to a tautology.

`atlas_source` defaults to false so that transcribing a new sample cannot
silently absorb the validation set into the training set. Promoting one is a
deliberate edit.

## Writing an expected file

Two rules keep the corpus trustworthy:

1. **Every asserted value is read off the image, never inferred.** The filename
   says `kingsrowloss`, but map and result are asserted by the operator and do
   not appear in an endgame report — so they are not ground truth here. A test
   that passed because the extractor learned to parse filenames would be worse
   than no test.
2. **What was not verified goes in `not_asserted` with a reason,** rather than
   being guessed at or silently omitted. Hero identities are the current
   example: the portraits are small and several heroes look alike at that size,
   so they wait for a pass against reference art.

Row order is top-to-bottom as rendered. `zero_cells` lists the cells the game
renders in dimmed grey rather than white — the digit reader has to catch those
or every `0` silently becomes `null`.

## The in-game Tab shot

`ingame_tab_2511x1006.png` is the only one of its kind and the localizer
**refuses it on purpose**. The Tab layout adds ultimate-charge and per-player
mute columns that are team-coloured but sit outside the grey header strip, so
the blue block overruns the header by ~467 canonical units and the alignment
check fires. `tests/test_localize.py` asserts that refusal, and asserts the
message names the likely cause — if a Tab shot ever localized "successfully",
mid-match statistics would land in the endgame report's slot, which the merge
rules treat as authoritative.

It is also the only screenshot in the corpus carrying the hero ban row, the
mode and map header, the match clock and the rank range. Those have no
extraction path until a Tab localizer exists.

## Known gaps in the corpus

- Every sample is **6v6**. Nothing here exercises 5v5, which is the
  `default_team_size`.
- Every endgame report is a **loss**, so nothing exercises a win.
- The red block in the Tab shot is **cut off mid-block** — only 3 of 6 rows are
  in frame. A full one is still worth capturing.
- No sample shows a **5-digit-plus mitigation** value or a hero released after
  the shots were taken.
