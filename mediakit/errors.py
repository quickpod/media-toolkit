"""Error types for mediakit."""


class MediaKitError(Exception):
    """Raised for any recoverable failure in a mediakit operation.

    All public functions raise this (and only this) on failure so callers
    -- the CLI and the GUI -- have a single exception to catch.  A missing
    FFmpeg binary is reported this way too, with a message explaining how to
    make it available.
    """
