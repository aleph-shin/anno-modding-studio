import sys

from .core.reading_process import ARGUMENT
from .self_test import ARGUMENT as SELF_TEST

# The Studio starts itself again for a reading in a process of its own
# (core.reading_process).  Looked for before anything of Qt is imported:
# that process needs none of it, and starts in a third of a second without.
# A built Studio starts here too (tools/release/entry.py), so the exe is
# routed the same way.
if ARGUMENT in sys.argv[1:]:
    from .core.reading_process import main
elif SELF_TEST in sys.argv[1:]:
    from .self_test import main
else:
    from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
