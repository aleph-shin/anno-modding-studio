"""Find what the documents rule out and no linter looks for.

    python tools/standards_check.py            every finding
    python tools/standards_check.py <rule>     one rule's findings

``docs/architecture.md``, ``docs/standards/translation.md`` and ``docs/development.md``
each state rules a change can break without ruff, mypy or the design check
noticing. Each was true of the code when this was written, and was held by
whoever remembered it. The suite holds some of them, but the pre-push hook
runs the quick half of ``tools/check.py`` and not the suite, so a push could
still carry a catalog out of step with the code.

Every rule is hard: one finding fails, and it is printed with its rule, the
file and the line. ``tools/check.py`` runs them all.

| Rule | What it finds | Where the rule is written |
|---|---|---|
| ``qt-in-core`` | a module under ``core/`` importing Qt or ``ui/`` | architecture.md, section 1 |
| ``leaf`` | a leaf of ``core/`` importing more of core than ``LEAVES`` allows | architecture.md, section 1 |
| ``private-name`` | a ``_name`` of one package module used by another, or by a tool | architecture.md, section 1 |
| ``isolation`` | a test module that does not import ``_isolation`` before the package | development.md |
| ``waiver`` | a ``noqa: BLE001`` with no reason after it | pyproject.toml |
| ``requirements`` | ``requirements.txt`` and pyproject's dependencies saying different things | development.md |
| ``link`` | a relative link in a document to a file that is not there | |
| ``catalog`` | a catalog out of step with the code, or a marked sentence breaking a rule | translation.md |
| ``bare-text`` | English handed to Qt without ``tr()`` | translation.md |
| ``untranslated`` | a catalog entry with no translation, or one to look at again | translation.md |
| ``user-path`` | a path into a real user's folder, ``C:\\Users\\<name>``, in any tracked text file | development.md |

The tests may reach a module's privates to pin them, so ``private-name``
covers the package and ``tools/`` only. A tool importing another tool's
private names is the tools' own business.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "anno_modding_studio"

RULES = (
    "qt-in-core", "leaf", "private-name", "isolation", "waiver",
    "requirements", "link", "catalog", "bare-text", "untranslated", "user-path",
)

QT = {"PySide6", "PySide2", "PyQt5", "PyQt6", "shiboken6"}
# The core modules anything may import without joining a cycle, and the only
# core modules each may import itself. docs/architecture.md, "The leaves stay
# leaves", says the same in words: change both together.
LEAVES = {
    "disk": set(), "paths": set(), "places": set(), "po": set(),
    "schema": set(), "settings": set(),
    "i18n": {"po"},
    "refs": {"i18n"}, "rda": {"i18n"}, "modio": {"i18n"},
    "sidefiles": {"i18n", "modop"}, "versioning": {"i18n"},
    "modfolder": {"disk", "settings", "versioning"},
    "records": {"disk", "i18n"},
    "games": {"i18n", "places"},
    "modop": {"games"},
    "applying": {"games", "modop"},
}

_BLIND_WAIVER = re.compile(r"#\s*noqa:[^#]*\bBLE001\b(?P<rest>[^#]*)")
_REASON = re.compile(r"\s-+\s*\S")
_FENCE = re.compile(r"^(```|~~~).*?^\1", re.S | re.M)
_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")
_DEPENDENCIES = re.compile(r"^dependencies\s*=\s*\[(.*?)\]", re.S | re.M)
# A folder under Users, in any spelling a source file gives it. The names a
# test or a document may use for nobody in particular are the placeholders.
_USER_PATH = re.compile(r"\b[A-Za-z]:[\\/]+Users[\\/]+([^\\/\s\"'`<>|*?:;,()\[\]{}]+)", re.I)
PLACEHOLDERS = {"public", "default", "me", "you", "x", "alice", "bob", "someone", "somebody"}
TEXT = {".py", ".md", ".txt", ".json", ".po", ".toml", ".in", ".bat", ".yml", ".yaml",
        ".js", ".ps1", ".ini", ".cfg", ".html", ".css", ".xml", ""}


def _modules(layer: str) -> dict:
    return {
        path.stem: path for path in sorted((PACKAGE / layer).glob("*.py"))
        if path.stem != "__init__"
    }


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def _from_package(node: ast.ImportFrom) -> bool:
    return bool(node.level) or (node.module or "").split(".")[0] == PACKAGE.name


# -- the layers -------------------------------------------------------------
def qt_in_core(name: str, source: str) -> list:
    """Qt, or anything of ``ui/``, imported by a module under ``core/``."""
    found = []
    for node in _imports(ast.parse(source)):
        if isinstance(node, ast.Import):
            taken = [alias.name for alias in node.names]
        elif node.level:
            # One dot is core itself; two reach the package, where ui/ is.
            taken = [] if node.level == 1 else [
                "%s.%s" % (PACKAGE.name, node.module or alias.name)
                for alias in node.names
            ]
        else:
            taken = ["%s.%s" % (node.module, alias.name) for alias in node.names]
        for module in taken:
            parts = module.split(".")
            if parts[0] in QT:
                found.append(("qt-in-core", name, node.lineno, "imports " + parts[0]))
            elif parts[:2] == [PACKAGE.name, "ui"]:
                found.append(("qt-in-core", name, node.lineno, "imports ui/"))
    return found


def core_imports(source: str, names) -> set:
    """The core modules ``source``, a core module, imports -- inside functions too."""
    edges = set()
    for node in _imports(ast.parse(source)):
        if not isinstance(node, ast.ImportFrom) or node.level != 1:
            continue
        if node.module is None:
            edges.update(alias.name for alias in node.names if alias.name in names)
        elif node.module.split(".")[0] in names:
            edges.add(node.module.split(".")[0])
    return edges


def leaves(sources: dict, allowed: dict | None = None) -> list:
    """Leaves of ``core/`` that import more of core than they are allowed."""
    allowed = LEAVES if allowed is None else allowed
    found = []
    for stem, may in sorted(allowed.items()):
        if stem not in sources:
            continue
        more = core_imports(sources[stem], set(sources)) - may - {stem}
        if more:
            found.append((
                "leaf", "anno_modding_studio/core/%s.py" % stem, 1,
                "imports %s, and a leaf is what everything else may import"
                % ", ".join(sorted(more)),
            ))
    return found


def private_names(name: str, source: str, modules) -> list:
    """A ``_name`` of a package module, imported or reached through the module."""
    tree = ast.parse(source)
    found = []
    aliases = {}
    for node in _imports(tree):
        if not isinstance(node, ast.ImportFrom) or not _from_package(node):
            continue
        for alias in node.names:
            if alias.name.startswith("_") and not alias.name.startswith("__"):
                found.append((
                    "private-name", name, node.lineno,
                    "imports %s from %s" % (alias.name, node.module or "."),
                ))
            elif alias.name in modules:
                aliases[alias.asname or alias.name] = alias.name
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in aliases
                and node.attr.startswith("_") and not node.attr.startswith("__")):
            found.append((
                "private-name", name, node.lineno,
                "uses %s.%s" % (aliases[node.value.id], node.attr),
            ))
    return found


# -- the tests ----------------------------------------------------------------
def isolation(name: str, source: str) -> list:
    """A test module that imports the package before ``_isolation``, or never
    imports ``_isolation`` at all."""
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            taken = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            taken = [node.module or ""]
        else:
            continue
        if "_isolation" in taken:
            return []
        if any(module.split(".")[0] == PACKAGE.name for module in taken):
            return [("isolation", name, node.lineno,
                     "imports the package before _isolation")]
    return [("isolation", name, 1, "does not import _isolation")]


# -- waivers ------------------------------------------------------------------
def waivers(name: str, source: str) -> list:
    """A blind except waived with no reason: ``# noqa: BLE001 - <reason>``."""
    found = []
    for number, line in enumerate(source.splitlines(), 1):
        match = _BLIND_WAIVER.search(line)
        if match and not _REASON.match(match.group("rest")):
            found.append(("waiver", name, number,
                          "noqa: BLE001 with no reason after it"))
    return found


