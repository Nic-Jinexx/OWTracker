"""Filesystem layout.

Everything resolves from the package location, never from the working
directory, so `run.bat` can be double-clicked from anywhere.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MIGRATIONS_DIR = ROOT / "migrations"
SEED_DIR = ROOT / "seed"
STATIC_DIR = ROOT / "static"
SAMPLES_DIR = ROOT / "samples"

# Operator data. Never committed; created on first run.
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "owtracker.db"
SCREENSHOTS_DIR = DATA_DIR / "screenshots"
DRAFT_SCREENSHOTS_DIR = SCREENSHOTS_DIR / "drafts"
BACKUPS_DIR = DATA_DIR / "backups"

# Reference libraries — build artifacts shipped with the app, not user data.
GLYPHS_DIR = SEED_DIR / "glyphs"
HERO_HASHES_PATH = SEED_DIR / "hero_hashes.json"


def ensure_data_dirs() -> None:
    for directory in (DATA_DIR, SCREENSHOTS_DIR, DRAFT_SCREENSHOTS_DIR, BACKUPS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def screenshot_url(stored: str) -> str:
    """Turn a stored screenshot path into a browser URL.

    Paths are stored root-relative (`data/screenshots/...`) because that is what
    makes sense inside the database, but the app serves that tree from the
    `/screenshots` mount — only the screenshots, never the whole data folder.
    """
    normalized = stored.replace("\\", "/")
    candidates = [str(SCREENSHOTS_DIR).replace("\\", "/")]
    try:
        # The usual case: the data directory lives inside the project.
        candidates.append(str(SCREENSHOTS_DIR.relative_to(ROOT)).replace("\\", "/"))
    except ValueError:
        # A relocated data directory, or a test fixture pointing at a temp dir.
        # `portable()` already tolerates this; so must the URL builder, or the
        # two disagree exactly when the paths are least obvious.
        pass
    for prefix in candidates:
        if prefix and normalized.startswith(prefix):
            return "/screenshots" + normalized[len(prefix):]
    return "/" + normalized.lstrip("/")


def portable(path: Path) -> str:
    """Path as stored in the database: root-relative with forward slashes.

    Falls back to the absolute path when the file genuinely lives outside the
    project (a relocated data directory, or a test fixture), so this never
    raises on a layout it did not expect.
    """
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")
