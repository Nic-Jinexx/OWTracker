"""Build the distributable.

Produces `dist/OWTracker/` — a folder containing an embedded CPython runtime
with every dependency already installed beside it. The result runs on a Windows
machine that has never had Python installed, with no internet connection and no
administrator rights: unzip, double-click run.bat.

This script needs internet (it fetches the embeddable runtime and the wheels).
The *artifact it produces* never does.

    python tools/package.py            build dist/OWTracker + the zip
    python tools/package.py --no-zip   build the folder only
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
BUILD = DIST / "OWTracker"

PYTHON_VERSION = "3.12.10"
EMBED_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-embed-amd64.zip"
)
CACHE = DIST / f"python-{PYTHON_VERSION}-embed-amd64.zip"

# Copied verbatim into the distributable.
PAYLOAD = ("app", "static", "seed", "migrations")

RUN_BAT = """@echo off
REM OWTracker. Everything needed is inside this folder - no installation,
REM no internet, no administrator rights. Double-click to start.

setlocal
cd /d "%~dp0"

if not exist "python\\python.exe" (
    echo.
    echo   The 'python' folder is missing. Unzip the whole OWTracker folder,
    echo   not just run.bat, and try again.
    echo.
    pause
    exit /b 1
)

echo Starting OWTracker...
echo Leave this window open while you use it. Close it to stop.
echo.

"python\\python.exe" -m app.main

if errorlevel 1 (
    echo.
    echo   OWTracker stopped unexpectedly. The message above says why.
    echo.
    pause
)
"""

READ_ME = """OWTracker
=========

A local Overwatch match tracker. Everything runs on this machine.

TO START
--------
Double-click run.bat. A console window opens and your browser goes to
OWTracker. Leave the console window open while you use it; close it to stop.

If Windows shows a "protected your PC" box, choose More info -> Run anyway.
That warning appears for any program that has not been code-signed.

WHERE YOUR DATA LIVES
---------------------
Everything is under the data folder next to run.bat:

    data\\owtracker.db       your matches, players, and settings
    data\\screenshots\\       every screenshot you have ever submitted
    data\\backups\\           copies made by the Back up button

To move to a new PC, copy the whole OWTracker folder. To back up, copy
data\\owtracker.db - it is an ordinary SQLite file and opens in any SQLite
browser.

WHAT IT DOES NOT DO
-------------------
No internet connection. No accounts. No telemetry. No AI service. Nothing you
put in leaves this computer, which is also why it costs nothing to run.

Match results are not printed on any scoreboard, so you always choose
Win/Loss/Draw yourself. The scoreboard is a single snapshot, so only one hero
per player is recorded - mid-match hero swaps are not captured.
"""


def log(message: str) -> None:
    print(f"[package] {message}")


def fetch_runtime() -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    if CACHE.exists():
        log(f"using cached runtime {CACHE.name}")
        return CACHE
    log(f"downloading {EMBED_URL}")
    urllib.request.urlretrieve(EMBED_URL, CACHE)
    log(f"downloaded {CACHE.stat().st_size // 1024} KB")
    return CACHE


def unpack_runtime(archive: Path) -> Path:
    python_dir = BUILD / "python"
    python_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(python_dir)

    # The embeddable distribution ships with site-packages disabled and only
    # its own directory on the path. Enable site, add the site-packages folder
    # we are about to populate, and add the parent directory so `app` (which
    # sits next to python/) is importable.
    pth = next(python_dir.glob("python*._pth"))
    pth.write_text(
        f"python{PYTHON_VERSION.replace('.', '')[:3]}.zip\n"
        ".\n"
        "..\n"
        "Lib\\site-packages\n"
        "import site\n",
        encoding="utf-8",
    )
    log(f"runtime unpacked, {pth.name} patched")
    return python_dir


def install_dependencies(python_dir: Path) -> None:
    """Install wheels into the bundled runtime at BUILD time.

    Using the build machine's interpreter with --target: both are CPython 3.12
    on win_amd64, so the cp312 wheels are the correct ones.
    """
    target = python_dir / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    log("installing dependencies into the bundled runtime")
    subprocess.run(
        [sys.executable, "-m", "pip", "install",
         "--target", str(target),
         "--only-binary", ":all:",
         "--quiet",
         "-r", str(ROOT / "requirements.txt")],
        check=True,
    )
    # Trim what the shipped app never uses.
    for junk in target.glob("*.dist-info"):
        shutil.rmtree(junk / "licenses", ignore_errors=True)
    shutil.rmtree(target / "bin", ignore_errors=True)
    log("dependencies installed")


def copy_payload() -> None:
    for name in PAYLOAD:
        source = ROOT / name
        if not source.exists():
            raise SystemExit(f"missing required folder: {name}")
        destination = BUILD / name
        shutil.copytree(
            source, destination,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    (BUILD / "run.bat").write_text(RUN_BAT, encoding="utf-8")
    (BUILD / "README.txt").write_text(READ_ME, encoding="utf-8")
    # Created empty so the first run has somewhere to write without needing
    # permissions it might not have.
    (BUILD / "data").mkdir(exist_ok=True)
    log(f"copied {', '.join(PAYLOAD)} + run.bat + README.txt")


def verify() -> None:
    """Prove the bundled runtime can import the app and open its database.

    Run with an empty environment and a cwd outside the build so nothing is
    accidentally borrowed from the developer machine.
    """
    python = BUILD / "python" / "python.exe"
    log("verifying the bundled runtime in isolation")
    result = subprocess.run(
        [str(python), "-c",
         "import sys; sys.path.insert(0, '.');"
         "import fastapi, uvicorn, numpy, cv2, PIL;"
         "from app.db import init_db; init_db();"
         "from app.main import app;"
         "print('imports ok |', 'numpy', numpy.__version__, '| cv2', cv2.__version__);"
         "print('routes', len(app.routes))"],
        cwd=BUILD, capture_output=True, text=True,
        env={"SYSTEMROOT": "C:\\Windows", "PATH": ""},
    )
    if result.returncode != 0:
        print(result.stdout)
        print(result.stderr, file=sys.stderr)
        raise SystemExit("bundled runtime failed verification")
    for line in result.stdout.strip().splitlines():
        log(f"  {line}")
    # The verification created a database; remove it so the shipped folder is
    # clean and the first real run starts empty.
    shutil.rmtree(BUILD / "data", ignore_errors=True)
    (BUILD / "data").mkdir()


def make_zip() -> Path:
    archive = DIST / "OWTracker.zip"
    if archive.exists():
        archive.unlink()
    log("compressing")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in sorted(BUILD.rglob("*")):
            if path.is_file():
                bundle.write(path, Path("OWTracker") / path.relative_to(BUILD))
    return archive


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-zip", action="store_true", help="build the folder only")
    args = parser.parse_args()

    if BUILD.exists():
        log("clearing previous build")
        shutil.rmtree(BUILD)

    archive = fetch_runtime()
    python_dir = unpack_runtime(archive)
    install_dependencies(python_dir)
    copy_payload()
    verify()

    log(f"folder: {BUILD}  ({folder_size(BUILD) / 1_048_576:.0f} MB)")
    if not args.no_zip:
        zip_path = make_zip()
        log(f"zip:    {zip_path}  ({zip_path.stat().st_size / 1_048_576:.0f} MB)")
    log("done — send the zip, unzip it anywhere, double-click run.bat")


if __name__ == "__main__":
    main()
