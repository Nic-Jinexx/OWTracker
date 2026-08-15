# OWTracker

A local Overwatch match tracker. Drop in a screenshot of the endgame scoreboard
— or take a snip with Win+Shift+S and paste it straight in with Ctrl+V — and it
reads the stats and keeps your match history.

Runs entirely on your own machine. No account, no cloud, no telemetry, and no
network access at all.

## Install

Download the zip from [Releases](../../releases/latest), unzip the whole
folder, and double-click `run.bat`. Python is bundled, so nothing needs
installing.

Windows may warn that it protected your PC. Choose More info, then Run anyway.

Your data lives in the `data` folder next to `run.bat`. Copy that folder to
move or back up.

## What it reads

Eliminations, assists, deaths, damage, healing and mitigation for all twelve
players, by matching the game's own digits. Anything it cannot read confidently
is flagged instead of guessed.

Player names are recognized by nameplate once you have typed them, and read
off the screenshot when they are new. A read name is always shown for you to
confirm, never saved as certain.

Heroes work the same way. The app ships knowing a handful of portraits and
learns the rest from you: pick a hero once in the review grid and every later
screenshot recognizes it by itself, along with the role it implies. The
portrait is shown beside the picker so you are naming a face, not a guess.

Win or loss is not printed on the scoreboard, so you pick that yourself.

You can edit or delete any match afterwards, and group matches into your own
named seasons. The Trends page charts your win rate over time.

## Limits

- Heroes you have never picked come back blank. The shipped library covers ten
  of the fifty heroes, so the first few matches need the dropdown; after that
  it mostly fills itself. If it ever learns a portrait wrongly, forget that
  hero under Settings and name it again.
- A read player name is right about nine times in ten on its own. If it matches
  someone already in your database it is corrected automatically; otherwise
  treat it as a starting point and check it.
- Only one hero per player per match. The scoreboard is a single snapshot.
- In-game (Tab) screenshots are not supported yet. Endgame reports only.
- Windows only in practice.

## Privacy

Nothing leaves the machine. Name reading uses a small text recognition model
that ships inside the download and runs locally, so it works offline and costs
nothing. There is no API key, no account, and no outbound request of any kind.

## From source

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m app.main
```
