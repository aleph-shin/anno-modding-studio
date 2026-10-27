"""Talking to mod.io: the one client the Studio uses, and nothing about Qt.

Reads -- what is the current file of these mods, which mod is called this,
the file itself -- need only a personal API key.  Writes -- uploading a file
to a mod of your own -- need a token as well, which the Studio gets by the
email code flow: mod.io sends a short code to the address, the code comes back
here, and a year-long token is the answer.  Before the first sign-in the terms
have to be shown and agreed, which is mod.io's rule and not a nicety.

A key is rate-limited to sixty requests a minute, so the client paces itself
to that rather than being told off, honours ``retry-after`` when it is, and
never lets the key or the token into an error message.

Every call goes to a game's own host, ``g-<id>.modapi.io``.  mod.io has
retired ``api.mod.io``: a sign-in request there is answered with error 11001,
"using the deprecated api.mod.io domain", and the listing endpoints will
follow.  Account endpoints -- the terms, the email code, who you are -- are
not about any game and are served by every game's host alike, so a client
made without a game uses one of the Annos' hosts for them.

The HTTP is behind three functions, ``transport`` for GETs, ``post`` for
writes and ``delete`` for taking something back -- a subscription -- so the
tests hand in fakes and the network is never touched by the suite.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import json
import os
import secrets as _random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional, Set

from .. import __version__
from .i18n import number, tr, trn

API ="https://api.mod.io/v1"  # retired by mod.io; kept only as a name
GAME_API = "https://g-{game_id}.modapi.io/v1"
# The host account endpoints go to when no game is in play. Any game's host
# serves them; this is Anno 1800's, and the Studio is about the two Annos.
ANY_GAME = 4169
USER_AGENT = "anno-modding-studio/%s" % __version__
PAGE = 100
# A personal key gets sixty requests a minute; one every 1.1 seconds leaves
# room for a retry inside the same minute.
MIN_INTERVAL = 1.1
MAX_ATTEMPTS = 4
TIMEOUT = 30.0
# The most a mod's summary may be: mod.io refuses a longer one.
SUMMARY_LIMIT = 250
# A download is read in pieces this big, so progress can be said and a stop
# honoured without waiting for the whole file.
CHUNK = 1024 * 1024
# Shorter than this and a key or token is redacted from nothing: see _redact.
REDACT_FROM = 6
# The most of an API answer read: a page of a hundred mods is well under a
# megabyte, and an answer that does not end would otherwise fill memory.
MOST_ANSWER = 16 * 1024 * 1024


class ModioError(RuntimeError):
    """Anything mod.io would not do for us, said without the key in it.

    ``status`` is the HTTP status mod.io answered with, where it answered at
    all: the message is for reading, in whatever language, not for matching.
    """

    status = 0


class KeyRejected(ModioError):
    """mod.io did not accept the API key at all."""


class Cancelled(ModioError):
    """Whoever asked for this stopped waiting for it."""


class SignedOut(ModioError):
    """The token is no longer good: expired, revoked, or logged out elsewhere."""


class Refused(ModioError):
    """mod.io understood the request and would not do it (a 403)."""

    def __init__(self, message: str, error_ref: int = 0) -> None:
        super().__init__(message)
        self.error_ref = error_ref


@dataclass
class Response:
    status: int
    headers: Dict[str, str]  # names lower-cased
    body: bytes


Transport = Callable[[str, Dict[str, str]], Response]
Poster = Callable[[str, Dict[str, str], bytes], Response]


class _CarefulRedirects(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to ``https``, and takes the sign-in along only
    to the host it was sent to.

    urllib's own handler copies every header but the content ones to the
    new address, so a redirect in an API answer would have handed the
    bearer token to whatever host it named, over plain http as readily.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise urllib.error.HTTPError(newurl, code, "a redirect away from https", headers, fp)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and not _same_host(req.full_url, newurl):
            for held in (new.headers, new.unredirected_hdrs):
                for name in [name for name in held if name.lower() == "authorization"]:
                    del held[name]
        return new


def _same_host(one: str, other: str) -> bool:
    return (urllib.parse.urlsplit(one).hostname or "").lower() == (
        urllib.parse.urlsplit(other).hostname or "").lower()


# Every request goes through this, never through urlopen's own opener.
_OPENER = urllib.request.build_opener(_CarefulRedirects)


def _answered(request: urllib.request.Request, timeout: float, most: int = MOST_ANSWER) -> Response:
    """One request, answered whatever the status, the body read no further
    than ``most``; only the network itself raises."""
    try:
        with _OPENER.open(request, timeout=timeout) as answer:
            return Response(
                answer.status,
                {k.lower(): v for k, v in answer.headers.items()},
                answer.read(most),
            )
    except urllib.error.HTTPError as err:
        headers = err.headers.items() if err.headers is not None else ()
        return Response(err.code, {k.lower(): v for k, v in headers}, err.read(most) if err.fp else b"")


def transport(url: str, headers: Dict[str, str]) -> Response:
    """One GET, answered whatever the status; only the network itself raises."""
    return _answered(urllib.request.Request(url, headers=headers), TIMEOUT)


def careful_get(url: str, headers: Dict[str, str], timeout: float, most: int) -> Response:
    """One GET to any host, as carefully as one to mod.io: ``https`` only,
    a redirect followed only to ``https`` and with no sign-in taken to
    another host, the body read no further than ``most`` bytes.  Answered
    whatever the status; only the network itself raises.

    For the Studio's other questions on the internet, so each is not
    hardened again on its own: whether a newer Studio is out
    (``core.studio_release``)."""
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise OSError("not an https address: %s" % url)
    return _answered(urllib.request.Request(url, headers=headers), timeout, most)


def post(url: str, headers: Dict[str, str], body: bytes) -> Response:
    """One POST with ``body`` as given; the caller set its content type."""
    return _answered(urllib.request.Request(url, data=body, headers=headers, method="POST"), TIMEOUT * 10)


def delete(url: str, headers: Dict[str, str], body: bytes = b"") -> Response:
    """One DELETE; mod.io answers a done one with 204 and no body."""
    return _answered(
        urllib.request.Request(url, data=body or None, headers=headers, method="DELETE"), TIMEOUT)


def web_url(url: str, fallback: str = "") -> str:
    """``url`` when it is a web page of mod.io's -- ``https``, on ``mod.io`` or
    a host under it -- else ``fallback``.

    For every address in mod.io's answers that the Studio may open: Windows
    opens whatever an address names, and a ``file:``, ``search-ms:`` or
    ``ms-`` one would do something other than show a page.
    """
    url = (url or "").strip()
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return fallback
    host = (parts.hostname or "").lower()
    if parts.scheme.lower() == "https" and (host == "mod.io" or host.endswith(".mod.io")):
        return url
    return fallback


def multipart(fields: Dict[str, str], files: Dict[str, tuple]) -> tuple:
    """A multipart/form-data body: ``(content_type, bytes)``.

    ``files`` maps a field name to ``(filename, bytes)``.  The stdlib has no
    encoder for this and the shape is fixed, so here it is.
    """
    boundary = "----AnnoModdingStudio" + _random.token_hex(16)
    lines: List[bytes] = []
    for name, value in fields.items():
        lines.append(("--%s" % boundary).encode())
        lines.append(('Content-Disposition: form-data; name="%s"' % name).encode())
        lines.append(b"")
        lines.append(str(value).encode("utf-8"))
    for name, (filename, data) in files.items():
        lines.append(("--%s" % boundary).encode())
        lines.append(
            (
                'Content-Disposition: form-data; name="%s"; filename="%s"'
                % (name, filename.replace('"', "_"))
            ).encode("utf-8")
        )
        lines.append(b"Content-Type: application/octet-stream")
        lines.append(b"")
        lines.append(data)
    lines.append(("--%s--" % boundary).encode())
    lines.append(b"")
    return "multipart/form-data; boundary=%s" % boundary, b"\r\n".join(lines)


@dataclass(frozen=True)
class Terms:
    """What mod.io requires be shown before the first sign-in."""

    text: str
    agree: str = "I Agree"
    disagree: str = "No, Thanks"
    terms_url: str = "https://mod.io/terms"
    privacy_url: str = "https://mod.io/privacy"


@dataclass(frozen=True)
class Token:
    access_token: str
    expires: int


@dataclass(frozen=True)
class Me:
    id: int
    username: str


@dataclass(frozen=True)
class ModSummary:
    """The parts of a mod object an update check or a search result shows."""

    id: int
    name: str
    name_id: str = ""
    profile_url: str = ""
    submitted_by: str = ""
    date_updated: int = 0
    modfile_id: int = 0
    version: str = ""
    file_added: int = 0
    filesize: int = 0
    # What the file unpacks to: the number a free-space check has to use,
    # since the zip itself is deleted once it has been unpacked.
    filesize_uncompressed: int = 0
    downloads: int = 0
    # The file itself: where to get it (a link that expires within hours, so
    # it is fetched fresh before use), what it is called, and how to know the
    # bytes that arrive are the ones mod.io meant.
    download_url: str = ""
    filename: str = ""
    md5: str = ""
    # mod.io scans every upload. A file its scan flagged is never downloaded.
    virus_positive: bool = False
    # A hidden mod is one its owner has not made public: kept on mod.io, seen
    # only by its team, and exactly what a creator uses while a mod is not
    # finished. Only the single-mod endpoint returns one, and only signed in.
    visible: bool = True
    # The plain text mod.io shows on the mod's card and in search, under its
    # name. Its page's description is rich text, and the website's to edit.
    summary: str = ""

    @property
    def url(self) -> str:
        return self.profile_url

    @property
    def has_file(self) -> bool:
        return bool(self.modfile_id and self.download_url)


def summarise(mod: Dict[str, Any]) -> ModSummary:
    modfile = mod.get("modfile") or {}
    stats = mod.get("stats") or {}
    by = mod.get("submitted_by") or {}
    return ModSummary(
        id=int(mod.get("id") or 0),
        name=str(mod.get("name") or "").strip(),
        name_id=str(mod.get("name_id") or ""),
        profile_url=web_url(str(mod.get("profile_url") or "")),
        submitted_by=str(by.get("username") or ""),
        date_updated=int(mod.get("date_updated") or 0),
        modfile_id=int(modfile.get("id") or 0),
        version=str(modfile.get("version") or "").strip(),
        file_added=int(modfile.get("date_added") or 0),
        filesize=int(modfile.get("filesize") or 0),
        filesize_uncompressed=int(modfile.get("filesize_uncompressed") or 0),
        downloads=int(stats.get("downloads_total") or 0),
        download_url=str((modfile.get("download") or {}).get("binary_url") or ""),
        filename=str(modfile.get("filename") or ""),
        md5=str((modfile.get("filehash") or {}).get("md5") or "").lower(),
        virus_positive=bool(modfile.get("virus_positive")),
        visible=bool(int(mod.get("visible", 1) or 0)) if mod.get("visible") is not None else True,
        summary=str(mod.get("summary") or "").strip(),
    )


@dataclass
class CollectionSummary:
    """The parts of a collection object a result row and a plan show.

    A collection is a list of mod ids somebody curated, and nothing more: no
    versions are pinned, so what it installs is whatever each mod's current
    file is on the day.  It has no ``profile_url`` of its own, which is why
    :attr:`url` is built rather than read.
    """

    id: int
    name: str
    name_id: str = ""
    game_name_id: str = ""
    summary: str = ""
    submitted_by: str = ""
    date_updated: int = 0
    mods_total: int = 0
    filesize: int = 0
    filesize_uncompressed: int = 0
    downloads: int = 0
    followers: int = 0
    # mod.io's own word for a collection some of whose mods are no longer
    # there, and how many of them are not.
    incomplete: bool = False
    incomplete_mods: int = 0

    @property
    def url(self) -> str:
        if not (self.game_name_id and self.name_id):
            return ""
        return "https://mod.io/g/%s/c/%s" % (self.game_name_id, self.name_id)


def summarise_collection(raw: Dict[str, Any]) -> CollectionSummary:
    stats = raw.get("stats") or {}
    by = raw.get("submitted_by") or {}
    return CollectionSummary(
        id=int(raw.get("id") or 0),
        name=str(raw.get("name") or "").strip(),
        name_id=str(raw.get("name_id") or ""),
        game_name_id=str(raw.get("game_name_id") or ""),
        summary=str(raw.get("summary") or "").strip(),
        submitted_by=str(by.get("username") or ""),
        date_updated=int(raw.get("date_updated") or 0),
        mods_total=int(stats.get("mods_total") or 0),
        filesize=int(raw.get("filesize") or 0),
        filesize_uncompressed=int(raw.get("filesize_uncompressed") or 0),
        downloads=int(stats.get("downloads_total") or 0),
        followers=int(stats.get("followers_total") or 0),
        incomplete=bool(raw.get("incomplete")),
        incomplete_mods=int(stats.get("incomplete") or 0),
    )


class Client:
    """A paced, key-redacting, cancellable mod.io reader.

    ``cancelled`` is asked before every request; a worker thread passes its
    stop flag so a long check can be abandoned between calls.
    """

    def __init__(
        self,
        key: str,
        fetch: Transport = transport,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        cancelled: Callable[[], bool] = lambda: False,
        token: str = "",
        poster: Poster = post,
        game_id: int = 0,
        deleter: Poster = delete,
    ) -> None:
        self._key = key.strip()
        self._token = (token or "").strip()
        self.base = GAME_API.format(game_id=int(game_id or ANY_GAME))
        self._fetch = fetch
        self._post = poster
        self._delete = deleter
        self._sleep = sleep
        self._clock = clock
        self._cancelled = cancelled
        self._last = -MIN_INTERVAL

    @property
    def signed_in(self) -> bool:
        return bool(self._token)

    def _headers(self, **more: str) -> Dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **more}
        if self._token:
            headers["Authorization"] = "Bearer " + self._token
        return headers

    # -- one request ------------------------------------------------------
    def get(self, path: str, base: str = "", **params: Any) -> Dict[str, Any]:
        query = urllib.parse.urlencode({"api_key": self._key, **params})
        url = "%s%s?%s" % (base or self.base, path, query)
        return self._exchange(path, lambda: self._fetch(url, self._headers()))

    def post_form(
        self, path: str, base: str = "", files: Optional[Dict[str, tuple]] = None,
        as_multipart: bool = False, **fields: Any
    ) -> Dict[str, Any]:
        """A write: form-encoded, or multipart when there is a file in it or
        ``as_multipart`` says the endpoint takes nothing else."""
        url = "%s%s?%s" % (base or self.base, path, urllib.parse.urlencode({"api_key": self._key}))
        if files or as_multipart:
            content_type, body = multipart({k: str(v) for k, v in fields.items()}, files or {})
        else:
            content_type = "application/x-www-form-urlencoded"
            body = urllib.parse.urlencode({k: str(v) for k, v in fields.items()}).encode()
        headers = self._headers(**{"Content-Type": content_type})
        # A write is sent again only after a 429, which says mod.io did not
        # take it: after a reset or a 5xx it may have, and a second sign-in
        # email or a code spent twice is worse than the error (A11-43).
        return self._exchange(
            path, lambda: self._post(url, headers, body), retry=not files, idempotent=False)

    def delete_path(self, path: str, base: str = "") -> Dict[str, Any]:
        """A DELETE, which takes something of the account's back."""
        url = "%s%s?%s" % (base or self.base, path, urllib.parse.urlencode({"api_key": self._key}))
        headers = self._headers(**{"Content-Type": "application/x-www-form-urlencoded"})
        return self._exchange(path, lambda: self._delete(url, headers, b""))

    def _exchange(self, path: str, send, retry: bool = True, idempotent: bool = True) -> Dict[str, Any]:
        """``retry`` allows sending again at all; ``idempotent`` also when it is
        not known whether mod.io took the request (a reset, a 5xx)."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if self._cancelled():
                raise Cancelled("stopped")
            self._wait_turn()
            try:
                answer = send()
            except (OSError, http.client.HTTPException) as err:
                # An answer cut short is an http.client error and not an
                # OSError: it is as much a failed request as a reset (A10-38).
                if attempt == MAX_ATTEMPTS or not (retry and idempotent):
                    raise ModioError(tr("could not reach mod.io: %s", self._redact(err))) from None
                self._sleep(_retry_after(None, attempt))
                continue
            if 200 <= answer.status < 300:
                try:
                    body = json.loads(answer.body.decode("utf-8")) if answer.body else {}
                except (ValueError, UnicodeDecodeError):
                    raise ModioError(tr("mod.io answered with something that is not JSON")) from None
                if not isinstance(body, dict):
                    raise ModioError(tr("mod.io answered with something unexpected"))
                return body
            if answer.status == 401:
                message, ref = _error_in(answer.body)
                if self._token and message:
                    raise SignedOut(tr(
                        "mod.io no longer accepts the sign-in. Sign in again (%s)", self._redact(message)
                    ))
                if self._token:
                    raise SignedOut(tr("mod.io no longer accepts the "
                                       "sign-in. Sign in again"))
                if message:
                    raise KeyRejected(tr("mod.io did not accept the API key (%s)", self._redact(message)))
                raise KeyRejected(tr("mod.io did not accept the API key"))
            if answer.status == 403:
                message, ref = _error_in(answer.body)
                raise Refused(self._redact(message) if message else tr("mod.io refused this"), ref)
            if retry and (answer.status == 429 or (answer.status >= 500 and idempotent)):
                if attempt == MAX_ATTEMPTS:
                    break
                self._sleep(_retry_after(answer.headers.get("retry-after"), attempt))
                continue
            message, _ref = _error_in(answer.body)
            error = ModioError(tr(
                "mod.io answered %(status)s on %(path)s: %(message)s",
                status=answer.status, path=path,
                message=self._redact(message or answer.body[:300].decode("utf-8", "replace")),
            ))
            error.status = answer.status
            raise error
        raise ModioError(trn(
            "gave up on %(path)s after %(n)s attempt", "gave up on %(path)s after %(n)s attempts",
            MAX_ATTEMPTS, path=path,
        ))

    def _wait_turn(self) -> None:
        wait = self._last + MIN_INTERVAL - self._clock()
        if wait > 0:
            self._sleep(wait)
        self._last = self._clock()

    def _redact(self, text: object) -> str:
        text = str(text)
        # A secret of a few characters is not one, and replacing it would
        # blank every letter of the message that happens to contain it.
        for secret, mark in ((self._key, "<key>"), (self._token, "<token>")):
            if len(secret) >= REDACT_FROM:
                text = text.replace(secret, mark)
        return text

    def pages(self, path: str, base: str = "", **params: Any) -> Iterator[Dict[str, Any]]:
        offset = 0
        while True:
            body = self.get(path, base, _limit=PAGE, _offset=offset, **params)
            yield body
            count = int(body.get("result_count") or 0)
            total = int(body.get("result_total") or 0)
            offset += count
            if count == 0 or offset >= total:
                return

    # -- what the Studio asks ----------------------------------------------
    def check_key(self) -> None:
        """Raise :class:`KeyRejected` if the key is no good; otherwise nothing."""
        self.get("/games", _limit=1)

    def mods_by_ids(self, game_id: int, ids: Iterable[int]) -> Dict[int, ModSummary]:
        """The current state of these mods, a hundred to a request."""
        wanted = sorted({int(i) for i in ids if int(i)})
        found: Dict[int, ModSummary] = {}
        base = GAME_API.format(game_id=game_id)
        for start in range(0, len(wanted), PAGE):
            chunk = wanted[start:start + PAGE]
            body = self.get(
                "/games/%d/mods" % game_id, base,
                **{"id-in": ",".join(str(i) for i in chunk), "_limit": PAGE},
            )
            for mod in body.get("data") or []:
                summary = summarise(mod)
                if summary.id:
                    found[summary.id] = summary
        return found

    def mod(self, game_id: int, mod_id: int) -> Optional[ModSummary]:
        """One mod as it is right now -- and so with a download link that has
        not expired, which is why it is asked for again just before a download.

        Asked for by itself rather than through the game's listing, because the
        listing never returns a hidden mod, not even to its owner; the single
        endpoint does, once signed in.  A mod that is not there, or is hidden
        from whoever is asking, is None either way.
        """
        base = GAME_API.format(game_id=game_id)
        try:
            body = self.get("/games/%d/mods/%d" % (game_id, int(mod_id)), base)
        except Refused:
            return None
        except ModioError as err:
            if err.status == 404:
                return None
            raise
        summary = summarise(body)
        return summary if summary.id else None

    def file_with_md5(self, game_id: int, mod_id: int, md5: str) -> int:
        """The id of the file of this mod whose MD5 is ``md5``, or 0: which of
        a mod's files a zip downloaded by hand was.  Every file the mod has
        is listed and looked through here; an old one is as good as the
        current one for saying which it is."""
        wanted = (md5 or "").strip().lower()
        if not wanted:
            return 0
        base = GAME_API.format(game_id=game_id)
        for body in self.pages("/games/%d/mods/%d/files" % (game_id, int(mod_id)), base):
            for found in body.get("data") or []:
                if str((found.get("filehash") or {}).get("md5") or "").lower() == wanted:
                    return int(found.get("id") or 0)
        return 0

    # -- signing in, and what needs it ----------------------------------------
    def terms(self) -> Terms:
        """What must be shown, and agreed to, before the first sign-in."""
        body = self.get("/authenticate/terms")
        buttons = body.get("buttons") or {}
        links = body.get("links") or {}
        return Terms(
            text=str(body.get("plaintext") or body.get("html") or ""),
            agree=str((buttons.get("agree") or {}).get("text") or "I Agree"),
            disagree=str((buttons.get("disagree") or {}).get("text") or "No, Thanks"),
            terms_url=web_url(str((links.get("terms") or {}).get("url") or ""), "https://mod.io/terms"),
            privacy_url=web_url(
                str((links.get("privacy") or {}).get("url") or ""), "https://mod.io/privacy"),
        )

    def request_code(self, email: str) -> str:
        """Ask mod.io to email a sign-in code; returns what it said."""
        body = self.post_form("/oauth/emailrequest", email=email.strip())
        return str(body.get("message") or tr("A code has been sent."))

    def exchange_code(self, code: str, terms_agreed: bool = True) -> Token:
        """Turn the emailed code into a token good for a year."""
        body = self.post_form(
            "/oauth/emailexchange", security_code=code.strip(), terms_agreed="true" if terms_agreed else "false"
        )
        token = str(body.get("access_token") or "")
        if not token:
            raise ModioError(tr("mod.io accepted the code but sent no token"))
        return Token(token, int(body.get("date_expires") or 0))

    def me(self) -> Me:
        body = self.get("/me")
        return Me(int(body.get("id") or 0), str(body.get("username") or ""))

    def my_mods(self, game_id: int) -> List[ModSummary]:
        """The signed-in account's own mods for this game."""
        found: List[ModSummary] = []
        for page in self.pages("/me/mods", game_id=int(game_id)):
            found.extend(summarise(mod) for mod in page.get("data") or [])
        return found

    def subscriptions(self, game_id: int) -> Set[int]:
        """The mod.io ids of every mod of this game the signed-in account is
        subscribed to.  A subscription is the account's, not the machine's:
        the game's own browser, signed in as it anywhere, installs these."""
        found: Set[int] = set()
        base = GAME_API.format(game_id=game_id)
        for page in self.pages("/me/subscribed", base, game_id=int(game_id)):
            for mod in page.get("data") or []:
                with contextlib.suppress(TypeError, ValueError):
                    if int(mod.get("id") or 0):
                        found.add(int(mod["id"]))
        return found

    def unsubscribe(self, game_id: int, mod_id: int) -> None:
        """Take the signed-in account's subscription to one mod away, on every
        machine it plays on.  The Studio itself never does: the game's client
        keeps its copy of a mod unsubscribed from outside the game, so this
        drops the subscription everywhere and frees nothing here.  The
        unsubscribe probe uses it, and ``subscribe`` to put a mod back."""
        base = GAME_API.format(game_id=game_id)
        self.delete_path("/games/%d/mods/%d/subscribe" % (game_id, int(mod_id)), base)

    def subscribe(self, game_id: int, mod_id: int) -> None:
        """Subscribe the signed-in account to one mod.  The Studio never does
        this by itself; the unsubscribe probe uses it to put a mod back."""
        base = GAME_API.format(game_id=game_id)
        self.post_form("/games/%d/mods/%d/subscribe" % (game_id, int(mod_id)), base)

    def logout(self) -> None:
        with contextlib.suppress(ModioError):
            self.post_form("/oauth/logout")

    def add_modfile(
        self,
        game_id: int,
        mod_id: int,
        archive: Path,
        version: str,
        changelog: str = "",
        active: bool = True,
    ) -> ModSummary:
        """Upload a new file to a mod of yours.  Live at once where the game
        does not curate, which neither Anno does -- so this is the last step,
        not a draft."""
        data = Path(archive).read_bytes()
        base = GAME_API.format(game_id=game_id)
        body = self.post_form(
            "/games/%d/mods/%d/files" % (game_id, mod_id),
            base,
            files={"filedata": (Path(archive).name, data)},
            version=version.strip(),
            changelog=changelog.strip(),
            active="true" if active else "false",
            filehash=hashlib.md5(data).hexdigest(),
        )
        return summarise({"id": mod_id, "modfile": body})

    def set_summary(self, game_id: int, mod_id: int, summary: str) -> ModSummary:
        """Replace the summary of a mod of yours, and nothing else of it.

        Multipart with no file in it: mod.io's Edit Mod refuses the same
        field form-encoded, which every other write here is."""
        base = GAME_API.format(game_id=game_id)
        body = self.post_form("/games/%d/mods/%d" % (game_id, mod_id), base,
                              as_multipart=True, summary=summary.strip())
        return summarise(body)

    def by_name_id(self, game_id: int, name_id: str) -> Optional[ModSummary]:
        """The mod a page address names -- by its slug, which is what the
        address carries, rather than by its number, which it does not."""
        name_id = name_id.strip().lower()
        if not name_id:
            return None
        base = GAME_API.format(game_id=game_id)
        body = self.get("/games/%d/mods" % game_id, base, name_id=name_id, _limit=1)
        for mod in body.get("data") or []:
            summary = summarise(mod)
            if summary.name_id.lower() == name_id:
                return summary
        return None

    # -- collections ---------------------------------------------------------
    def collections(self, game_id: int, query: str, limit: int = 20) -> List[CollectionSummary]:
        """Collections whose names match ``query``, as mod.io ranks them."""
        query = query.strip()
        if not query:
            return []
        base = GAME_API.format(game_id=game_id)
        body = self.get(
            "/games/%d/collections" % game_id, base, _q=query, _limit=max(1, min(limit, PAGE)),
        )
        return [summarise_collection(raw) for raw in body.get("data") or []]

    def collection_by_name_id(self, game_id: int, name_id: str) -> Optional[CollectionSummary]:
        """The collection a page address names, by the slug the address carries."""
        name_id = name_id.strip().lower()
        if not name_id:
            return None
        base = GAME_API.format(game_id=game_id)
        body = self.get("/games/%d/collections" % game_id, base, name_id=name_id, _limit=1)
        for raw in body.get("data") or []:
            found = summarise_collection(raw)
            if found.name_id.lower() == name_id:
                return found
        return None

    def collection_mods(self, game_id: int, collection_id: int) -> List[ModSummary]:
        """Every mod in a collection, each with its current file and a link to it.

        Whole mod objects come back, so the list is the install plan: one
        request a hundred mods, not one a mod.  Paged to the end rather than
        to the game's declared ``limit_collection_mods`` -- that says 100 for
        both Annos and collections of three hundred are listed regardless.
        """
        base = GAME_API.format(game_id=game_id)
        found: List[ModSummary] = []
        path = "/games/%d/collections/%d/mods" % (game_id, int(collection_id))
        for page in self.pages(path, base):
            for raw in page.get("data") or []:
                summary = summarise(raw)
                if summary.id:
                    found.append(summary)
        return found

    def dependencies(self, game_id: int, mod_id: int) -> List[ModSummary]:
        """The mods this one declares it needs, as mod.io lists them."""
        base = GAME_API.format(game_id=game_id)
        found: List[ModSummary] = []
        for page in self.pages("/games/%d/mods/%d/dependencies" % (game_id, mod_id), base):
            for entry in page.get("data") or []:
                found.append(summarise({**entry, "id": entry.get("mod_id") or entry.get("id")}))
        return found

    def search(self, game_id: int, query: str, limit: int = 20) -> List[ModSummary]:
        """Mods whose names match ``query``, best first, as mod.io ranks them."""
        query = query.strip()
        if not query:
            return []
        base = GAME_API.format(game_id=game_id)
        body = self.get(
            "/games/%d/mods" % game_id, base, _q=query, _limit=max(1, min(limit, PAGE)),
        )
        return [summarise(mod) for mod in body.get("data") or []]


