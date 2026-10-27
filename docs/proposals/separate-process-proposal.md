# The reading after an edit in a process of its own: a proposal

The reading that follows an edit runs in a process of its own, so that it
stops taking turns with the window. Issue 205 asked for this and for a
proposal first. This is it.

Written on 13 October 2026, against `main` at `ed87a5f`. Python 3.13.0,
PySide6 6.11.2, lxml 6.1.3.

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

**Status (13 October 2026).** Built, both phases. Phase 1: a reading hands
back a `stale.Answer`, and the sweep for the mods' searches says where it
is. Phase 2: each reading runs in a process of its own
(`core/reading_process.py`), ended to stop it, with the search answers
handed over and back, and the reading done on the thread where the process
fails. On the bench project a step taken while the reading runs takes what
it takes alone, where on the thread it took up to 2.4 times as long; the
figures are in the design notes, under *The reading after an edit is done
in a process of its own*. Every unit of the census comes to the same
answer in a process as on the thread, both games. Where it differs from what is written
here: the worker keeps the search answers on the index, and not the
controller; the process is sent the names the loaded mods give things,
which section 6 left to be found out; the window stops a reading before it
closes, rebuilds or writes to the index, and starts none until it has
(`ReadingsController.hold`), which settles open question 4 and covers a
rebuild the proposal did not think of, since a second process holding the
file stops it being replaced; and the plain edit is at 1.4 seconds in the
bench and not the 0.7 expected, alone as well, for a reason that is not
the reading. Not done: anything in a frozen build (section 11), and open
question 1.

## 1. The problem

An edit starts a reading of the mods it leaves in doubt: the later ops of
the mod edited, and the mods that load after it (`core/stale.py`,
`ui/readings_controller.py`, `RereadWorker` in `ui/workers.py`). The reading
runs on a `QThread`. It is Python, so it shares the interpreter with the
window, and the interpreter is handed from one thread to the other every
5 ms whichever the system would rather run. Two threads that both want it
get half each, and a thread's priority does not change that.

On the bench project (417 mods, 32,359 changes; `tools/bench.py`, 12 October
2026, in the design notes under *On a list of 417 mods an edit holds the
window for a second*):

