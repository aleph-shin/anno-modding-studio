"""Fetch the text files out of every Anno mod on mod.io, for a structure census.

The studio has only been checked against one person's installed mods, and the
games accept layouts nobody here has seen.  This pulls, for every mod mod.io
lists, the files a structure census reads -- XML, modinfo, anything an
``<Include>`` is likely to name -- and the list of everything else in the zip,
without downloading the textures and models that make up nearly all of it.

A zip keeps its table of contents at the end, and mod.io's CDN answers HTTP
range requests, several ranges to a request.  So a large mod costs a request
for its tail and one asking for every wanted member at once.  Mods under
``--full-below`` bytes are simply downloaded whole and checked against mod.io's
MD5.  The zip format itself is left to :mod:`zipfile`, reading through a file
object that fetches only the byte ranges it is asked for -- so zip64, every
compression method zipfile knows and each member's CRC are all its problem.

Each mod's download link is resolved once, paced like an API call; the ranges
then go straight to the CDN, so mod.io's own server sees one request per mod
however many ranges it took.

    python tools/modio_fetch.py
    python tools/modio_fetch.py --game anno-1800 --mod 3211479

It uses the official API with a personal key, read from ``MODIO_API_KEY`` and
never printed, stays under the key's 60 requests a minute, and honours
``retry-after`` on a 429.

Per mod, under ``$MODIO_DIR/census/<game>/<mod id>/``: ``manifest.json``
(every zip entry, what was extracted, what was not and why) and ``files/``
(the extracted files at their paths inside the zip).  A mod whose manifest
already describes its current file is not fetched again, so an interrupted
run picks up where it stopped.  None of it may be inside the repository: it
is other people's work.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from anno_modding_studio import __version__  # noqa: E402
from anno_modding_studio.core import modio, modzip  # noqa: E402
from anno_modding_studio.core.modio import ModioError  # noqa: E402

API = "https://api.mod.io/v1"
GAME_API = "https://g-{game_id}.modapi.io/v1"
USER_AGENT = "anno-modding-studio-census/%s" % __version__
DEFAULT_GAMES = ("anno-1800", "anno-117-pax-romana")
# Attempts at the CDN; mod.io's API is the Studio client's business.
MAX_ATTEMPTS = 5
# The key never sits in a file or on a command line; the output folder comes
# from the environment too, since it used to be wherever the key file was.
KEY_VAR = "MODIO_API_KEY"
DIR_VAR = "MODIO_DIR"

class Client(modio.Client):
    """The Studio's mod.io client, its pacing lent to the download links.

    Resolving a link is a request to mod.io's own server too, so it waits its
    turn with the API calls under the one rate limit.
    """

    def wait_turn(self) -> None:
        """Hold off until one more request fits under the key's rate limit."""
        self._wait_turn()


def _retry_after(header: Optional[str], attempt: int) -> float:
    try:
        return max(1.0, float(header)) if header else float(min(60, 2**attempt))
    except ValueError:
        return float(min(60, 2**attempt))


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _read_key() -> str:
    key = os.environ.get(KEY_VAR, "").strip()
    if not key or any(c.isspace() for c in key):
        raise ModioError(f"{KEY_VAR} does not hold a single API key")
    return key


def _data_dir(parser: argparse.ArgumentParser, flag: str) -> Path:
    """The folder output goes under when `flag` is not given."""
    folder = os.environ.get(DIR_VAR, "").strip()
    if not folder:
        parser.error(f"give {flag}, or set {DIR_VAR} to a folder outside the repository")
    return Path(folder)


def find_games(client: Client, wanted: Iterable[str]) -> List[Dict[str, Any]]:
    """mod.io's game objects for these name_ids, in the order asked for."""
    names = list(wanted)
    data = client.get("/games", API, **{"name_id-in": ",".join(names)})["data"]
    found = {game["name_id"]: game for game in data}
    missing = [name for name in names if name not in found]
    if missing:
        raise ModioError(f"no such game on mod.io: {', '.join(missing)}")
    return [found[name] for name in names]


