"""Keep the suite out of the developer's own files.

The work is done in _isolation.py, which every test module also imports first.
It lives there rather than here because conftest.py is only loaded by pytest,
and the documented test command is unittest -- which, while this file held
the isolation, ran the suite against the real Studio home and cache.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import _isolation

# Where a worker of a parallel run writes down what it ran (tools/check.py).
RECORD = "ANNO_STUDIO_TEST_RECORD"


def pytest_collection_modifyitems(config, items) -> None:
    """Run the tests in another order, where ``ANNO_STUDIO_TEST_ORDER`` names
    one: a number to shuffle by, the same number giving the same order.

    A test that passes only after another has run is not testing what it
    says: one compared a menu with the wording it had months before, and
    passed because an earlier test of its file had left a loadout in the list
    (audit finding A8-36). In the order the files are written nothing shows
    that. Classes are shuffled, and the tests inside each, and a class's
    tests stay together so that what a class sets up once is set up once.
    """
    import os
    import random

    seed = os.environ.get("ANNO_STUDIO_TEST_ORDER", "").strip()
    if not seed or seed == "written":
        return
    if os.path.isfile(seed):
        _replay(seed, config, items)
        return
    shuffled = random.Random(seed)
    groups: dict = {}
    for item in items:
        groups.setdefault((item.module.__name__, getattr(item.cls, "__name__", "")), []).append(item)
    order = list(groups)
    shuffled.shuffle(order)
    items[:] = []
    for key in order:
        shuffled.shuffle(groups[key])
        items.extend(groups[key])


def _replay(record, config, items) -> None:
    """Run what one worker of a parallel run ran, in its order, and nothing
    else: ``record`` is the file :func:`pytest_runtest_logstart` wrote.

    Under ``tools/check.py`` the suite is shared among several processes, each
    running the classes it is handed in the shuffled order, so the tests one
    of them ran before a failure are its own and not the order's. Their record
    is how the failure is had again, in one process."""
    with open(record, encoding="utf-8") as written:
        named = [line.strip() for line in written if line.strip()]
    found = {item.nodeid: item for item in items}
    wanted = set(named)
    config.hook.pytest_deselected(items=[item for item in items if item.nodeid not in wanted])
    items[:] = [found[nodeid] for nodeid in named if nodeid in found]


def pytest_runtest_logstart(nodeid, location) -> None:
    """In a worker of a parallel run, write each test down as it starts, in
    the folder ``ANNO_STUDIO_TEST_RECORD`` names, a file to a worker: what
    ``ANNO_STUDIO_TEST_ORDER=<that file>`` runs again. Written as it goes, so
    a worker that crashes leaves what it had run."""
    import os

    folder, worker = os.environ.get(RECORD, ""), os.environ.get("PYTEST_XDIST_WORKER", "")
    if folder and worker:
        with open(os.path.join(folder, worker + ".txt"), "a", encoding="utf-8") as record:
            record.write(nodeid + "\n")


def pytest_runtest_logreport(report) -> None:
    """And each test that failed there, in a file of the worker's own beside
    it, so that ``tools/check.py`` can say which record to run again."""
    import os

    folder, worker = os.environ.get(RECORD, ""), os.environ.get("PYTEST_XDIST_WORKER", "")
    if folder and worker and report.failed:
        with open(os.path.join(folder, worker + ".failed"), "a", encoding="utf-8") as failed:
            failed.write(report.nodeid + "\n")


def pytest_report_header(config) -> str:
    import os

    seed = os.environ.get("ANNO_STUDIO_TEST_ORDER", "").strip()
    if seed and os.path.isfile(seed):
        return "test order: as one worker ran it, from %s" % seed
    return "test order: shuffled by %s" % seed if seed and seed != "written" else "test order: as written"


def pytest_runtest_teardown(item, nextitem) -> None:
    """Empty the suite's settings after every test, whether or not the test
    asks: a window remembers into them as it goes, and the next test's window
    starts out knowing what the last one's did. Most tests register
    ``_isolation.forget_settings`` for themselves; one that did not left the
    splitter where its window had it, and a test of the changes list's width
    passed or failed by what had run before it.

    And its loadouts folder, for the same reason: a window saves into it
    by name, and a test found the loadout another had left under the name
    it was about to use."""
    _isolation.forget_settings()
    _isolation.forget_loadouts()
    # And how the one application all the tests share is styled.
    _isolation.forget_styling()
    _isolation.forget_keys()
