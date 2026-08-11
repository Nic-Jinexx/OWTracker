"""LocalExtractor — the only extractor.

Pipeline (CLAUDE-OWTRACKER.md → Extraction → Stages):

    1. localize    anchors -> canonical transform, normalize to 1920px
    2. segment     team blocks by background hue, rows, column centres
    3. read        digits via the glyph atlas
    4. identify    hero portraits via pHash
    5. recognize   nameplates via pHash against player_nameplates

Stages 1-5 are built in milestones 2-5 and require real screenshots to
calibrate against. Until the reference libraries exist this returns an empty
draft with an explanatory warning rather than guessing — which is also exactly
the behaviour required for an unreadable image, so the contract is the same in
both cases.
"""

from __future__ import annotations

import cv2
import numpy as np

from .. import paths
from ..draft import SUGGESTION_ORIGIN, STAT_FIELDS, empty_draft, field
from . import cells, nameplates, ocr
from .base import ExtractionResult
from .glyphs import load_atlas
from .heroes import load_hero_library, identify_portrait
from .localize import localize
from .reader import read_cell

# Blue is the operator's block, red the enemy's — assigned from the background
# hue by the localizer, never from vertical position.
TEAM_FROM_BLOCK = {"blue": "ally", "red": "enemy"}


class LocalExtractor:
    """Reads one screenshot.

    The nameplate library and the hash tolerances are injected rather than
    fetched: the extractor holds no database connection, which keeps it usable
    from a script and testable without standing up a schema. The caller (the
    drafts route) knows the draft and the settings; this knows the pixels.
    """

    def __init__(self, team_size: int = 6, *,
                 nameplate_library: list[tuple[int, str]] | None = None,
                 nameplate_max_distance: int = 64,
                 hero_max_distance: int = 8,
                 read_names: bool | None = None):
        self.team_size = team_size
        self.nameplate_library = nameplate_library or []
        self.nameplate_max_distance = nameplate_max_distance
        self.hero_max_distance = hero_max_distance
        # None means "use it if it is installed"; False turns it off even where
        # it is. Either way it is ANDed with availability, so asking for name
        # reading on a machine without the engine is a no-op rather than a
        # per-row round trip through a function that can only return nothing.
        wanted = ocr.available() if read_names is None else bool(read_names)
        self.read_names = wanted and ocr.available()

    # -- library availability -------------------------------------------

    @staticmethod
    def reference_status() -> dict:
        """What the extractor has to work with. Surfaced in the UI so a
        missing atlas reads as 'not built yet' rather than 'extraction is
        broken'."""
        glyphs = sorted(paths.GLYPHS_DIR.glob("*.png")) if paths.GLYPHS_DIR.exists() else []
        return {
            "glyph_atlas": [p.stem for p in glyphs],
            "glyph_atlas_ready": len(glyphs) >= 10,
            "hero_hashes_ready": paths.HERO_HASHES_PATH.exists(),
        }

    # -- extraction ------------------------------------------------------

    def extract(self, image_path: str, kind: str) -> ExtractionResult:
        draft = empty_draft(self.team_size)
        warnings: list[str] = []

        image = self._load(image_path)
        if image is None:
            return ExtractionResult(
                draft=draft, kind=kind,
                warnings=["That image could not be decoded. Enter this match by hand."],
                diagnostics={"stage": "load"},
            )

        height, width = image.shape[:2]
        diagnostics = {"stage": "load", "width": width, "height": height}

        if kind != "endgame_report":
            # `app/extract/tab.py` localizes the Tab board and its chrome, but
            # localizing is not reading: nothing here can yet turn a map-name
            # box into a map, or a rank badge into a rank. Wiring it up before
            # those readers exist would attach a draft full of empty fields to
            # a screenshot that looks like it was understood.
            #
            # Refusing is also the safe answer regardless. A Tab shot read as an
            # endgame report would push mid-match statistics into the slot the
            # merge rules treat as authoritative.
            warnings.append(
                "Only the endgame report can be read so far. This screenshot has been "
                "archived and its map, mode, bans and rank range can be entered by hand.")
            return ExtractionResult(draft=draft, kind=kind,
                                    warnings=warnings, diagnostics=diagnostics)

        located = localize(image)
        diagnostics.update(located.diagnostics)
        diagnostics["stage"] = "localize"
        warnings.extend(located.warnings)
        if not located.ok:
            warnings.append(located.reason or "The scoreboard could not be located.")
            return ExtractionResult(draft=draft, kind=kind,
                                    warnings=warnings, diagnostics=diagnostics)

        atlas = load_atlas()
        heroes = load_hero_library()
        # A missing digit atlas used to abort the whole extraction. Hero and
        # nameplate identification need nothing from it, so it now costs the
        # statistics only — there is no reason to throw away the roster because
        # the numbers cannot be read.
        if not atlas.ready:
            warnings.append(
                "The digit atlas (seed/glyphs/) is not built, so statistics were not "
                "read. Run tools/build_glyph_atlas.py --write.")
        if not heroes:
            warnings.append(
                "No hero portraits are known yet, so heroes are blank. Run "
                "tools/build_hero_hashes.py to label them once.")

        layout = located.layout
        diagnostics["stage"] = "read"
        rows, unread = [], []
        crops: list[dict] = []
        recognized = portraits_read = suggested = 0

        for block in layout.teams:
            team = TEAM_FROM_BLOCK[block.team]
            for index in range(block.row_count):
                row = {"team": team, "row_index": index}

                if atlas.ready:
                    for column in STAT_FIELDS:
                        reading = read_cell(image, layout, block, index, column, atlas)
                        # An unread cell is emitted blank rather than omitted: the
                        # merge rules ignore a blank incoming value, so this can
                        # never overwrite something the operator typed, and the
                        # review grid still gets a cell to highlight.
                        row[column] = field(reading.value, source="template",
                                            origin=kind, confidence=reading.confidence)
                        if not reading.ok:
                            unread.append(
                                f"{team} row {index + 1} {column}: {reading.problem}")

                if heroes:
                    hero = identify_portrait(image, layout, block, index,
                                             heroes, self.hero_max_distance)
                    if hero.identified:
                        portraits_read += 1
                        row["hero_name"] = hero.name       # resolved to an id by the route
                        row["hero_confidence"] = hero.confidence

                # The nameplate crop travels as pixels, not as a path: the
                # extractor does not know which draft it is serving, and the
                # route already owns where uploaded images live.
                plate = cells.nameplate_image(image, layout, block, index)
                fingerprint = nameplates.signature(plate)
                if fingerprint:
                    row["nameplate_phash"] = fingerprint
                    row["nameplate_width"] = int(plate.shape[1])
                    crops.append({"team": team, "row_index": index, "image": plate})
                    hit = nameplates.match(fingerprint, self.nameplate_library,
                                           self.nameplate_max_distance)
                    if hit.recognized:
                        recognized += 1
                        row["player_id"] = hit.player_id
                        row["nameplate_confidence"] = hit.confidence
                    elif self.read_names:
                        # Only for rows the hash could not place. The hash is
                        # exact and proven; reading is a fallback for people it
                        # has never seen, and running it on a row already
                        # identified could only ever disagree with a better
                        # answer. Suggestions are low confidence by
                        # construction, so the grid asks for confirmation.
                        plate_text = cells.region_image(image, layout, block,
                                                        index, ocr.TEXT_BOUNDS)
                        proposed = ocr.suggest(plate_text, self.nameplate_library)
                        if proposed is not None:
                            suggested += 1
                            row["player_name"] = field(
                                proposed.name, source="template",
                                origin=SUGGESTION_ORIGIN,
                                confidence=proposed.confidence)
                            if proposed.player_id is not None:
                                row["nameplate_suggested_player_id"] = proposed.player_id
                            row["name_read_by"] = proposed.origin

                rows.append(row)

        # Invariant 6: the row count comes off the image, never from the
        # setting, which is only ever used to phrase the disagreement warning.
        detected = max((block.row_count for block in layout.teams), default=0)
        draft["rows"] = rows
        draft["meta"]["team_size"] = field(detected, source="template",
                                           origin=kind, confidence=1.0)

        counts = {block.team: block.row_count for block in layout.teams}
        diagnostics["row_counts"] = counts
        diagnostics["cells_read"] = (len(rows) * len(STAT_FIELDS) - len(unread)
                                     if atlas.ready else 0)
        diagnostics["cells_unread"] = len(unread)
        diagnostics["unread"] = unread[:20]
        diagnostics["heroes_identified"] = portraits_read
        diagnostics["players_recognized"] = recognized
        diagnostics["names_suggested"] = suggested
        diagnostics["name_reading"] = self.read_names

        if unread:
            warnings.append(
                f"{len(unread)} of {len(rows) * len(STAT_FIELDS)} statistics could not be "
                f"read confidently and are blank for you to fill in.")
        if recognized:
            warnings.append(
                f"Recognized {recognized} player{'' if recognized == 1 else 's'} from "
                f"a previous match. Check the names before saving.")
        if detected != self.team_size:
            warnings.append(
                f"This screenshot is {detected}v{detected}, not {self.team_size}v"
                f"{self.team_size}. The row count was taken from the image.")

        return ExtractionResult(draft=draft, kind=kind, warnings=warnings,
                                diagnostics=diagnostics, crops=crops)

    @staticmethod
    def _load(image_path: str) -> np.ndarray | None:
        """Decode defensively.

        cv2.imread returns None rather than raising on a bad file, and chokes
        on non-ASCII paths on Windows — so read the bytes ourselves and decode
        from memory.
        """
        try:
            with open(image_path, "rb") as handle:
                buffer = np.frombuffer(handle.read(), dtype=np.uint8)
            if buffer.size == 0:
                return None
            image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
            return image if image is not None and image.size else None
        except (OSError, ValueError):
            return None