def list_mods(client: Client, game: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every live mod mod.io has for a game, as the API returns them, once each."""
    mods: Dict[int, Dict[str, Any]] = {}
    listed = 0
    # Newer games refuse api.mod.io for their mods and want their own domain, which
    # needs the game's id -- so the game lookup, through api.mod.io, comes first.
    base = GAME_API.format(game_id=game["id"])
    # Sorted by id so a mod published mid-run cannot shift a page boundary and
    # make another mod appear twice or not at all.
    for page in client.pages(f"/games/{game['id']}/mods", base, _sort="id"):
        listed += page["result_count"]
        for mod in page["data"]:
            mods.setdefault(mod["id"], mod)
        print(f"  {listed} of {page['result_total']}")
    if len(mods) != listed:
        print(f"  warning: {listed - len(mods)} mods listed twice", file=sys.stderr)
    return list(mods.values())

# What a census reads.  An <Include> can name any file, so the manifest's full
# entry list is what shows one that was not fetched.
TEXT_SUFFIXES = (".xml", ".json", ".jsonc", ".include", ".cfg", ".ifo", ".fc")
ARCHIVE_SUFFIXES = (".zip", ".rda", ".7z", ".rar")
FULL_BELOW = 1024**2
# zipfile looks for the end record in the last 22 bytes, then in the last
# 64 KB + 22 (the longest a zip comment can be): one tail fetch covers both.
TAIL = 65536 + 22
READAHEAD = 64 * 1024
# A member's local header can carry a longer extra field than its central
# directory entry (zip64 sizes, for one); this much is fetched past the data
# on spec, and a member needing more costs one more request.
HEADER_SLACK = 1024
MERGE_GAP = 16 * 1024
RANGES_PER_REQUEST = 50
MAX_MEMBER = 256 * 1024**2
# A server ignoring Range on a small file costs nothing; on a 3 GB one it would
# be the very download this is here to avoid.
IGNORED_RANGE_LIMIT = 8 * 1024**2
REDIRECTS = (301, 302, 303, 307, 308)
DONE = ("ok", "partial", "no-file", "flagged")
MANIFEST = "manifest.json"


class FetchError(RuntimeError):
    pass


@dataclass
class Response:
    status: int
    headers: Dict[str, str] = field(default_factory=dict)  # names lower-cased
    body: bytes = b""


# (method, url, Range header or None) -> Response, never following redirects.
Transport = Callable[[str, str, Optional[str]], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def http(method: str, url: str, byte_range: Optional[str]) -> Response:
    headers = {"User-Agent": USER_AGENT}
    if byte_range:
        headers["Range"] = byte_range
    opener = urllib.request.build_opener(_NoRedirect)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        request = urllib.request.Request(url, headers=headers, method=method)
        try:
            with opener.open(request, timeout=120) as response:
                found = {k.lower(): v for k, v in response.headers.items()}
                length = int(found.get("content-length") or 0)
                if byte_range and response.status == 200 and length > IGNORED_RANGE_LIMIT:
                    raise FetchError("the server sent the whole file, not the range asked for")
                body = b"" if method == "HEAD" else response.read()
                return Response(response.status, found, body)
        except urllib.error.HTTPError as err:
            return Response(err.code, {k.lower(): v for k, v in err.headers.items()})
        except (urllib.error.URLError, TimeoutError, ConnectionError) as err:
            if attempt == MAX_ATTEMPTS:
                raise FetchError(f"network: {err}") from None
            time.sleep(_retry_after(None, attempt))
    raise FetchError(f"gave up after {MAX_ATTEMPTS} attempts")


def resolver(
    pace: Callable[[], None],
    transport: Transport,
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[str], str]:
    """Turn a mod.io download link into the CDN address it redirects to."""

    def resolve(url: str) -> str:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            pace()
            response = transport("HEAD", url, None)
            if response.status in REDIRECTS and response.headers.get("location"):
                return urllib.parse.urljoin(url, response.headers["location"])
            if response.status == 200:
                return url
            if response.status == 429 or response.status >= 500:
                sleep(_retry_after(response.headers.get("retry-after"), attempt))
                continue
            raise FetchError(f"{response.status} resolving the download link")
        raise FetchError(f"gave up resolving the download link after {MAX_ATTEMPTS} attempts")

    return resolve


_CONTENT_RANGE = re.compile(r"bytes\s+(\d+)-(\d+)/(\d+|\*)", re.IGNORECASE)


def _content_range(value: Optional[str]) -> Tuple[int, int, Optional[int]]:
    match = _CONTENT_RANGE.search(value or "")
    if not match:
        raise FetchError(f"unreadable Content-Range {value!r}")
    total = None if match[3] == "*" else int(match[3])
    return int(match[1]), int(match[2]), total


Part = Tuple[int, Optional[int], bytes]  # start, total file size if given, bytes


def parse_multipart(body: bytes, content_type: str) -> List[Part]:
    """The parts of a multipart/byteranges response.

    Each part is cut to exactly the length its Content-Range gives, so a
    boundary string that happens to occur inside the zip data is never looked at.
    """
    match = re.search(r'boundary="?([^";]+)"?', content_type)
    if not match:
        raise FetchError("a multipart response without a boundary")
    delimiter = b"--" + match[1].encode("latin-1")
    parts: List[Part] = []
    pos = 0
    while True:
        at = body.find(delimiter, pos)
        if at < 0:
            break
        pos = at + len(delimiter)
        if body[pos : pos + 2] == b"--":
            break
        head_end = body.find(b"\r\n\r\n", pos)
        if head_end < 0:
            raise FetchError("a multipart response ended inside a part's headers")
        head = body[pos:head_end].decode("latin-1")
        found = re.search(r"content-range:([^\r\n]*)", head, re.IGNORECASE)
        if not found:
            raise FetchError("a multipart part without a Content-Range")
        start, end, total = _content_range(found[1])
        data_start = head_end + 4
        data = body[data_start : data_start + end - start + 1]
        if len(data) != end - start + 1:
            raise FetchError("a multipart part arrived short")
        parts.append((start, total, data))
        pos = data_start + len(data)
    if not parts:
        raise FetchError("a multipart response with no parts")
    return parts


def merge_spans(spans: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Inclusive byte spans, overlapping or near neighbours joined."""
    merged: List[List[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + MERGE_GAP:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


class Remote:
    """One file on the CDN, fetched only in the byte ranges something reads."""

    def __init__(
        self,
        url: str,
        size: int,
        transport: Transport,
        refresh: Callable[[], str],
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.url = url
        self.size = size
        self.requests = 0
        self.received = 0
        self._transport = transport
        self._refresh = refresh
        self._sleep = sleep
        self._refreshed = False
        self._segments: List[Tuple[int, bytes]] = []

    def whole(self) -> bytes:
        response = self._request(None)
        if response.status != 200:
            raise FetchError(f"{response.status} downloading the file")
        self._store_whole(response.body)
        return response.body

    def fetch(self, spans: List[Tuple[int, int]]) -> None:
        """Fetch these inclusive byte spans in as few requests as it takes."""
        wanted = [(a, b) for a, b in merge_spans(spans) if self._slice(a, b + 1) is None]
        for i in range(0, len(wanted), RANGES_PER_REQUEST):
            group = wanted[i : i + RANGES_PER_REQUEST]
            response = self._request("bytes=" + ",".join(f"{a}-{b}" for a, b in group))
            if response.status == 200:
                self._store_whole(response.body)
                return
            if response.status != 206:
                raise FetchError(f"{response.status} fetching a byte range")
            content_type = response.headers.get("content-type", "")
            if content_type.lower().startswith("multipart/byteranges"):
                parts = parse_multipart(response.body, content_type)
            else:
                start, end, total = _content_range(response.headers.get("content-range"))
                if len(response.body) != end - start + 1:
                    raise FetchError("a byte range arrived short")
                parts = [(start, total, response.body)]
            for start, total, data in parts:
                if total is not None and total != self.size:
                    raise FetchError(
                        f"the file on the server is {total} bytes, not the {self.size} listed"
                    )
                if start + len(data) > self.size:
                    raise FetchError("a byte range runs past the end of the file")
                self._segments.append((start, data))

    def read_at(self, pos: int, count: int) -> bytes:
        end = min(self.size, pos + count)
        if pos >= end:
            return b""
        data = self._slice(pos, end)
        if data is None:
            self.fetch([(pos, min(self.size, pos + max(count, READAHEAD)) - 1)])
            data = self._slice(pos, end)
            if data is None:
                raise FetchError(f"bytes {pos}-{end - 1} did not arrive")
        return data

    def _slice(self, start: int, end: int) -> Optional[bytes]:
        for seg_start, seg in self._segments:
            if seg_start <= start and end <= seg_start + len(seg):
                return seg[start - seg_start : end - seg_start]
        return None

    def _store_whole(self, body: bytes) -> None:
        if len(body) != self.size:
            raise FetchError(f"the file is {len(body)} bytes, not the {self.size} listed")
        self._segments = [(0, body)]

    def _request(self, byte_range: Optional[str]) -> Response:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.requests += 1
            response = self._transport("GET", self.url, byte_range)
            self.received += len(response.body)
            if response.status in (401, 403, 410) and not self._refreshed:
                # The CDN address carries a token; a lapsed one is asked for again.
                self._refreshed = True
                self.url = self._refresh()
                continue
            if response.status == 429 or response.status >= 500:
                self._sleep(_retry_after(response.headers.get("retry-after"), attempt))
                continue
            return response
        raise FetchError(f"gave up on the CDN after {MAX_ATTEMPTS} attempts")


class RemoteFile:
    """Just enough of a binary file object for zipfile to read a Remote."""

    def __init__(self, remote: Remote) -> None:
        self._remote = remote
        self._pos = 0

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        base = (0, self._pos, self._remote.size)[whence]
        if base + offset < 0:
            raise OSError("seek before the start of the file")
        self._pos = base + offset
        return self._pos

    def read(self, count: Optional[int] = -1) -> bytes:
        if count is None or count < 0:
            count = self._remote.size - self._pos
        data = self._remote.read_at(self._pos, count)
        self._pos += len(data)
        return data

    def close(self) -> None:
        pass


# Where a zip entry goes under ``files/``, or None if it must not be
# written: the Studio's own rule, so the census and an install agree.
safe_path = modzip.safe_path


def _native(path: Path) -> str:
    """A path Windows will open past 260 characters."""
    text = str(path.resolve())
    if os.name == "nt" and not text.startswith("\\\\?\\"):
        return "\\\\?\\" + text
    return text


def _remove(path: Path) -> None:
    if path.exists():
        shutil.rmtree(_native(path))


def _span(info: zipfile.ZipInfo, size: int) -> Tuple[int, int]:
    encoding = "utf-8" if info.flag_bits & 0x800 else "cp437"
    name_length = len(info.orig_filename.encode(encoding, errors="replace"))
    end = (
        info.header_offset + 30 + name_length + len(info.extra) + HEADER_SLACK + info.compress_size
    )
    return info.header_offset, min(size, end) - 1


def _extract(
    archive: zipfile.ZipFile,
    remote: Remote,
    files: Path,
    manifest: Dict[str, Any],
    suffixes: Tuple[str, ...],
) -> None:
    infos = archive.infolist()
    manifest["comment"] = archive.comment.decode("utf-8", "replace") or None
    manifest["entries"] = [
        {
            "name": info.filename,
            "size": info.file_size,
            "packed": info.compress_size,
            "method": info.compress_type,
            "flags": info.flag_bits,
        }
        for info in infos
    ]
    manifest["nested_archives"] = [
        info.filename
        for info in infos
        if not info.is_dir() and info.filename.lower().endswith(ARCHIVE_SUFFIXES)
    ]

    fits = []
    for info in infos:
        if info.is_dir() or not info.filename.lower().endswith(suffixes):
            continue
        if info.file_size > MAX_MEMBER:
            manifest["skipped"].append({"name": info.filename, "reason": "too large"})
        elif info.flag_bits & 0x1:
            manifest["skipped"].append({"name": info.filename, "reason": "encrypted"})
        else:
            fits.append(info)
    remote.fetch([_span(info, remote.size) for info in fits])

    taken: set[str] = set()
    for info in fits:
        target = safe_path(info.filename)
        if target is None:
            manifest["skipped"].append({"name": info.filename, "reason": "not a safe path"})
            continue
        why = "unsafe characters" if target != info.filename.replace("\\", "/") else None
        if target.lower() in taken:
            stem, suffix = os.path.splitext(target)
            n = 2
            while f"{stem}~{n}{suffix}".lower() in taken:
                n += 1
            target, why = f"{stem}~{n}{suffix}", "same name as another entry but for case"
        taken.add(target.lower())
        if why:
            manifest["renamed"].append({"name": info.filename, "path": target, "why": why})
        try:
            data = archive.read(info)
        except Exception as err:  # noqa: BLE001 -- a census records what breaks; it does not stop
            manifest["skipped"].append({"name": info.filename, "reason": f"{type(err).__name__}: {err}"})
            continue
        out = files.joinpath(*PurePosixPath(target).parts)
        os.makedirs(_native(out.parent), exist_ok=True)
        with open(_native(out), "wb") as handle:
            handle.write(data)
        manifest["extracted"].append({"name": info.filename, "path": target, "size": len(data)})


def load_manifest(folder: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def fetch_mod(
    mod: Dict[str, Any],
    dest: Path,
    resolve: Callable[[str], str],
    transport: Transport,
    *,
    full_below: int = FULL_BELOW,
    suffixes: Tuple[str, ...] = TEXT_SUFFIXES,
    refetch: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> Tuple[Dict[str, Any], bool]:
    """Fetch one mod's wanted files into ``dest``: its manifest, and whether it was fetched.

    A manifest already describing the mod's current file comes back as it is.
    Anything else is rebuilt in ``<dest>.partial`` and swapped in at the end, so
    ``dest`` never holds half of one fetch.  A failed fetch is recorded too, and
    tried again on the next run.
    """
    modfile = mod.get("modfile") or None
    previous = load_manifest(dest)
    if (
        not refetch
        and previous is not None
        and previous.get("modfile_id") == (modfile or {}).get("id")
        and previous.get("status") in DONE
    ):
        return previous, False

    manifest: Dict[str, Any] = {
        "mod_id": mod["id"],
        "name_id": mod.get("name_id"),
        "name": mod.get("name"),
        "modfile_id": modfile["id"] if modfile else None,
        "filename": modfile.get("filename") if modfile else None,
        "version": modfile.get("version") if modfile else None,
        "filesize": modfile.get("filesize") if modfile else None,
        "md5": (modfile.get("filehash") or {}).get("md5") if modfile else None,
        "fetched_at": int(time.time()),
        "status": "",
        "error": None,
        "strategy": None,
        "md5_ok": None,
        "requests": 0,
        "bytes_received": 0,
        "comment": None,
        "entries": [],
        "extracted": [],
        "skipped": [],
        "renamed": [],
        "nested_archives": [],
    }
    staging = dest.with_name(dest.name + ".partial")
    _remove(staging)
    os.makedirs(_native(staging), exist_ok=True)

    if modfile is None:
        manifest["status"] = "no-file"
    elif modfile.get("virus_positive"):
        manifest["status"] = "flagged"
    else:
        url = modfile["download"]["binary_url"]
        remote: Optional[Remote] = None
        try:
            size = int(modfile["filesize"])
            remote = Remote(resolve(url), size, transport, lambda: resolve(url), sleep)
            if size <= full_below:
                manifest["strategy"] = "whole"
                body = remote.whole()
                manifest["md5_ok"] = hashlib.md5(body).hexdigest() == manifest["md5"]
                if not manifest["md5_ok"]:
                    raise FetchError("the download does not match mod.io's MD5")
            else:
                manifest["strategy"] = "ranged"
                remote.fetch([(max(0, size - TAIL), size - 1)])
            archive = zipfile.ZipFile(RemoteFile(remote))
            _extract(archive, remote, staging / "files", manifest, suffixes)
            manifest["status"] = "partial" if manifest["skipped"] else "ok"
        except Exception as err:  # noqa: BLE001 -- one broken mod is a finding, not the end of the run
            manifest["status"] = "failed"
            manifest["error"] = f"{type(err).__name__}: {err}"
        finally:
            if remote is not None:
                manifest["requests"] = remote.requests
                manifest["bytes_received"] = remote.received

    temp = staging / (MANIFEST + ".tmp")
    temp.write_text(json.dumps(manifest, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(temp, staging / MANIFEST)
    _remove(dest)
    os.replace(_native(staging), _native(dest))
    return manifest, True


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, help="folder to put census/ in (default: $MODIO_DIR)")
    parser.add_argument("--game", action="append", help="game name_id; repeatable (default: both Annos)")
    parser.add_argument("--mod", type=int, action="append", help="only this mod id; repeatable")
    parser.add_argument("--limit", type=int, help="at most this many mods per game")
    parser.add_argument("--refetch", action="store_true", help="fetch mods already fetched")
    parser.add_argument(
        "--full-below", type=int, default=FULL_BELOW, help="download files this small whole"
    )
    args = parser.parse_args(argv)

    root = (args.root or _data_dir(parser, "--root")) / "census"
    if _inside(REPO, root):
        print(f"refusing: the census folder {root} is inside the repository", file=sys.stderr)
        return 2

    # Mod names come in every script there is, and output that is piped or
    # redirected on Windows cannot print most of them: the run ended at the
    # first such name, and at the same one again on every run after, the mod
    # being stored before its name is printed.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    statuses: Counter[str] = Counter()
    received = listed = 0
    try:
        client = Client(_read_key())
        resolve = resolver(client.wait_turn, http)
        for game in find_games(client, args.game or DEFAULT_GAMES):
            print(f"listing {game['name']} ...")
            mods = list_mods(client, game)
            if args.mod:
                absent = set(args.mod) - {m["id"] for m in mods}
                if absent:
                    print(f"  not listed for this game: {sorted(absent)}")
                mods = [m for m in mods if m["id"] in set(args.mod)]
            if args.limit:
                mods = mods[: args.limit]
            for number, mod in enumerate(mods, 1):
                dest = root / game["name_id"] / str(mod["id"])
                try:
                    manifest, fetched = fetch_mod(
                        mod, dest, resolve, http, full_below=args.full_below, refetch=args.refetch
                    )
                except OSError as err:
                    # Storing the mod failed, not fetching it: a full disk or a
                    # locked folder, which the next mod would meet as well.
                    print(f"error: could not store {mod.get('name')}: {err}", file=sys.stderr)
                    print("run again to carry on from here", file=sys.stderr)
                    return 1
                status = manifest["status"] if fetched else "already"
                statuses[status] += 1
                if fetched:
                    received += manifest["bytes_received"]
                    listed += manifest["filesize"] or 0
                print(
                    f"  [{number}/{len(mods)}] {status:<8}"
                    f" {len(manifest['extracted']):>4} files"
                    f" {manifest['bytes_received']:>12,} of {manifest['filesize'] or 0:>14,} bytes"
                    f"  {manifest['name']}"
                )
                if manifest["error"]:
                    print(f"      {manifest['error']}", file=sys.stderr)
    except ModioError as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nstopped; run again to carry on from here")
        return 130

    print("\n" + ", ".join(f"{status} {n}" for status, n in statuses.most_common()))
    if listed:
        print(f"received {received:,} bytes for {listed:,} bytes of mods ({received / listed:.1%})")
    print(f"census in {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
