"""Anno Modding Studio - a GUI for authoring Anno ModOp XML patches."""

import sys

# The Studio's version, and the only place it is written: pyproject.toml
# reads it, and so does everything else that names it -- the About box, the
# User-Agent sent to mod.io and the Marketplace, what a loadout and an
# exported modinfo say wrote them, each data folder's data-version.json, and
# the built Studio's file version, release tag and zip.
# tests/test_studio_version.py fails on a second copy of it.
__version__ = "0.1.0"

# Where the Studio is published.
REPO_URL = "https://github.com/aleph-shin/anno-modding-studio"

# The user guide, from the repository's root. The README is the repository's
# front page; what Help opens, and the update notice links, is this.
GUIDE = "docs/guide.md"


def source_url(path: str = "") -> str:
    """Where ``path`` is read on GitHub, as this Studio has it: at its
    release's tag, ``v<version>``, for a built Studio, which is that
    release, and on ``main`` for one run from its source."""
    ref = "v" + __version__ if getattr(sys, "frozen", False) else "main"
    return "%s/blob/%s/%s" % (REPO_URL, ref, path)
