"""The census's handle on the game's mod loader, through xmltest.

Moved into the app as :mod:`anno_modding_studio.core.xmltest`, which the Studio
now uses as a second opinion on the mods it exports.  Everything that module
has is here too, under the same names, so the census tooling and its tests
read as they did.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from anno_modding_studio.core import xmltest as _moved  # noqa: E402

globals().update({name: value for name, value in vars(_moved).items() if not name.startswith("__")})
