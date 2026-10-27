"""The Studio's keywords: terms it uses in one sense only, each with its
meaning written once.

A sentence that uses one marks it, ``[[patch]]``, and a box or a help panel
shows it as a term you can point at for what it means (``i18n.tr_kw``,
``ui.help_tip.KeywordTips``). Every other sentence that says it can then
leave the explaining out. The glossary in docs/standards/design-standards.md,
section 14, says what each term means for whoever writes the text. Each
meaning here is that one, said to the reader.
"""

from __future__ import annotations

from typing import Dict

from .i18n import tr, tr_noop

# Key: the English term, as a sentence marks it. Value: its meaning, a
# sentence or two that starts with the term.
KEYWORDS: Dict[str, str] = {
    "patch": tr_noop(
        "A patch is a mod of yours that loads straight after somebody else's "
        "mod and holds your changes to it. An update to their mod leaves your "
        "changes in place, and the patch goes wherever you use their mod."
    ),
    "fork": tr_noop(
        "A fork is a copy of a loadout under a new name, to try something in. "
        "The original stays as it is."
    ),
    "project": tr_noop(
        "A project is the file a mod of yours is kept in: its details, its "
        "ModOps, its history, its builds and its mod.io page. Every loadout "
        "that has the mod reads it from there, so a change made in one "
        "loadout is in all of them."
    ),
    "generated copy": tr_noop(
        "A generated copy is a library mod with something of yours written "
        "into it: your edits to its ModOps, or its settings. Play builds it "
        "beside the library and links it in. The library's copy stays as its "
        "author shipped it."
    ),
    "live": tr_noop(
        "The live loadout is the one the game loads now. Play, or Make this "
        "live, makes a loadout live: it switches your mods folder to match "
        "it. Until then, another loadout changes nothing in the game."
    ),
}


def meaning(key: str) -> str:
    """What the keyword ``key`` means, in the reader's language, or "" for a
    key that is no keyword."""
    said = KEYWORDS.get(key)
    return tr(said) if said else ""
