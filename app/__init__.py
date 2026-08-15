"""OWTracker — a local-only Overwatch match tracker.

No network calls, no credentials, and exactly one model: the bundled ONNX text
recognizer that reads unknown nameplates, which runs offline like everything
else. See CLAUDE-OWTRACKER.md.
"""

# The single source of truth. `app.main` reads this rather than repeating it —
# the two had drifted to 0.1.0 and 0.2.0 while the shipped release was 0.3.1,
# so the number the app reported was not the number anyone had.
__version__ = "0.5.0"