def _retry_after(header: Optional[str], attempt: int) -> float:
    try:
        return max(1.0, float(header)) if header else float(min(30, 2**attempt))
    except ValueError:
        return float(min(30, 2**attempt))



# -- the file itself -----------------------------------------------------------
class DownloadError(ModioError):
    """The file could not be fetched whole and as mod.io described it."""


Progress = Callable[[int, int], None]
Chunks = Callable[[str], Iterator[bytes]]


def stream(url: str) -> Iterator[bytes]:
    """The bytes at ``url``, a chunk at a time, following the CDN redirect.

    Only over ``https``, the address and any redirect alike: urllib would
    as readily open a ``file:`` or ``ftp:`` address mod.io's answer named."""
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise OSError(tr("mod.io gave a download address that is not https"))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with _OPENER.open(request, timeout=TIMEOUT) as answer:
        while True:
            piece = answer.read(CHUNK)
            if not piece:
                return
            yield piece


def download(
    url: str,
    dest: Path,
    *,
    chunks: Chunks = stream,
    progress: Optional[Progress] = None,
    cancelled: Callable[[], bool] = lambda: False,
    md5: str = "",
    size: int = 0,
) -> Path:
    """Fetch ``url`` into ``dest``, and only leave it there if it is right.

    Written beside its destination and moved into place at the end, so a
    stop, a dropped connection or a checksum that does not match leaves no
    half-file wearing the finished name.  Raises :class:`Cancelled` if asked
    to stop, :class:`DownloadError` for anything else.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    digest = hashlib.md5()
    done = 0
    try:
        with open(partial, "wb") as out:
            for piece in chunks(url):
                if cancelled():
                    raise Cancelled("stopped")
                done += len(piece)
                if size and done > size:
                    # Stopped where it passed the size, not written to the
                    # end of a body that need not end.
                    break
                out.write(piece)
                digest.update(piece)
                if progress is not None:
                    progress(done, size)
    except Cancelled:
        _discard(partial)
        raise
    except (OSError, http.client.HTTPException) as err:
        # http.client's errors -- a body cut short, a garbled answer -- are
        # not OSErrors, and are as much a failed download as a reset.
        _discard(partial)
        raise DownloadError(tr("the download failed: %s", err)) from None
    if size and done != size:
        _discard(partial)
        raise DownloadError(trn(
            "the download is %(n)s byte, and mod.io reported %(said)s",
            "the download is %(n)s bytes, and mod.io reported %(said)s", done, said=number(size),
        ))
    if md5 and digest.hexdigest() != md5.lower():
        _discard(partial)
        raise DownloadError(tr("the download does not match the checksum mod.io gave for it"))
    os.replace(partial, dest)
    return dest


def _discard(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


def _error_in(body: bytes) -> tuple:
    """The message and error_ref in a mod.io error body, if it is one."""
    try:
        raw = json.loads(body.decode("utf-8"))
        error = raw.get("error") or {}
        return str(error.get("message") or ""), int(error.get("error_ref") or 0)
    except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
        return "", 0


@dataclass(frozen=True)
class PageLink:
    """What a mod.io page address says: which game, and which mod or
    collection by slug.  Exactly one of the two slugs is set."""

    game_name_id: str
    mod_name_id: str = ""
    collection_name_id: str = ""


def parse_page(text: str) -> Optional[PageLink]:
    """A pasted ``https://mod.io/g/<game>/m/<mod>`` address, or a
    collection's ``…/g/<game>/c/<collection>``, or None.

    Anything after the slug -- a tab, a query -- is ignored; so is whether
    the scheme or the ``www.`` is there.  Not an address is None, and so is
    an address for something other than a mod's or a collection's page.
    """
    text = (text or "").strip()
    if "mod.io" not in text.lower():
        return None
    if "://" not in text:
        text = "https://" + text
    parsed = urllib.parse.urlparse(text)
    if not parsed.netloc.lower().endswith("mod.io"):
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) >= 4 and parts[0] == "g" and parts[2] == "m":
        return PageLink(parts[1].lower(), mod_name_id=parts[3].lower())
    if len(parts) >= 4 and parts[0] == "g" and parts[2] == "c":
        return PageLink(parts[1].lower(), collection_name_id=parts[3].lower())
    return None
