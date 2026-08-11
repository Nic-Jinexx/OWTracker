# OWTracker

A local Overwatch match tracker. Drop in a screenshot of the endgame scoreboard
and it reads the stats and keeps your match history.

Runs entirely on your own machine. No account, no cloud, no telemetry.

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
is flagged instead of guessed. Player names are recognized by nameplate after
you type them once.

Win or loss is not printed on the scoreboard, so you pick that yourself.

## Limits

- Hero names come from a small, unverified library and may be wrong.
- Only one hero per player per match. The scoreboard is a single snapshot.
- In-game (Tab) screenshots are not supported yet. Endgame reports only.
- Windows only in practice.

## From source

```
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m app.main
```