# -- what is written twice -------------------------------------------------------
def requirements(pyproject: str, requirements_txt: str) -> list:
    """``requirements.txt`` and pyproject's dependencies, where they differ."""
    match = _DEPENDENCIES.search(pyproject)
    declared = sorted(re.findall(r"[\"']([^\"']+)[\"']", match.group(1))) if match else []
    listed = sorted(
        line.strip() for line in requirements_txt.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if declared == listed:
        return []
    return [("requirements", "requirements.txt", 1,
             "lists %s and pyproject.toml %s"
             % (", ".join(listed) or "nothing", ", ".join(declared) or "nothing"))]


def links(name: str, text: str, folder: Path) -> list:
    """Relative links in a document that lead to no file."""
    found = []
    # Blanked, not cut, so a line number still counts from the top.
    text = _FENCE.sub(lambda match: "\n" * match.group().count("\n"), text)
    for match in _LINK.finditer(text):
        target = match.group(1)
        if re.match(r"[a-z][a-z0-9+.-]*:", target) or target.startswith("#"):
            continue
        if not (folder / target.split("#")[0]).exists():
            found.append(("link", name, text.count("\n", 0, match.start()) + 1,
                          "links to %s, which is not there" % target))
    return found


def user_paths(name: str, text: str) -> list:
    """Paths into a real user's folder: a name there that is not a placeholder
    is most likely the name of whoever wrote it."""
    found = []
    for match in _USER_PATH.finditer(text):
        if match.group(1).lower() not in PLACEHOLDERS:
            found.append(("user-path", name, text.count("\n", 0, match.start()) + 1,
                          "names the user folder %r: use C:\\Users\\Public or a placeholder"
                          % match.group(1)))
    return found


def _tracked() -> list:
    """The text files git tracks, or, outside a clone, every text file below
    the root but the ones .gitignore keeps out."""
    import subprocess

    try:
        names = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True,
                               check=True).stdout.decode("utf-8").split("\0")
    except (OSError, subprocess.CalledProcessError):
        skip = {"__pycache__", "build", "dist", "sample", "extracts", "AnnoModdingStudioData"}
        names = [_relative(path) for path in ROOT.rglob("*") if path.is_file()
                 and not any(part.startswith(".") or part in skip
                             for part in path.relative_to(ROOT).parts)]
    return [ROOT / name for name in sorted(names)
            if name and Path(name).suffix.lower() in TEXT and (ROOT / name).is_file()]


