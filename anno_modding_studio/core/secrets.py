"""Where the Studio keeps a secret, and how.

The mod.io API key is the first thing the Studio has ever had to hold that must
not be readable.  Nothing it already keeps qualifies: the settings file is plain
text and gets pasted into bug reports, a loadout lives in Documents and gets
shared, an export goes to other people by design.

So the key goes through Windows' own Data Protection API.  ``CryptProtectData``
wraps a blob so that only the same Windows user on the same machine can unwrap
it, and an entropy string of the Studio's own means another DPAPI-using program
running as that user cannot either, unless it knows the string.  That is the
same footing a browser's cookie store stands on: safe from other accounts, from
a copied file, from a backup, from a synced folder -- and not from software
running as you.  The user guide says so in as many words rather than implying more.

The file sits next to the index cache under ``%LOCALAPPDATA%``, which is
machine-local, rather than under Documents, which is not.  ``ANNO_STUDIO_CACHE``
moving the cache moves this too, so the test suite's isolation covers it without
anything more to remember.  ``ANNO_STUDIO_SECRETS`` names a file outright.
Kept in one folder with everything else (:func:`core.places.one_folder`), the
file is as safe as it was: carried to another machine or another account, it
cannot be unwrapped there, and the key is asked for again.

The sign-in is a token from mod.io's email-code flow, and it runs out: a year
from the day it was given, at the ``date_expires`` mod.io sends with it, kept
here as ``token_expires``.  mod.io offers no refresh for this flow -- its
refresh tokens belong to the OAuth login a website does with a client secret
-- so a token past its day is simply no sign-in, and is not sent.  It was
kept and sent regardless, and only mod.io's 401 said anything.
"""

from __future__ import annotations

import contextlib
import ctypes
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from . import disk, places

ENV_FILE = "ANNO_STUDIO_SECRETS"
FILE_NAME = "credentials.bin"
# Mixed into every wrap.  Not a secret -- it is in the source -- but it means a
# blob of ours cannot be unwrapped by a program that merely runs as the user.
ENTROPY = b"AnnoModdingStudio mod.io credentials v1"
DESCRIPTION = "Anno Modding Studio mod.io credentials"

Wrap = Callable[[bytes], bytes]
# How long before a sign-in runs out the Studio starts saying so: time to
# sign in again before a publish is refused for it.
WARN_BEFORE = 14 * 24 * 3600


@dataclass
class Credentials:
    """What the Studio knows about the user's mod.io account."""

    api_key: str = ""
    token: str = ""
    token_expires: int = 0
    email: str = ""

    def __bool__(self) -> bool:
        return bool(self.api_key or self.token)

    def expired(self, now: Optional[float] = None) -> bool:
        """Whether a token is held that is past the day mod.io gave for it."""
        return bool(self.token and self.token_expires) and self.token_expires <= _now(now)

    def signed_in(self, now: Optional[float] = None) -> bool:
        """Whether the token may be sent: held and not past its day.  A token
        whose day is not known is good until mod.io says otherwise."""
        return bool(self.token) and not self.expired(now)

    def runs_out_soon(self, now: Optional[float] = None) -> bool:
        """Whether a good sign-in runs out within :data:`WARN_BEFORE`."""
        return self.signed_in(now) and bool(self.token_expires) and (
            self.token_expires - _now(now) <= WARN_BEFORE)

    def live_token(self, now: Optional[float] = None) -> str:
        """The token to send, or "" when there is no good one."""
        return self.token if self.signed_in(now) else ""


def _now(now: Optional[float]) -> float:
    return time.time() if now is None else now


# -- DPAPI --------------------------------------------------------------------
if sys.platform == "win32":
    import ctypes.wintypes as _wt

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", _wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> _Blob:
        buffer = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    def _take(out: _Blob) -> bytes:
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    def protect(data: bytes, entropy: bytes = ENTROPY) -> bytes:
        """Wrap ``data`` so only this Windows user on this machine can read it."""
        out = _Blob()
        ok = ctypes.windll.crypt32.CryptProtectData(
            ctypes.byref(_blob(data)), DESCRIPTION, ctypes.byref(_blob(entropy)),
            None, None, 0, ctypes.byref(out),
        )
        if not ok:
            raise ctypes.WinError()
        return _take(out)

    def unprotect(data: bytes, entropy: bytes = ENTROPY) -> bytes:
        """The bytes :func:`protect` was given, or ``OSError`` if not ours to read."""
        out = _Blob()
        ok = ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(_blob(data)), None, ctypes.byref(_blob(entropy)),
            None, None, 0, ctypes.byref(out),
        )
        if not ok:
            raise ctypes.WinError()
        return _take(out)

else:  # pragma: no cover - the Studio is a Windows app; this keeps imports working

    def protect(data: bytes, entropy: bytes = ENTROPY) -> bytes:
        raise OSError("credential protection needs Windows")

    def unprotect(data: bytes, entropy: bytes = ENTROPY) -> bytes:
        raise OSError("credential protection needs Windows")


# -- the file -------------------------------------------------------------------
def default_path() -> Path:
    """Where the credentials file lives."""
    named = os.environ.get(ENV_FILE, "").strip()
    if named:
        return Path(named)
    cache = os.environ.get("ANNO_STUDIO_CACHE", "").strip()
    if cache:
        return Path(cache) / "modio" / FILE_NAME
    return places.local_home() / "modio" / FILE_NAME


class Store:
    """The credentials file: read it, write it, forget it.

    ``wrap`` and ``unwrap`` are DPAPI unless a test hands in something else;
    the file format is the wrapped bytes and nothing more.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        wrap: Wrap = protect,
        unwrap: Wrap = unprotect,
    ) -> None:
        self.path = Path(path) if path is not None else default_path()
        self._wrap = wrap
        self._unwrap = unwrap

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> Credentials:
        """What is saved, or nothing: a missing, foreign or damaged file is
        the same as no file, since nothing can be done with it either way."""
        try:
            wrapped = self.path.read_bytes()
        except OSError:
            return Credentials()
        try:
            raw = json.loads(self._unwrap(wrapped).decode("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return Credentials()
        if not isinstance(raw, dict):
            return Credentials()
        return Credentials(
            api_key=str(raw.get("api_key") or ""),
            token=str(raw.get("token") or ""),
            token_expires=int(raw.get("token_expires") or 0),
            email=str(raw.get("email") or ""),
        )

    def save(self, credentials: Credentials) -> None:
        """Write the lot, whole, and only once it is all on disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        disk.write_bytes(self.path, self._wrap(json.dumps(asdict(credentials)).encode("utf-8")))

    def drop_token(self) -> Credentials:
        """Take the sign-in off this PC and keep the rest: what is left.  For a
        sign-out, and for a token mod.io has said it no longer takes."""
        credentials = self.load()
        credentials.token = ""
        credentials.token_expires = 0
        self.save(credentials)
        return credentials

    def forget(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
