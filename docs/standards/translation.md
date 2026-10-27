# Translating the Studio

The Studio's own words -- menus, buttons, hints, messages -- can be shown in a
language other than English. Game texts are a separate matter: asset names come
from the game's own `texts_*.xml`, in whichever language was picked on import.

Translations other than English are made by machine, and the language picker
says so. English→German machine translation is fluent; what it gets wrong is
context (a bare "Open" could be a verb or a state), consistent terminology, and
idiom. The rules below exist mostly to give it the context, and to make sure a
sentence *can* be translated at all.

## How it works

| Piece | What it does |
|---|---|
| `core/i18n.py` | `tr`, `trn`, `tr_noop`, plus `number`, `join_list`, `date_text`. Qt-free, so core's own messages go through it too. |
| `core/po.py` | Reads and writes `.po` catalogs. |
| `locale/<code>.po` | One catalog per language: each English sentence and its translation. The only translation file; nothing compiled is committed. |
| `ui/language.py` | At start-up, installs the language chosen in *Settings ▸ Language* (default: Windows' display language), and Qt's own catalog for the words Qt draws itself (Close, Cancel, a text field's right-click menu). |
| `tools/translations.py` | Finds every marked sentence, enforces the rules, keeps the catalogs in step. |
| `tests/test_i18n.py` | Fails if a marked sentence breaks a rule, a catalog is out of step with the code, or a translation does not fill in what its English does. |

A language is applied once, before the first window is built. Changing it in
Settings takes effect on the next start.

An English sentence is its own key. Anything untranslated -- new text nobody
has translated yet, or text whose English changed since -- shows in English.
Nothing is ever blank and nothing breaks.

## Marking text

```python
from ..core.i18n import tr, trn

QLabel(tr("Settings"))
tr("Saved %s.", name)                                  # one value: positional is fine
tr("%(mod)s overrides %(other)s.", mod=a, other=b)     # two or more: name them
trn("%(n)s mod", "%(n)s mods", count)                  # a count: trn, and %(n)s
trn("One mod changes %(what)s", "%(n)s mods change %(what)s", count, what=x)
tr("Open", context="verb")                             # same English, different meaning
```

**Whole sentences.** German puts words in a different order, so a sentence
glued from fragments cannot be translated. Pass the variable parts *into* a
whole sentence:

```python
# No: the fragments translate, the order does not.
"Removed " + name + " from " + where
"%s%s" % (path, "" if chosen else "  (the default)")
# Yes
tr("Removed %(name)s from %(where)s.", name=name, where=where)
path if chosen else tr("%s  (the default)", path)
```

When a sentence has a few variants, write each variant out in full rather than
assembling one from optional parts. It costs a few more strings. Each one
reads right in every language.

**Format through tr, never afterwards.** `tr("Saved %s.", x)`, not
`tr("Saved %s.") % x`. A translation with a mistake in its placeholders then
falls back to English rather than raising. With no values given, the text is
returned untouched, so a literal `%` needs no doubling. With values, it does
(`%%`), as usual.

**Counts go through `trn`.** `"s" if n != 1 else ""` is an English rule.
`trn(singular, plural, n, **values)` picks the form, and fills `%(n)s` with the
count written the reader's way (1,234 / 1.234). The singular may leave the
number out ("One mod"). Write `%(n)s`, never `%d`: the count arrives already
formatted.

**Lists inside a sentence go through `join_list`.** `join_list(names)` gives
"A, B and C", or "A, B oder C" with `join_list(names, "or")`. A list set out
one per line, or after a colon, is not part of a sentence. Plain `", ".join`
is fine there.

**Numbers and dates the reader sees go through `number` and `date_text`.**
`number(1234)` gives "1,234" or "1.234", and `number(x, 1)` has one decimal.
`date_text(ts)` gives "05 Sep 2026". Use `year=False` and `clock=True` as
needed. Not for GUIDs (identifiers, never grouped), and not for anything
written to a file name or a record: those stay ISO.

**Context** separates two uses of identical English that another language says
differently: "Open" (verb) vs "Open" (state), "Normal" (text size) vs "Normal"
(priority). Use it when the English is a word or two and ambiguous. It is part
of the lookup key, so `tr_noop` and the later `tr` must pass the same one.

**A note for the translator** goes in a comment directly above the call:

```python
# Translators: "Play" is the Studio's name for its launch tab, not the verb.
tab.setText(tr("Play"))
```

**Nothing at import time.** No language is installed while modules load, so
`tr()` at module or class level, or in a default argument, would stay English
forever. The extractor refuses it. Mark module-level tables with `tr_noop` and
translate them where they are shown:

```python
DENSITY_LABELS = (("compact", tr_noop("Compact", context="density")), ...)   # stays English here
combo.addItem(tr(label, context="density"), key)                             # translated when shown
```

`tr(variable)` is allowed for exactly this case. The extractor cannot see
inside a variable, so the text must be marked with `tr_noop` where it is
written.

**Never Qt's `self.tr()`**. Its lookups go to Qt's translator, not the
Studio's catalogs. The extractor refuses it.

## What is *not* translated

Translating is only for words a person reads in the Studio's window. Before
marking a string, check where it goes:

- **Anything written to disk or sent anywhere**: mod files, XML, `modinfo.json`,
  XML comments in generated ModOps, loadout files, settings keys and values,
  logs, file and folder names, anything sent to mod.io. A mod made on a German
  PC must be the same mod.
- **Anything compared or looked up**: a string used as a dict key, compared with
  `==`, matched with `in`/`startswith`, or parsed back. Translate it where it is
  *shown*, and keep the English as the key (the `tr_noop` pattern above).
- **Names**: Anno 1800, Anno 117, mod.io, Steam, Ubisoft Connect, iModYourAnno,
  GUID, XPath, ModOp, file names, XML tags and attribute names.
- **Text that comes from elsewhere**: game texts, mod names and descriptions,
  error messages from the OS or from lxml. Wrap the Studio's own sentence around
  them, never the other way.
- **Developer-facing text**: `repr`, assertion messages, `log.debug`.

**Text that is both kept and shown** follows a third pattern. Why a ModOp
could not be read, why a ModOp is kept as written, a change's summary: these
are saved in the loadout file and written into exported mods, so they stay
English, and they are also what the Studio shows. Mark each shape where it is
made, with a context that says so, and fill it with `%` as before:

```python
reason = tr_noop("there is no asset %s in your assets.xml", context=STORED) % guid
```

Where it is shown, `tr_stored(reason)` recognises the shape in the finished
English, takes the values back out, and fills them into the translation. Values
that are themselves marked shapes ("kept as written: <reason>") are translated
too, and reasons joined with "; " one at a time. Nothing extra is saved, so a
loadout saved before the catalog existed reads in German as well; an older
wording that no longer matches simply stays English. The shapes follow the
same rules as `tr` (whole sentences, named placeholders once there are two),
plus two of their own. A shape may not be only placeholders, and two
placeholders may not touch: the Studio could not tell where one value ends and
the next begins. The test suite checks that every marked shape is recognised
as itself.

When core raises an exception whose message the Studio shows, the message is
marked where it is raised. Check first that no code matches on that message.
Match on the exception type or an attribute instead.

**Keywords** are marked in the English: `[[patch]]`, or `[[patch|patches]]`
where the words shown are another form of the term
([design standards](design-standards.md#keywords)). A translation keeps every
mark, with the key as it is and its own words after the bar:
`deinen [[patch|Patch]]`, `das [[live|Live-Profil]]`. The check fails a
translation that marks other keywords than its English, or not as many
times. English kept as it is (`STORED`) marks none, since it is written to
files.

Mark a Qt call's deliberately English argument with `# i18n: keep - <reason>`
on its line (a brand name, a file pattern). Otherwise `lint` will flag it.

## The commands

```bash
python tools/translations.py update        # after changing any marked text
python tools/translations.py check         # what the test runs
python tools/translations.py lint          # English handed to Qt without tr()
python tools/translations.py todo de > todo.jsonl
python tools/translations.py apply de done.jsonl
python tools/translations.py new fr "Français (traduction automatique)" "nplurals=2; plural=(n > 1);"
```

`update` adds new sentences to every catalog untranslated, flags ones whose
English changed as *fuzzy* (shown in English until retranslated), and drops ones
the code no longer uses. Commit the catalog with the code change.

`lint` is a safety net for the UI, not a proof. It sees text handed straight to
a Qt call (`QLabel("…")`, `setText("…" % x)`), not text put in a variable
first or assembled in core.

## A translation pass

1. `todo de > todo.jsonl` lists every untranslated or fuzzy entry, one JSON
   object per line: the English, its context, where it is used, any translator
   note, and for fuzzy ones the previous translation.
2. Fill in `strings`: one string, or one per plural form. Read the code at
   `references` when the English alone is ambiguous.
3. `apply de done.jsonl` writes them in, refusing any whose placeholders do
   not match the English.
4. `check`, then commit.

A translation a person has corrected is never overwritten: `todo` lists only
untranslated and fuzzy entries.

## German

Decisions for `de.po`. A new translation follows them, so the Studio reads as
one piece of writing rather than as 1,800 separately translated sentences.

**Voice.** The reader is addressed as *du* (lowercase), as Anno 117's own
German does and as the modding community writes to itself. The English is plain
and a little dry, and the German should be too. Translate what a sentence says,
not word for word. Break a long English sentence in two if German reads better
that way.

**Typography.**
- Quotation marks are „…“ and the inner ones ‚…‘.
- Keep …, ▸, ›, —, and every placeholder, `&` accelerator, `\n` and HTML tag.
- An `&` accelerator goes before a letter of the German word. Two entries in
  one menu must not share a letter.
- Numbers use `1.234,5`. Dates read `05. Sep. 2026`.

**The Studio's own names.** Other text refers to these, so they are fixed:

| English | German |
|---|---|
| File / Edit / View / Help menus | Datei / Bearbeiten / Ansicht / Hilfe |
| Mods menu | Mods |
| Game menu, its Library submenu | Spiel, Bibliothek |
| Check menu | Prüfen |
| Play (the menu, and the feature that starts the game) | Spielen |
| Settings | Einstellungen |
| Start, Asset editor, Mod file, References, Export (tabs) | Start, Asset-Editor, Mod-Datei, Verweise, Export |
| Mod changes (the panel) | Mod-Änderungen |
| card 4 / the fourth card (on the Start tab) | Karte 4 / die vierte Karte |
| the library (where the Studio installs mods) | die Bibliothek |
| generated copy (a library mod with the Studio's files written into it) | die generierte Kopie |
| loadout, profile | das Loadout, das Profil |
| project (the file a mod of yours is kept in) | das Projekt |
| make live (write a loadout into the mods folder) | live schalten |
| fork (a loadout) | abzweigen, der Abzweig |
| your own copy (of somebody else's mod) | deine eigene Kopie |
| Undo / Redo | Rückgängig / Wiederholen |

**Terms.**

| English | German |
|---|---|
| mod, mods | die Mod, die Mods |
| ModOp, op | die ModOp, die ModOps (never translated) |
| asset, template, GUID, XPath, ModID | das Asset, das Template, die GUID, XPath, die ModID |
| patch (a mod that changes another mod) | der Patch |
| mod file (the file a mod is exported as), outline | die Mod-Datei, die Gliederung |
| base game | das Basisspiel |
| mods folder | der Mod-Ordner |
| game files | die Spieldateien |
| localisation texts | die Lokalisierungstexte |
| load order; loads after | die Ladereihenfolge; lädt nach |
| switched on / off (a mod, a change) | aktiviert / deaktiviert |
| change (to an asset) | die Änderung |
| pattern; kept ModOp | das Muster; die behaltene ModOp |
| field, value | das Feld, der Wert |
| conflict; contested field | der Konflikt; umstrittenes Feld |
| backup | die Sicherung |
| collection (mod.io) | die Sammlung |
| subscribe / unsubscribe (mod.io) | abonnieren / abbestellen |
| the game's mod browser | der Mod-Browser des Spiels |
| author (who publishes) | der Autor, die Autorin; neutrally „wer veröffentlicht“ |
| history, version | der Verlauf, die Version |
| hop (in the reference graph) | der Schritt |
| points at / is pointed at by | verweist auf / wird verwiesen von |
| link (a mods-folder junction into the library) | der Link (a *Verknüpfung* is a Windows shortcut) |
| link (an edge in the reference graph) | die Verbindung |
| revert; discard | zurücksetzen; verwerfen (*rückgängig* is only Undo) |
| read in, re-read (a mod, a folder) | einlesen, neu einlesen |
| convert / the conversion (to the library) | umstellen / die Umstellung |
| install (a mod from mod.io, into the library) | installieren |
| download (the transfer only) | herunterladen |
| API key; sign in / out | der API-Schlüssel; anmelden / abmelden |
| set aside; put back | beiseitelegen; zurücklegen |
| bundled (a mod inside a mod) | mitgeliefert |
| narrow ModOp | eng gefasste ModOp |
| tick / untick | anhaken / den Haken herausnehmen |
| match (a search result) | der Treffer |
| Currently editing (the editor's picker) | Aktuell bearbeitet |
| the Start tab | der Tab „Start“ |
| Play, named in running text | „Spielen“, in quotation marks |
| checkpoint (a saved state in the loadout's history) | der Zwischenstand |
| build (a numbered build of a mod) | der Build |
| version bump; Major / Minor / Patch (the version part) | die Version erhöhen; Hauptversion / Nebenversion / Patch-Version |
| release (a published version) | die Veröffentlichung |
| the game's loader (Anno Modding Tools' mod loader) | der Loader des Spiels |
| probe; reading rule | die Probe; die Leseregel |
| script, module, hook (Lua) | das Skript, das Modul; hook names (Init, Tick…) stay as they are |
| difficulty; game setup (modinfo fields) | die Schwierigkeit; das Spiel-Setup |
| in-game name | der Name im Spiel |
| taken twice (a GUID) | doppelt vergeben |

Names stay as they are: Anno 1800, Anno 117: Pax Romana, mod.io, Steam,
Ubisoft Connect, iModYourAnno, Anno Modding Studio, file names, XML tags.
Game concepts use the game's own German. Look them up in the game's
`texts_german.xml` rather than inventing a term.
