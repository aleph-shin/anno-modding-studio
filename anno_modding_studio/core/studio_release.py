"""Whether a newer version of the Studio itself is out, asked of GitHub.

Not to be confused with :mod:`core.updates`, which asks mod.io whether a
*mod* has moved on.  This asks one question about the Studio: which release
is GitHub's newest, ``GET /repos/<owner>/<name>/releases/latest``.  The
answer is one of three: a newer version, with its number and its page; up
to date; or could not check, and why.

The Studio only says so and links the page.  It never downloads a release
and never installs one: a built Studio is updated by unzipping the new zip
into a new folder (the user guide's *Install and run*), and one run from its
source by updating the checkout.

The request is GitHub's public API, unauthenticated -- no token, ever --
with a User-Agent naming the Studio and its version, as the other requests
do, and nothing else about the person or their mods.  It goes through
mod.io's careful opener (:func:`modio.careful_get`): https only, a redirect
followed only to https, the answer read no further than a cap.

A 404 is "could not check", said quietly: it is what GitHub answers for a
repository it does not show, such as a private one, and what it answers
before the first release is published.  So are a rate limit (403 or 429)
and being offline.  When the window asks, and how often, is
``ui.studio_release_controller``'s; this has no Qt.
"""

from __future__ import annotations

import datetime
import http.client
import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from .. import GUIDE, REPO_URL, __version__, source_url
from . import modio, versioning
from .i18n import tr

API = "https://api.github.com/repos/%s/%s/releases/latest"
USER_AGENT = "AnnoModdingStudio/%s" % __version__
# Short: it runs behind the window, and an answer that is late is no worse
# than none.  A release's answer is a few kilobytes; its notes can run on.
TIMEOUT = 10.0
MOST_ANSWER = 1024 * 1024
# Asked at most once in this long, unless asked for from the Help menu.
DAY = datetime.timedelta(hours=24)

NEWER = "newer"
UP_TO_DATE = "up to date"
UNCHECKED = "unchecked"

# Why a check came to nothing, for code to match on; the sentence is
# Answer.why.
NOT_PUBLISHED = "not-published"
RATE_LIMITED = "rate-limited"
REFUSED = "refused"
UNREACHABLE = "unreachable"
UNREADABLE = "unreadable"
NOT_A_RELEASE = "not-a-release"

# A tag the Studio's releases are made under, ``v<major>.<minor>.<patch>``,
# and nothing else: it goes into an address and a sentence.
_TAG = re.compile(r"v?(\d{1,6}(?:\.\d{1,6}){0,3})")

Getter = Callable[[str, Dict[str, str], float, int], modio.Response]


@dataclass(frozen=True)
class Release:
    """One published release of the Studio."""

    # Without the leading v: "0.2.0".
    version: str
    # As GitHub has it: "v0.2.0".
    tag: str
    # Its page on GitHub, which holds its notes and its zip.
    page: str
    # When it was published, as YYYY-MM-DD; "" where GitHub does not say.
    published: str = ""


@dataclass(frozen=True)
class Answer:
    """What a check came to: NEWER with the release, UP_TO_DATE with the
    newest release, or UNCHECKED with ``reason`` and the sentence ``why``."""

    state: str
    release: Optional[Release] = None
    reason: str = ""
    why: str = ""


def repository(url: str = REPO_URL) -> Tuple[str, str]:
    """``(owner, name)`` of the repository at ``url``."""
    parts = [part for part in urllib.parse.urlsplit(url).path.split("/") if part]
    if len(parts) < 2:
        raise ValueError("not a repository's address: %r" % url)
    name = parts[1][:-4] if parts[1].endswith(".git") else parts[1]
    return parts[0], name


def latest_url(url: str = REPO_URL) -> str:
    """Where GitHub says which release of the repository is newest."""
    return API % repository(url)


def request_headers() -> Dict[str, str]:
    """Everything the request carries: what it wants and who asks, and no
    sign-in of any kind."""
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
    }


def version_of(tag: str) -> str:
    """The version a release's tag names, without its ``v``; "" for a tag
    that is not one of the Studio's."""
    found = _TAG.fullmatch(str(tag or "").strip())
    return found.group(1) if found else ""


def is_newer(version: str, than: str = __version__) -> bool:
    """Whether ``version`` comes after ``than``.  Neither can be a version
    with no numbers: such a one is newer than nothing."""
    if not versioning.key(version) or not versioning.key(than):
        return False
    return versioning.trimmed(version) > versioning.trimmed(than)


def release_page(tag: str, url: str = REPO_URL) -> str:
    """The page of the release under ``tag``: its notes and its zip."""
    return "%s/releases/tag/%s" % (url, urllib.parse.quote(tag))


def update_guide() -> str:
    """The user guide's *Install and run*, which says how a built Studio is
    updated: the new zip unzipped into a new folder, the data carried over."""
    return source_url(GUIDE) + "#install-and-run"