- **Everything done while the reading runs takes about twice as long.** An
  edit held the window for 2.1 s alone and for 3.2 to 3.9 s with the
  reading running. Opening an asset, showing a tab and the filter go the
  same way. The edit alone is 0.7 s since (#204); what it takes beside the
  reading has not been timed again, and the sharing is as it was.
- **The reading runs for 43 to 142 s after one edit,** and Play and Export
  wait for it.

## 2. What was measured for this

The issue lists what a design has to settle. Four of those are questions of
how much, so they were measured first: the reading run from a script
against the bench project and the bench's copy of the index, with no window
and nothing else running. One run each unless it says otherwise.

Two edits stand for the two ends. *The last mod edited* is what the bench
does: one mod is asked for, and the stack is read as far as it, which is
all of it. *The first mod edited* is the most that can be asked: 404 mods.

| | |
|---|---|
| The snapshot (`stale.snapshot`) | 835 KB: 417 layers and one mod's text of 8 KB. Taken in 10 ms; pickled, and read back, in under 10 ms each |
| A reading, the index new | 67 to 88 s (four runs) |
| A reading, the same index again | 39 and 40 s (two runs) |
| A reading, a new index handed the first one's search answers | 39 s |
| Those search answers (`AssetIndex._matched`) | 547 expressions, 5,920 GUIDs; 70 KB pickled |
| The reports as they are | Cannot be pickled: each holds the reader's results, and those hold lxml elements |
| The answer for 404 mods, without the reader's working | 16,625 ops, 32,354 changes, 1,297 unread ops: 37 MB. Pickled in 0.13 s, read back in 0.12 s |
| Taking that answer into the project (`stale.take`) | 0.17 s |
| The longest the reading goes without saying where it is | 29 s with the index new; 3 s otherwise. It says so 1,135 times in all |
| Memory | The script stood at 166 MB with the project loaded, reached 1,167 MB during the reading, and stood at 393 MB after it |
| Starting Python and importing what the reading needs | 0.30 s (three runs). 231 modules, none of them PySide6 |

What that changes in the issue's list:

- **What crosses to the process is small.** The issue expected the snapshot
  to hold the 32,359 changes. It holds the layers and the text of each mod
  the project writes, and nothing of what the mods came to.
- **What comes back is the large part, and still cheap:** a quarter of a
  second for the most there can be, beside 0.17 s to take it and the redraw
  that follows.
- **The reports cannot cross as they are.** `stale.take` and
  `modfile.take_reading` ask a report for three things only: its ops, its
  changes and its unread ops. Those are plain data and pickle as they are.
- **Starting a process costs less than the wait before a reading starts**
  (`EDIT_MS`, 500 ms). So the choice between a process for each reading
  and one kept running is not about the start.
- **What a kept process would keep is 70 KB.** A second reading is quicker
  by 28 s because the index keeps what each search expression matched, the
  game's file being fixed under it. A new index handed those answers is as
  quick as the old one.
- **A reading cannot be stopped during its search sweep.** The 29 s is the
  sweep of every asset for the search expressions not answered yet. It
  reports where it is to the first reading of a folder (`ModsWorker`), and
  not to this one: `reader.analyse_stack` passes no `searching` on. So the
  window can wait most of 30 s at closing (`ReadingsController.stop`).
- **The reading takes about a gigabyte at its peak,** in the window's
  process today, and some 230 MB of it was still held when it ended.

## 3. What is proposed

1. **Each reading runs in a process of its own,** started for it and gone
   when it ends. The window sends the snapshot and gets the answer.
2. **A reading is stopped by ending its process.** At once, wherever it has
   got to, with nothing to ask of it and nothing to wait for.
3. **The search answers go with the request and come back as soon as the
   sweep is over,** before the reading proper. The window's index keeps
   them, as it does today, and hands them to the next reading.
4. **The answer is the ops, the changes and the unread ops of each mod**
   and no more, on the thread as well as from the process.
5. **The process runs below normal priority.** Between two processes a
   priority means what it says, and the window gets a core first.
6. **The reading on the thread stays,** for the tests and for a process
   that cannot be started.

Not proposed: reading less, a quicker reader, any other background work
moved (section 9), and anything that changes what a reading comes to.

## 4. Why a process for each reading

**One kept running** pays for the interpreter once and keeps its index
warm. Measured, the first is 0.3 s against a reading of 39 s at best, and
the second is 70 KB that can be sent. What it costs:

- **A way to stop it that it has to answer.** A flag, read at each step the
  reading reports, as today: up to 3 s, and 29 s in a sweep. Ending the
  process instead throws the warm index away, which is the reason it was
  kept.
- **Whatever it holds, held.** About 230 MB after one reading here, for as
  long as the Studio is open.
- **A state to keep in step.** The index it opened, the language, the
  settings: each can change in the window while it runs.

**One for each reading** has none of those. Its memory goes back to the
system with it, peak and all. It starts from what it is sent, so nothing
it holds can be out of date. And stopping it is the system's work and not
the reader's.

What it must not do is lose the sweep. Edits come in runs, and each edit
stops the reading the one before it started. A reading ended 10 s into a
sweep of 28 has found nothing, and the next begins the sweep again; somebody
editing every 20 s would never see a reading finish. So:

- **A reading that is not wanted any more is ended once it has said what
  its searches found.** If it has said so already, or was sent every answer
  it needs, that is at once. Otherwise the window waits for that one
  message, takes the answers, and ends it. This is what happens today,
  less the wait for the next step after the sweep.
- **Closing the window ends it whatever it is doing.**

## 5. How it is built

**The process's side, in `core/`.** A module with a `main` of its own
(`core/reading_process.py`, started by an argument to the Studio's own
entry point that is looked at before Qt is imported: section 11). It reads one request
from its standard input, opens the index, runs `stale.read_again`, and
writes messages to its standard output. `core/` imports nothing of Qt
(`pyproject.toml` says so, and the 231 modules loaded here bear it out),
which is why it starts in 0.3 s. It is started with `sys.executable`, so under `pythonw`
it is `pythonw` and has no console, and with the flag that gives it no
window where the Studio was started from a console's `python`.

**The request,** pickled: the snapshot; the index's file and the game file
it was built from (`AssetIndex.db_path`, `source_path`); the order the
loader logged (`loaderlog.logged_order`, read in the window as now); the
search answers the window's index holds; and the language the window is
in. The environment is inherited, so the `ANNO_STUDIO_*` settings the tests
and the bench isolate themselves with reach it unasked.

**The messages back,** each one pickle on the stream:

1. *The search answers,* once the sweep is over: the expressions it had to
   look for and what each matched. Sent even where there were none to look
   for, since it is also what says the reading can be ended at once.
2. *The answer:* for each mod asked for, its ops, changes and unread ops,
   and for a mod read from the project's own text, that text and the number
   the reading gave each of its ops (`modfile.Reread`). Or what went wrong,
   as the sentence the rows show today, with the traceback beside it.

**Pickle** and not JSON: both ends are the Studio's own code at the same
commit, started by the window, and `Op`, `Change` and `Unread` pickle as
they stand. Their `to_dict` is what a project file holds, which is not all
a reading knows of them.

**The window's side stays a `RereadWorker`.** It is a `QThread` with the
same `taken`, `done`, `cancel` and `wait`, so little of
`ReadingsController` changes: the answers a reading found are kept on the
index when they arrive, and a reading that fell back is said. In `run` the worker starts the process, writes the
request, and waits on the pipe, which does not hold the interpreter. Read
back on that thread, the largest answer holds it for 0.12 s, once. `cancel`
ends the process, at once or after the first message (section 4). A pipe
that closes with no answer is a reading that was stopped, where the worker
asked for that, and a reading that failed where it did not.

Not a `QProcess`: its output arrives in pieces on the window's own thread,
to be put together and unpickled there. Not `multiprocessing`: on Windows
its child imports the parent's main module, which is `app.py` and all of
PySide6 with it.

**The answer without the working** (`stale.Answer`, say: `ops`, `changes`,
`unread`). `stale.read_again` returns these in place of the reader's
reports, on the thread and in the process alike, so `stale.take` and
`modfile.take_reading` are handed one thing whichever read it. Today the
reports keep every tree the reader built until the answer is taken or
thrown away. Nothing else calls `read_again` but two tests
(`tests/test_stale.py`, `tests/test_ui.py`).

**Not outliving the window.** The process's main thread reads; a second
thread waits on standard input, which nothing is written to after the
request. When the window goes, however it goes, that pipe closes, and the
process ends itself. Nothing it holds needs putting away: it writes no
file, and it only reads the index.

**When it cannot be started, or dies.** The reading is done on the thread,
as today, and the Studio says once in the status bar that it is. A second
failure in a row is not tried a third time in that session.

**Switching it.** `ANNO_STUDIO_REREAD` keeps `none` and gains `thread`.
Unset is the process.

## 6. Things to get right

- **The index is written to while it is read.** The window puts the game's
  texts into it at launch and when the language changes (`load_texts`,
  `clear_texts`), and the schema's tables when the templates are imported.
  Today the reading shares the window's connection, and nothing is locked
  against anything. A second process reading through a sweep holds the file
  shared for as long, and a write from the window then waits and, after
  five seconds, fails. To settle in phase 2: the process opens the index
  read-only, and either the window ends a reading before it writes, or the
  index is opened so that readers and the writer do not wait on each other.
  The writes are few and rare.
- **What the window's index knows that a new one does not.** The names the
  loaded mods give things are set on the window's index
  (`set_mod_names`), in memory and in a table of that connection alone.
  `keptops` asks the index for a display name. Whether anything a reading
  comes to depends on them is to be found, and if so they are sent.
- **Words.** A reason the reader writes may be translated where it is
  written (`tr`) and not where it is shown (`tr_noop`, `STORED`). The
  process is sent the window's language for that.
- **The same answer.** Phase 2 is done when the process and the thread give
  the same answer for every unit of the census, both games.

## 7. Choices weighed and not taken

- **The reading waits while the window works.** The thread stops at its
  next step whenever the window's event loop is busy, and goes on when it
  is idle. No process and little code. It is only as good as the gaps
  between the reader's steps, which are up to 3 s, so an edit would still
  share with it for most of the time it takes unless the reader were made
  to ask far more often; the reading takes longer by all the time the window
  works; and it never has a core to itself.
- **A longer or shorter turn** (`sys.setswitchinterval`). Changes how often
  the two change places and not the share each gets.
- **A Python built without the lock.** The Studio runs on whatever Python
  3.10 or later is installed (`run.bat`), and lxml and PySide6 would each
  have to be built for it. A frozen build changes the first of those and
  not the second (section 11).
- **One process kept running.** Section 4.
- **A pool of processes, a mod each.** A stack is read in order, each mod
  against what the ones before it left. It does not divide.

## 8. The tests

- **The suite keeps the reading off** (`ANNO_STUDIO_REREAD=none` in
  `tests/_isolation.py`).
- **The tests that are about the reading run it on the thread.**
  `StaleReadingsCase.reading` sets the variable to nothing today and calls
  the worker's `run` in place of `start`; it sets `thread`. They test what
  is marked, taken and shown, and none of that changes.
- **New tests for the process itself,** in a file of their own and each
  with a hard time limit: the answer for a small stack equals the thread's;
  the search answers come first and are kept on the window's index; a
  reading cancelled before them is ended after them, and one cancelled
  after them at once; closing the window ends the process; closing the
  pipe ends it with no window; a process that fails to start, and one that
  dies half way, are each read on the thread.
- **The bench.** `--alone` stays, for the other work a step can share with.
  A run with it and one without should now agree.

## 9. What this leaves alone

- **How long the reading takes.** Every mod as far as the last one asked
  for, each from what the project writes for it. 39 s here at best.
  Reading less is a change of its own.
- **Play and Export wait for the reading,** as designed. The wait should be
  shorter: the 43 to 142 s was a reading sharing with the bench's steps.
- **The first filter keystroke after an edit,** which is slow with nothing
  else running.
- **The other workers.** The first reading of a folder (`ModsWorker`) is
  waited for behind a dialog. The scan of the other mods on this PC is
  seconds. The loader checks are processes already. A mod read again at
  other settings (`stale.read_at`) is the window's own and made at once.
  Each could be sent the same way later; none is the trouble.
- **What a reading comes to,** and what is exported.

## 10. Phases

1. **The answer, and a sweep that can be stopped.** `stale.Answer` in
   place of the reports; `analyse_stack` passes `searching` on, and the
   worker stops there when the window is closing. Not when the reading is
   stopped for a newer one: that one finishes its sweep, for the reason of
   section 4, and stops at the step after it as it does today. Still on
   the thread. By itself this frees the reader's working when a reading
   ends, and takes the 29 s out of closing the window. The tests of the
   reading pass as they are.
2. **The process.** `core/reading_process.py`; `RereadWorker` starting it,
   sending, waiting and ending it; the search answers both ways; the
   priority; `thread` and the fall back to it; the index opened read-only
   and the window's writes to it settled (section 6). The tests of section 8. Then
   the census for the same answer, and the bench with and without
   `--alone`.

Expected of the bench after phase 2: a step taken with the reading running
within the run-to-run difference of the same step alone, so an edit at
about 0.7 s both ways; and the reading at about 40 s where it had the
answers and 70 where it had not, whatever is done beside it.

Neither phase changes what is read or written. Phase 2 wants the census
all the same, because the reading is done somewhere new.

## 11. A frozen build

The Studio is to be released as an executable, and is not one yet. Nothing
here was measured in one: no freezing tool is installed, and which is used
is not decided. What follows is by reading, and PyInstaller is taken as
the example.

**The problem is the same.** A frozen Studio is the same interpreter with
the same lock, and the reading takes turns with the window as it does from
source. What the process is for does not change, and neither do stopping
at once, the memory given back, or the priority.

**How the process is started changes.** There is no `python` to run and no
`-m`: `sys.executable` is the Studio itself. It is started again with an
argument (`--reading-process`, say), and the entry point sends that to the
process's `main` before anything of Qt is imported. That is why the
process's side has a `main` of its own from the start, and why
`__main__.py` should look at its arguments before it imports `app`. From
source the same argument works, so there is one way to start it and not
two.

**What it costs to start is the figure that does not carry over.** The
0.30 s is Python from a folder on disk. Section 4 rests on it: a process
for each reading is the simpler choice because starting one is nearly
free.

- *Built as a folder* there is nothing to unpack, and the start should be
  near what it is from source. To be timed.
- *Built as one file,* the executable unpacks itself to a temporary folder
  each time it starts, which with Qt in it is seconds. PyInstaller hands
  the folder already unpacked to a process the application starts from its
  own executable, so the reading should not pay it again; that too is to
  be timed and not assumed.
- *A virus scanner* looks at an executable as it starts, an unsigned one
  the harder. Once for a Studio that starts once; once a reading here.

If the start turns out dear, the answer is a process started ahead and
left waiting for its request, with the next started as that one is given
work: still a process for each reading, still ended to stop it, and the
start paid where nobody waits for it. Not one kept running, for the
reasons of section 4, which freezing does not touch. This is open question
2, and a frozen build is what would decide it.

**A window with no console.** A windowed executable has no standard
streams of its own, as `pythonw` has none. The process is given all three
by name, the two pipes and somewhere for its errors, and nothing is left
to be inherited.

**Two entries in the Task Manager, under one name,** one of them a
gigabyte for a minute. Somebody may end the second. That is a reading that
died, and is read on the thread (section 5).

**Not outliving the window matters more.** An installer or an update
cannot replace an executable that is still running. The process ends
itself when the window's pipe closes, however the window went; the test
for that (section 8) is run against the frozen build as well.

**One thing a frozen build opens.** Section 7 sets aside a Python built
without the lock because the Studio runs on whatever Python is installed.
Frozen, the Python is chosen and shipped, and that reason goes. Three
others stand. PySide6 publishes no build for such a Python (asked of the
package index on 13 October 2026, for 3.13 and 3.14 on Windows; lxml has
one for 3.14). The reading shares the index's one connection and its kept
answers with the window, safely only because the two take turns. And
whoever runs the Studio from source, the tests and the bench among them,
would run something else than what is released. Worth asking again when
PySide6 has a build; not a reason to wait.

**What to do about it now.** Build phase 2 as written, with the argument
looked at before Qt is imported. When a freezing tool is chosen, time the
start in it, as a folder and as one file, before open question 2 is
answered.

## 12. Open questions

Answered on 14 October 2026, but for the first, which was answered on 24
October (#217).

1. **Whether the search answers should outlast a launch.** Yes. What the
   index keeps for an expression is the assets it matches in the game's own
   file, with no mod applied (`AssetIndex.find_matching`). The mods are
   not in it: at each reading, every asset a mod before the op has changed
   or made is dropped from that answer and asked again as the mods leave it
   (`Translator._run_search`). So the kept part changes only with the
   expression, the game's file, and the rule the Studio answers an
   expression by. The index's file is already named for the game's file
   and the index's format (`cache_path_for`); kept beside it, the answers
   would want the Studio's version beside them as well, for the rule. 70
   KB, and the 28 s paid once for a game version and not once a session.
   Built: the window's index writes them beside its file
   (`searches_path_for`) whenever it has new ones, and reads them when it
   is opened, if they were worked out under the same `search_rule` (the
   Studio's version, lxml's, and from source a hash of `core/index.py`)
   in the same build of the index. A file beside it is not the index, so
   writing it needs no `hold`.
2. **Whether the process starts with the edit and not with the reading.**
   No: it starts with the reading. 0.3 s of 39 from source. A frozen build
   may start slower (section 11), and is the time to ask again.
3. **What is said when the reading falls back to the thread.** One line in
   the status bar, once a session, as suggested. Built.
4. **How the index is shared with a writer** (section 6). The reading is
   ended first, and none starts until the write is done
   (`ReadingsController.hold`). Built.
5. **Memory on a small machine.** Accepted as it is.
6. **Whether the first reading of a folder follows.** No: it stays on its
   thread, behind its dialog.
