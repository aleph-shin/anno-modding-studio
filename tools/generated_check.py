"""Measure generated copies against the mods they are made of.

    python tools/generated_check.py --census C:/census --game anno-1800
    python tools/generated_check.py --census ... --game anno-117-pax-romana --out C:/gc

Phase 1 of docs/proposals/patches-proposal.md points a library mod's link at
a generated copy of it wherever Play writes something of the Studio's into
it, and section 12 asks, before that is relied on, whether a copy with
nothing written loads exactly as the mod does.  The game cannot tell two
folders apart that hold the same files with the same bytes, so this asks
that, of every mod in a census:

* **A copy with nothing written.**  Built with :func:`core.generated.build`
  and compared with the mod here, file by file and byte by byte, apart from
  the generator's own check: the same files, no file more but the manifest,
  each one the same bytes.  How many were copied and how many linked.
* **iModYourAnno settings.**  Every mod that offers knobs is set away from
  each knob's default where the knob has another value to take, a copy is
  built with what writing that changes, and the copy is read back: it must
  be set to exactly that, and every file it did not write must be the
  author's.

Each copy is deleted once measured, so the disk holds one at a time.  The
copies are made on the census's own volume, where a hard link can be made.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from anno_modding_studio.core import generated, imya, settings  # noqa: E402


def _files(folder: Path) -> dict:
    found = {}
    for top, _dirs, names in os.walk(folder):
        for name in names:
            path = Path(top) / name
            found[path.relative_to(folder).as_posix()] = path
    return found


def _sha1(path: Path) -> str:
    return hashlib.sha1(path.read_bytes(), usedforsecurity=False).hexdigest()


def compare(source: Path, copy: Path, written: dict) -> list:
    """What in ``copy`` is not ``source`` with ``written`` over it, worked out
    here rather than by :func:`core.generated.check`."""
    theirs, ours = _files(source), _files(copy)
    wrong = []
    for rel in sorted(set(theirs) | set(written)):
        if rel not in ours:
            wrong.append("missing " + rel)
        elif rel in written:
            if ours[rel].read_bytes() != written[rel]:
                wrong.append("not as written " + rel)
        elif _sha1(ours[rel]) != _sha1(theirs[rel]):
            wrong.append("different " + rel)
    for rel in sorted(set(ours) - set(theirs) - set(written) - {generated.MANIFEST}):
        wrong.append("extra " + rel)
    return wrong


def other_value(expose):
    """A value for a knob other than its default, or None where it has none."""
    if expose.is_toggle:
        return "false" if settings.is_on(expose.default) else "true"
    if expose.kind == imya.ENUM:
        others = [value for value in expose.values if value != expose.default]
        return others[0] if others else None
    if expose.kind == imya.SLIDER and expose.slider:
        low, high, step = expose.slider
        try:
            at = float(expose.default)
        except ValueError:
            return None
        moved = at + step if at + step <= high else at - step
        return None if moved < low or moved == at else ("%d" % moved if moved.is_integer() else "%g" % moved)
    return None


def measure_mod(source: Path, out: Path, report: dict) -> None:
    into = out / source.name
    started = time.perf_counter()
    made = generated.build(source, into, {})
    report["seconds"] += time.perf_counter() - started
    report["mods"] += 1
    report["copied"] += made.copied
    report["linked"] += made.linked
    wrong = made.wrong or compare(source, into, {})
    if wrong:
        report["plain_wrong"].append({"mod": str(source), "wrong": wrong[:10]})
    shutil.rmtree(into, ignore_errors=True)

    tweaks = imya.read(source)
    if not tweaks:
        return
    report["tweaked_mods"] += 1
    answers = {}
    for expose in tweaks.exposes:
        value = other_value(expose)
        if value is not None:
            answers[expose.name] = value
    if not answers:
        return
    written = imya.changed_files(source, tweaks, answers)
    made = generated.build(source, into, written)
    wrong = made.wrong or compare(source, into, written)
    read = imya.read(into)
    if read is None or not read.is_set_to(answers) or read.unreadable:
        wrong.append("reads back as %s, not %s" % (
            read.chosen() if read is not None else None, answers))
    report["tweaked_set"] += 1
    report["knobs"] += len(answers)
    if wrong:
        report["tweaked_wrong"].append({"mod": str(source), "wrong": wrong[:10]})
    shutil.rmtree(into, ignore_errors=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--census", required=True, type=Path)
    parser.add_argument("--game", required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="where the copies are made, one at a time (default: beside the census)")
    parser.add_argument("--json", type=Path, default=None, help="write the report here too")
    args = parser.parse_args(argv)
    units = sorted(path for path in (args.census / args.game).iterdir() if (path / "files").is_dir())
    out = args.out or args.census.parent / ("gc-%d" % os.getpid())
    out.mkdir(parents=True, exist_ok=True)
    report = {
        "game": args.game, "units": len(units), "mods": 0, "copied": 0, "linked": 0,
        "seconds": 0.0, "plain_wrong": [], "tweaked_mods": 0, "tweaked_set": 0, "knobs": 0,
        "tweaked_wrong": [], "errors": [],
    }
    try:
        for unit in units:
            for source in sorted((unit / "files").iterdir()):
                if not source.is_dir():
                    continue
                try:
                    measure_mod(source, out / unit.name, report)
                except Exception as exc:  # noqa: BLE001 - one mod's fault is a finding, not the end
                    report["errors"].append({"mod": str(source), "error": repr(exc)})
            shutil.rmtree(out / unit.name, ignore_errors=True)
    finally:
        shutil.rmtree(out, ignore_errors=True)
    report["seconds"] = round(report["seconds"], 1)
    print(json.dumps({key: (len(value) if isinstance(value, list) else value)
                      for key, value in report.items()}, indent=2))
    for key in ("plain_wrong", "tweaked_wrong", "errors"):
        for entry in report[key][:20]:
            print(key, json.dumps(entry))
    if args.json:
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 1 if report["plain_wrong"] or report["tweaked_wrong"] or report["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