def _page(html_url, tag: str, url: str = REPO_URL) -> str:
    """GitHub's page for the release where it is one of the repository's,
    over https; else the page made from the tag.  Windows opens whatever an
    address names, so one from an answer is opened only when it is that."""
    try:
        parts = urllib.parse.urlsplit(str(html_url or ""))
    except ValueError:
        return release_page(tag, url)
    owner, name = repository(url)
    wanted = "/%s/%s/releases/" % (owner.lower(), name.lower())
    if (parts.scheme.lower() == "https" and (parts.hostname or "").lower() == "github.com"
            and parts.path.lower().startswith(wanted) and not parts.query):
        return urllib.parse.urlunsplit(("https", "github.com", parts.path, "", ""))
    return release_page(tag, url)


def read_release(answer, url: str = REPO_URL) -> Release:
    """The release in GitHub's answer for ``releases/latest``.

    Raises ``ValueError`` for an answer that is not a published release
    with a version the Studio reads.  A draft or a pre-release is refused
    as well, though ``releases/latest`` names neither: whatever GitHub
    answers, the Studio offers only a release that has been published."""
    if not isinstance(answer, dict):
        raise ValueError("GitHub's answer is not a release")
    if answer.get("draft") or answer.get("prerelease"):
        raise LookupError("GitHub's newest release is a draft or a pre-release")
    tag = str(answer.get("tag_name") or "")
    version = version_of(tag)
    if not version:
        raise ValueError("GitHub named a release tag that is not a version: %r" % tag[:40])
    published = str(answer.get("published_at") or "")[:10]
    try:
        datetime.date.fromisoformat(published)
    except ValueError:
        published = ""
    return Release(version=version, tag=tag.strip(), page=_page(answer.get("html_url"), tag.strip(), url),
                   published=published)


def _unchecked(reason: str, why: str) -> Answer:
    return Answer(UNCHECKED, reason=reason, why=why)


def _why_not(response: modio.Response) -> Answer:
    """What a check that GitHub did not answer with a release came to."""
    status = response.status
    if status == 404:
        return _unchecked(NOT_PUBLISHED, tr("GitHub shows no published release of the Studio yet."))
    if status == 429 or (status == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers)):
        return _unchecked(RATE_LIMITED, tr(
            "GitHub's limit on requests from your address is used up for now. "
            "Try again in an hour."))
    return _unchecked(REFUSED, tr("GitHub answered with status %s. Try again later.", status))


def check(get: Optional[Getter] = None, current: str = __version__, url: str = REPO_URL) -> Answer:
    """Ask GitHub which release of the Studio is newest, and compare it with
    ``current``.  ``get`` stands in for the network in the tests.  Never
    raises for anything the network or GitHub does."""
    get = get or modio.careful_get
    try:
        response = get(latest_url(url), request_headers(), TIMEOUT, MOST_ANSWER)
    # An answer cut short is an http.client error, not an OSError (A10-38).
    except (OSError, http.client.HTTPException, ValueError) as exc:
        detail = str(getattr(exc, "reason", "") or exc) or type(exc).__name__
        return _unchecked(UNREACHABLE, tr(
            "GitHub could not be reached (%s). Check the connection and try again.", detail))
    if response.status != 200:
        return _why_not(response)
    try:
        release = read_release(json.loads(response.body.decode("utf-8")), url)
    except LookupError:
        return _unchecked(NOT_A_RELEASE, tr(
            "GitHub's newest release is a draft or a pre-release, which the Studio doesn't offer."))
    except ValueError:
        # Not JSON (a UnicodeDecodeError is a ValueError too), or no version.
        return _unchecked(UNREADABLE, tr("GitHub's answer didn't name a version of the Studio."))
    return Answer(NEWER if is_newer(release.version, current) else UP_TO_DATE, release)


def is_due(last: str, now: Optional[datetime.datetime] = None) -> bool:
    """Whether a day has passed since ``last``, an ISO time in UTC, or
    there is no ``last`` to go by.  A ``last`` later than now is a clock
    that was put back, and is no reason to wait."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        then = datetime.datetime.fromisoformat(str(last or "").replace("Z", "+00:00"))
    except ValueError:
        return True
    if then.tzinfo is None:
        then = then.replace(tzinfo=datetime.timezone.utc)
    return not (now - DAY < then <= now)


def stamp(now: Optional[datetime.datetime] = None) -> str:
    """``now`` as :func:`is_due` reads it back: ISO, in UTC."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def updated_from(previous: str, current: str = __version__) -> str:
    """The version the Studio was before it was updated to ``current``, or
    "" where it was not: a first start (no ``previous``), the same version,
    or going back to an older one.  ``previous`` is read from a file, so
    only a version is taken from it."""
    previous = version_of(previous)
    return previous if previous and is_newer(current, previous) else ""


def wants_saying(release: Optional[Release], skipped: str = "", current: str = __version__) -> bool:
    """Whether a newer ``release`` is worth a notice: newer than this Studio,
    and newer than the version you chose to skip."""
    if release is None or not is_newer(release.version, current):
        return False
    return not skipped or is_newer(release.version, skipped)
