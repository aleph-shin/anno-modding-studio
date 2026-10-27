"""What the built Studio's exe runs: the package's own ``__main__``.

The freezing tool runs one script as ``__main__``, outside any package, so
``anno_modding_studio/__main__.py`` and its relative imports cannot be that
script themselves.  This hands over to it, so the exe is routed as
``python -m anno_modding_studio`` is: ``--reading-process`` to the reading's
process before anything of Qt is imported, ``--self-test`` to the
self-test, anything else to the window.

Nothing in the package uses ``multiprocessing``, which is what would need
``multiprocessing.freeze_support()`` here: the reading's process is the exe
started again with its argument (``core/reading_process.py``).
"""

from __future__ import annotations

from anno_modding_studio.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())
