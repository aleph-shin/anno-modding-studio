"""Sending a new version of your mod to mod.io, from the folder you exported.

Creating a mod on mod.io wants a name, a summary, a logo and a page's worth
of choices, and the website does that better, once.  Publishing an *update*
wants a zip, a version and a changelog, and that is repetitive, mechanical,
and exactly what an authoring tool should do.  So a loadout is linked to a
mod on mod.io once, and from then on the Export tab can send the folder it
just wrote as that mod's next file.

With it goes the mod's summary, the plain text under its name on mod.io,
where the Export tab's differs.  Never the page's description: that is rich
text, written and seen in a web browser, which is better at both.

Neither Anno curates: an uploaded file is live the moment it finishes.  There
is no draft and no queue, so what this module prepares is shown in full and
confirmed before anything is sent, and the version is checked against the one
already live so the same number is never uploaded twice.
"""

from __future__ import annotations

import hashlib
import os
import re
import urllib.parse
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import modio, versioning
from .i18n import tr


class PublishError(RuntimeError):
    pass


@dataclass
class Plan:
    """Everything a publish will send, laid out to be confirmed."""

    mod: modio.ModSummary
    archive: Path
    version: str
    size: int
    md5: str
    as_user: str = ""
    changelog: str = ""
    # The summary to put in place of the one on mod.io; "" to leave that be.
    summary: str = ""
    # The Export tab's summary where it is too long for mod.io, and so is
    # not sent.
    too_long: str = ""

    @property
    def live_version(self) -> str:
        return self.mod.version


def zip_mod(folder: Path, into: Path) -> Path:
    """The exported mod folder as a zip, its folder name the top-level entry.

    That is the shape most mods on mod.io ship in, and the one every install
    path -- the in-game browser, the Studio's own, a player unzipping by hand
    -- reads straight back into a mods folder.  Dot-folders inside are left
    out: they are tooling, never part of a mod.
    """
    folder = Path(folder)
    if not folder.is_dir():
        raise PublishError(tr("%s is not a folder", folder))
    into = Path(into)
    into.mkdir(parents=True, exist_ok=True)
    archive = into / (folder.name + ".zip")
    partial = archive.with_suffix(".zip.part")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED) as zipped:
        for root, dirs, files in os.walk(folder):
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            for name in sorted(files):
                path = Path(root) / name
                inside = Path(folder.name) / path.relative_to(folder)
                zipped.write(path, inside.as_posix())
    os.replace(partial, archive)
    return archive


def plan(
    client: modio.Client,
    game_id: int,
    mod_id: int,
    exported: Path,
    version: str,
    scratch: Path,
    as_user: str = "",
    live: Optional[modio.ModSummary] = None,
    summary: str = "",
) -> Plan:
    """Zip the export and check it against what is live, without sending it.

    Refuses a version that is already the live one -- that is the one mistake
    nobody can undo from here -- and one the live file is already past, since
    that is nearly always a loadout whose version was never bumped.  The
    Export tab offers the next number before it gets here (see
    :func:`versioning.next_after`); this is the check that holds regardless.

    ``live`` is the mod as mod.io has it, when the caller has just asked.
    ``summary`` is the mod's summary as the Export tab has it: sent where it
    differs from mod.io's, and fits.
    """
    version = version.strip()
    if not version:
        raise PublishError(tr("give the mod a version first: it is what mod.io files are listed by"))
    if live is None:
        live = client.mod(game_id, mod_id)
    if live is None:
        raise PublishError(tr(
            "mod.io has no mod %s that this account can see: it may have been "
            "deleted, or it belongs to another account", mod_id
        ))
    if versioning.is_behind(version, live.version):
        if live.version.strip().casefold() == version.casefold():
            raise PublishError(tr(
                "%s is already the live version on mod.io. Bump the version "
                "before publishing",
                version,
            ))
        raise PublishError(tr(
            "the live version on mod.io is %(live)s, which is newer than "
            "%(version)s. Bump the version",
            live=live.version, version=version,
        ))
    archive = zip_mod(exported, scratch)
    data = archive.read_bytes()
    summary = summary.strip()
    fits = len(summary) <= modio.SUMMARY_LIMIT
    return Plan(
        mod=live,
        archive=archive,
        version=version,
        size=len(data),
        md5=hashlib.md5(data).hexdigest(),
        as_user=as_user,
        # An empty one is left: mod.io requires a summary, and an author
        # who never wrote one here has the website's.
        summary=summary if summary and fits and summary != live.summary.strip() else "",
        too_long=summary if not fits else "",
    )


def send(client: modio.Client, game_id: int, ready: Plan) -> modio.ModSummary:
    """Upload the planned file.  Live at once.  The summary is
    :func:`send_summary`'s, after: the file is what matters, and a summary
    refused must not keep it from going."""
    return client.add_modfile(
        game_id, ready.mod.id, ready.archive, ready.version, ready.changelog, active=True
    )


def send_summary(client: modio.Client, game_id: int, ready: Plan) -> bool:
    """Put the planned summary on the mod's page; whether there was one."""
    if not ready.summary:
        return False
    client.set_summary(game_id, ready.mod.id, ready.summary)
    return True


def why_refused(refusal: modio.Refused, game_label: str) -> str:
    """A refusal in words the user can act on.

    For Anno 117, mod.io requires a Ubisoft account linked to yours before a
    mod may be added; there is no way to ask in advance -- the account object
    has no such field -- so the refusal is where it shows up.
    """
    text = str(refusal) or tr("mod.io refused the upload")
    return tr(
        "%(refusal)s If this is %(game)s, mod.io may require your Ubisoft account to be linked "
        "to your mod.io account first: do that on mod.io's website, then try again.",
        refusal=text, game=game_label,
    )


def name_id_of(text: str) -> str:
    """The name ID mod.io would make of ``text``, as it does of a mod's name
    for its page address: lowercase, anything but letters and digits a dash.
    "" for nothing to make one of."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def mod_id_from(text: str) -> Optional[int]:
    """A mod id typed as a number, or found in a mod.io page address."""
    text = (text or "").strip()
    if text.isdigit():
        return int(text)
    # A page address names the mod by its name_id, which carries no id at all
    # -- and the game's own name_id has a number in it ("anno-1800") that must
    # not be mistaken for one. Only a whole path segment of digits, or an
    # ``id=`` in the query, counts.
    parsed = urllib.parse.urlparse(text)
    for value in urllib.parse.parse_qs(parsed.query).get("id", []):
        if value.isdigit():
            return int(value)
    for segment in parsed.path.split("/"):
        if segment.isdigit():
            return int(segment)
    return None
