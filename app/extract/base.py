"""The extractor interface.

There is exactly one implementation, `LocalExtractor`, and there will never be
a remote one — extraction is deterministic template matching that runs on this
machine (see CLAUDE-OWTRACKER.md, Non-negotiables). The interface exists so the
review UI, the merge rules, and the accuracy harness all talk to one shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Protocol


@dataclass
class ExtractionResult:
    """What an extractor returns for a single image.

    `draft` is a DraftMatch payload fragment in the shape defined by
    app.draft — field envelopes carrying source, origin, and confidence — ready
    to be merged into the draft the operator is reviewing.
    """

    draft: dict
    kind: str
    # Populated even on failure so the debug overlay has something to render
    # and the operator can see what the localizer thought it found.
    diagnostics: dict = dataclass_field(default_factory=dict)
    warnings: list[str] = dataclass_field(default_factory=list)
    # Nameplate crops as raw pixels: `{team, row_index, image}`. The extractor
    # does not know which draft it is serving and has no business inventing a
    # path, so the route writes them where uploaded images already live.
    crops: list = dataclass_field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True only if something was actually read.

        A blank draft still carries ten empty rows, so the test has to be
        "does any field hold a value", not "are there rows".
        """
        def has_value(envelope) -> bool:
            return isinstance(envelope, dict) and envelope.get("value") is not None

        if any(has_value(v) for v in self.draft.get("meta", {}).values()):
            return True
        for row in self.draft.get("rows", []):
            if any(has_value(v) for v in row.values()):
                return True
        return bool(self.draft.get("bans"))


class Extractor(Protocol):
    def extract(self, image_path: str, kind: str) -> ExtractionResult:
        """Read one screenshot of the declared kind.

        Must never raise on malformed input. A file that cannot be processed
        returns an empty draft plus a warning, so the server keeps running and
        the operator sees a visible error instead of a stack trace.
        """
        ...