# -- the catalogs -----------------------------------------------------------------
def catalogs() -> list:
    """What ``tools/translations.py`` finds: catalogs out of step, English
    handed to Qt without ``tr()``, and entries nobody has translated."""
    import translations

    found = [("catalog", "anno_modding_studio/locale", 1, said)
             for said in translations.check()]
    found += [("bare-text", problem.path, problem.line, problem.text)
              for problem in translations.lint()]
    for path in translations.catalogs():
        done, fuzzy, total = translations.progress(path)
        if done < total or fuzzy:
            found.append((
                "untranslated", _relative(path), 1,
                "%d to translate, %d to look at again: python tools/translations.py todo %s"
                % (total - done, fuzzy, path.stem),
            ))
    return found


# -- the repository ----------------------------------------------------------------
def _documents() -> list:
    return [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]


def findings() -> list:
    """Every finding in the repository, as ``(rule, file, line, text)``."""
    found = []
    core, ui = _modules("core"), _modules("ui")
    sources = {stem: path.read_text(encoding="utf-8") for stem, path in core.items()}
    for stem, source in sources.items():
        found += qt_in_core(_relative(core[stem]), source)
    found += leaves(sources)
    modules = set(core) | set(ui)
    for path in sorted([*PACKAGE.rglob("*.py"), *(ROOT / "tools").glob("*.py")]):
        source = path.read_text(encoding="utf-8")
        found += private_names(_relative(path), source, modules)
        found += waivers(_relative(path), source)
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        found += isolation(_relative(path), path.read_text(encoding="utf-8"))
    found += requirements(
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        (ROOT / "requirements.txt").read_text(encoding="utf-8"),
    )
    for path in _documents():
        if path.is_file():
            found += links(_relative(path), path.read_text(encoding="utf-8"), path.parent)
    for path in _tracked():
        found += user_paths(_relative(path), path.read_text(encoding="utf-8", errors="replace"))
    return found + catalogs()


def failures() -> list:
    """Every finding of every rule, as lines to print. None is allowed."""
    return ["  %s: %s:%d: %s" % found for found in findings()]


def main(argv: list | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] not in RULES:
        print(__doc__)
        return 2
    found = [item for item in findings() if not argv or item[0] == argv[0]]
    print("\n".join("%s: %s:%d: %s" % item for item in found) or "nothing found")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
