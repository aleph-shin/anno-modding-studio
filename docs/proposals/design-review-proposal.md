# Design review: a proposal

How to make the Studio easier to read, see and operate without making it less
exact. The proposal covers the words on screen, tooltips and help, the look and
its themes, scaling, accessibility, and the README. It ends with the standards
that new features must meet and the checks that keep them honest.

It is based on a survey of the app on 2 Oct 2026. The survey had four parts:

- an audit of every user-facing string, from source and from the German catalog
- an audit of scaling and accessibility, including measured contrast ratios
- screenshots of the running app at Normal and Largest text size
- renders of the real app under four candidate themes, which became the
  colour schemes and drawing styles in section 3.5

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/`, `docs/` or `README`.

**Recommendation in one paragraph.** Write for two audiences at once: the
power user, who wants to know exactly what a button will do, and the newcomer,
who knows what mods are but has never opened a ModOp. Do it with layers, not
modes:

1. The surface uses the modding system's real words and says only what a
   control does.
2. The explanation sits one step away, behind a `?` beside the label.
3. Guidance for someone new appears where the screen is empty, and goes away
   once there is work on it.

Under that, put every colour into tokens, so a colour scheme is a set of
values and the drawing style (boxes or flat, roomy or compact) is a separate
switch that works with any scheme. Make a single Interface size setting
scale everything. Bring keyboard, screen reader and contrast support up to a
stated baseline. Then rewrite the
strings and the README area by area, with the German following each area.
Checks in `tools/check.py` hold every rule at its current count and ratchet
the counts down, as they already do for lint.

**Status.** Proposed and approved 2 Oct 2026, and built: all six stages
were done by 3 Oct 2026. `REVIEW.md`, Pass 9, records each stage and what it
left open. The counts and file names in this document are the survey's, as
they stood before the work.

---

## 1. Who the Studio is for

Every design question here comes back to the tension between two groups of
people.

**Power users** maintain a long, fiddly modlist, or publish several mods
meant to be used together. They care what the Studio will literally do when
they press a button: which file it writes, which ModOp, in which load order.
Hiding how modding works for the sake of friendliness makes things harder for
them, not easier.

**Newcomers** are comfortable with modding as an idea. They may already mod
Anno with a mod manager. They have probably never edited a mod's XML or
installed the mod loader's VS Code extension. They picked up the Studio as a
good way to manage mods, and maybe to try its other features later. They
don't want to be buried in detail on day one, but they aren't beginners at
computers or at the game.

People sit anywhere between the two, and part of the Studio's job is to help
a newcomer become a power user. So there is no easy mode and no advanced mode.
The answer is one interface where:

- **the surface is literal.** Labels use the real terms (ModOp, patch, load
  order, ModID, loadout) and name the action exactly. A newcomer learns the
  real vocabulary from the first screen, because that vocabulary is what lets
  them grow. A power user never has to translate a friendly word back into the
  real one.
- **the depth is one step away.** Every term or option that needs explaining
  has a `?` beside it. Behind it is the mechanism, the consequences and the
  edge cases. It is there when you want it and out of the way when you don't.
- **guidance is temporary.** A tab with nothing on it says what it is for and
  how to start. Once you are working, that text is gone and the space belongs
  to the work.

---

## 2. What the survey found

### Things to keep

The structure of the app is sound, and several details already do what this
proposal asks for:

- The three cards on the Start tab are choices, not modes. Each one sets the
  project up and then gets out of the way.
- The Start tab's numbered steps answer themselves in their headers: which
  game, which project, which way of working.
- The editor's rows show state with weight, italics and strike-through plus a
  text note, not with colour alone (`ui/editor_panel.py:924`).
- The editor's `+` and right-click actions are also in the Edit menu, so they
  work from the keyboard.
- Search handles the keyboard well: Down moves into the results and Esc
  closes them. Back and forward are on Alt+← and Alt+→.
- Ellipses are used consistently for buttons that open a dialog.
- A handful of strings already read the way all of them should, for example:
  "Search by in-game name, internal name, GUID, or template, then narrow down
  with the template filter. Pick a row to start editing it."

### Too much text on the surface

| | Count |
|---|---|
| Catalog entries (`locale/de.po`) | 2,391, 867 of them 8 words or longer |
| Places that call `tr`: `ui/` / `core/` | 1,822 / 806 |
| Always-visible hint labels | 204, about 3,900 words in all |
| Hint labels over 30 words | 50; the longest is 109 (`ui/convert_dialog.py:86`) |
| Words on the Start tab before you have done anything | about 400 |
| Checkboxes with labels over 4 words | 14 of 21 |
| Buttons of any kind with labels over 4 words | 27 of 155 |
| Tooltips | 181 `setToolTip` calls; median 16 words, longest 147 |

The pattern behind most of these is that a label describes the mechanism
instead of naming the choice. Take "This is my own mod — carry on with it under
its name, ModID and version" (`ui/mod_import_dialog.py:293`): the label is
15 words, and its tooltip explains the same thing again in 45. Several long
explanations also appear twice with slightly different wording, once in
`ui/mod_import_dialog.py` and once in `ui/mods_folder_dialog.py`.

Every tab also opens with an explanatory paragraph. That is helpful the
first time and noise on every visit after.

### The voice

Across the 867 longer strings:

| Pattern | Strings |
|---|---|
| Em-dash or `--` | 163 (19%) |
| Semicolon | 105 (12%) |
| Colon setting up a punchline | 106 (12%) |
| The Studio, the loader or a hint "says", "asks", "tells" or "knows" something | 42 |
| Sentences opening "Nothing…" | 25 |
| "rather than" | 22 |
| "exactly" | 19 |

The voice of the code comments has leaked into the interface. It builds a
setup, then a colon, then a payoff. It closes on an aphorism ("every switch is
the Studio's to set and Play is certain"). It reassures ("Nothing here is a
mode"). It gives the app a personality ("Nothing needs it: without it, that
one line says so."). This reads as machine-written, and it is slower to scan
than plain instructions. The README and the design notes share the same voice.

### Tooltips disappear and can't be reached

- Qt hides a tooltip after 10 seconds plus 40 ms for each character past 100.
  The longer tooltips here can't be read in that time. Nothing in the app
  changes the timeout.
- Tooltips in trees and tables go through a different code path, so fixing
  plain widgets would not fix them.
- A tooltip only appears under the mouse. Keyboard users never see one.
  Magnifier users often can't, because the tooltip appears away from the part
  of the screen they have zoomed into.
- Some information exists only in a tooltip: mod option descriptions
  (`ui/mod_options_dialog.py:122`), reference graph details, and the detail of
  unread-op warnings.

### Text size scales text and nothing else

At Largest:

- Checkboxes and radio buttons stay 14 px PNGs.
- The editor's `+` is a 15 px glyph drawn at a fixed size
  (`ui/editor_panel.py:84`).
- Tree arrows stay at their native size.
- The reference graph's fonts are fixed in points and ignore the setting
  (`ui/reference_panel.py:131`).
- "Revert selected" is clipped.

There are about 250 fixed pixel sizes across 40 files. The raster images are
the check, radio and spin-arrow PNGs, which `tools/make_*.py` generate, and
the mod.io logo. The logo's @2x file is never loaded, because
`ui/modio_brand.py:50` uses a plain `QPixmap`.

### Accessibility

Measured or counted:

- **Focus.** Focus rings on buttons, checkboxes, radio buttons and tabs come
  from Fusion's default and measure about 1.65:1 against the background,
  which is effectively invisible. Only text fields and combo boxes have a
  `:focus` style.
- **Accessible names.** There is one `setAccessibleName` call in the app. No
  label has a buddy, about 20 "Label:" texts sit beside fields with no link
  to them, and nothing sets a tab order.
- **Double-click.** Nine lists can only be activated by double-click, with no
  Enter equivalent. The busiest one is the changes panel
  (`ui/changes_dock.py:424`).
- **Reference graph.** It is mouse-only, and a screen reader can't see it.
- **Colour alone.** WARN and ACCENT are the same amber, 1.11:1 apart. The
  Export problem list tells errors from warnings by colour alone. The changes
  list tells a removal from an edit by red against green alone.
- **Contrast.** Selected text is 4.14:1, the XML preview's comments 3.62:1,
  field and checkbox borders 1.38:1, and scrollbar handles 1.70:1.
- **Keyboard shortcuts.** No button, tab, checkbox or label has a mnemonic.
  There is no shortcut for switching between the main tabs.
- **Copy buttons.** The Copy button disables itself for its feedback flash,
  which throws keyboard focus elsewhere (`ui/widgets.py:295`).
- **Themes.** There is only a dark theme. Windows light mode and Windows
  contrast themes are ignored.
- **Inline colours.** About 30 inline `setStyleSheet("color: …")` calls and
  the XML highlighter's three fixed colours sit outside the theme.

### The look

The dark slate with Anno gold suits the Studio and is worth keeping. The
problems are details:

- **Gold means too many things.** It marks the primary button, links, the
  current tab, asset titles, mod headings in the changes list, warnings and
  selection.
- **Page titles repeat the tab.** Most tabs repeat their own name as a heading
  above the explanatory paragraph.
- **Boxes inside boxes.** The Start tab has cards inside step cards. The
  Export tab stacks four group boxes.
- **Mixed alignment.** The Export form's labels are right-aligned, and its
  hint text is indented to a third position.

### Help

The Help menu has one entry: About.

---

## 3. The standards

These become `docs/design-standards.md`, written in Stage 0 and followed by
everything after it. Each standard below says what the rule is. Section 4
says how it is checked.

### 3.1 Three layers of text

| Layer | What it's for | Budget |
|---|---|---|
| **Label** (button, checkbox, radio, tab, menu entry, column) | Says which choice the click makes. Buttons are a verb and its object. | 1–4 words. Up to 6 where a real term needs them, for example "Export as zip". |
| **Consequence line** (optional, always visible) | Only where a click is irreversible, writes outside the project, or does something a reasonable person would not expect. States the consequence, not the mechanism. | One sentence, up to about 20 words. |
| **Help** (`?` beside the label) | The mechanism, why it matters, and the edge cases. | Up to about 80 words. Longer belongs in the README, linked from the help. |

Plain tooltips directly on a control stay, for four uses only:

- naming an icon-only button
- showing a shortcut
- showing the full text of a truncated cell
- a short clarification of a terse label

They stay under about 15 words. Anything longer moves behind a `?`.

**Empty states replace tab-top paragraphs.** When a tab or list has nothing
to show, it says in one or two sentences what belongs there and how to get it,
with the action as a button where there is one. Once there is content, the
text is gone. Page headings that repeat the tab's name are removed.

**Literal means literal.** Anything that writes outside the project says
where before it writes: the folder, the file, or the mod.io page. Dialog
buttons are named for their action ("Unload 3 mods", "Keep both"), never
Yes/No or OK/Cancel where the action has a name. There are 68 Yes/No buttons
today. A status message uses the button's verb, so the Export button leads to
"Exported Better Bricks to …".

### 3.2 Voice

Write the way a knowledgeable person explains something to a friend sitting
beside them. In practice:

- **Lead with the outcome.** Say what happens, then why if the reader needs
  it.
- **Use second person, present tense, active voice.** "You", not "the user".
- **Keep sentences short.** One idea per sentence, usually under 20 words.
- **Use sentence case everywhere,** including titles and buttons.
- **Avoid these in UI text:**
  - em-dashes: use a full stop, a comma or brackets
  - semicolons: split the sentence
  - "rather than" and "not X but Y" contrasts: just say what it does
  - reassurance openers ("Nothing is…"): state the specific fact instead,
    such as "Your mods folder isn't changed"
  - giving the app a personality: it *shows*, *checks* or *writes*; it
    doesn't *say*, *ask*, *know* or *decide*
  - punchline closers, aphorisms, lists of three for rhythm
  - filler words like "exactly", "simply" and "just"
- **Errors** say what happened and what to do next, in that order. They don't
  apologise.
- **Use one word for one thing.** The glossary in `design-standards.md` lists
  each term, what it means, and the words not to use for it.
- **German** keeps the du-form and the rules in `docs/translation.md`.

Some examples from the current text:

| Today | Rewritten |
|---|---|
| Until this is set, every mod loaded counts as somebody else's — so the Studio offers a patch rather than editing one. | Mods with this creator name are yours, and the Studio edits them directly. Other mods get a patch. |
| What the Studio remembers about you and this machine. None of it belongs to the mod you are working on, and all of it is still here the next time you open one. | *(removed: the dialog's title says it)* |
| ☐ This is my own mod — carry on with it under its name, ModID and version | ☐ **This is my mod** `?` *(Help: keeps its name, ModID and version, and edits it directly instead of making a patch.)* |
| ☐ Header comment (mod details, game version, counts) | ☐ **Header comment** `?` |
| Revert all changes to this asset | **Revert asset** |

### 3.3 Help icons

One widget, `HelpTip`, used for every `?`. It appears beside the label it
explains.

- **Drawing.** A circled question mark one text-height tall, in the dim text
  colour, gold on hover and focus.
- **Keyboard.** It takes focus with Tab. Space, Enter or F1 opens it, and Esc
  closes it.
- **Popup.** It opens on hover after a short delay, or on click. It is a
  floating panel, not a `QToolTip`. It wraps at about 45 characters a line,
  its text can be selected and copied, and it stays open until the mouse has
  left both the icon and the panel, or Esc is pressed. It never times out.
- **Links.** It can end with a "More in the guide" link to a README section.
- **Screen readers.** Its accessible name is "Help: \<label\>" and its
  accessible description is the help text, so a screen reader reads it
  without opening it.
- **Magnifiers.** It opens next to the icon, which is where the focus and
  the magnifier already are.

**Plain tooltips stop timing out.** An event filter on the application
handles `QEvent.ToolTip` for plain widgets and for item views (through
`indexAt` and `ToolTipRole`), and shows the text with no expiry. The tooltip
still hides when the mouse leaves or a key is pressed.

### 3.4 Containers mean something

- **Cards** (`WayCard`, `StepCard`) mean "these are alternatives, or steps in
  order". They look the same in every colour scheme and style.
- **Group boxes** mean "these settings belong together". The Outlines
  setting draws them as boxes (Boxed) or as a heading over a rule (Flat).
  Neither reading changes the meaning.
- **No containers are nested for decoration.** A card holds content, not
  other cards that are only there for looks.
- **Form labels are left-aligned** and sit above or beside their field
  consistently within a dialog. Help and consequence lines align with the
  field they belong to.
- **Spacing** uses a 4, 8, 12, 16 and 24 px scale. Dialog margins are 16 and
  standard spacing is 8, which most dialogs already use.

### 3.5 Colour schemes and drawing style

A theme has two independent parts:

- **A colour scheme**: a set of values for the semantic tokens below, and
  nothing else.
- **A drawing style**: how containers and spacing are drawn. Any style works
  with any scheme.

A new scheme is a new set of values. It needs no new style rules, and the
contrast tests check it automatically.

**Tokens.** Every colour is a semantic token in `ui/theme.py`. Code asks for
what a colour *means*, never for a hex value:

| Group | Tokens |
|---|---|
| Surfaces | `bg`, `surface` (fields, lists), `raised` (headers, buttons) |
| Lines | `border` (decorative), `field-border` (where a control starts, 3:1 or better), `focus` (the accent, or a darker shade of it where the accent fill is too light to reach 3:1) |
| Text | `text`, `text-dim`, `text-disabled`, `text-on-accent` |
| Accent | `accent` (fills), `accent-text` (accent used as text), `accent-hover`, `selection` |
| Status | `ok`, `warn`, `error`, `rule`, each always with a glyph or word, never colour alone |
| Changes | `changed`, `removed`, `added` |
| XML preview | `syn-comment`, `syn-tag`, `syn-attr`, `syn-value` |

**The accent has one meaning:** something you can act on, or what is
current. That covers the primary button, focus, the current tab, the checked
state, selection and links. It is never used for a warning or for
decoration. In most schemes the accent is Anno gold; Timber's is pine green.
Warnings get their own orange. Asset titles and mod headings use weight, not
colour.

**Six colour schemes:**

| Scheme | Description |
|---|---|
| **Granite** | Today's look, tidied: blue-grey dark with Anno gold. The default. |
| **Brick** | Dark, with brick red as the dominant colour, strongest in the raised bands: tabs, headers, buttons and the status bar. Anno gold stays the accent. |
| **Marble** | Light: a cool white, not cream. Gold stays on the primary button and turns bronze wherever it is read as text, and for focus. |
| **Timber** | Light, on pale pine wood, with a deep pine-green accent like painted trim. |
| **Obsidian (high contrast)** | Black, white and yellow, with every boundary drawn. |
| **Quartz (high contrast)** | White, black and a deep blue accent with white text on it, with every boundary drawn. |

**How Brick and Timber were settled.** Both were rendered in two versions
on 2 Oct, and these were chosen:

- **Brick: the redder version.** A more muted one read as brown, not brick.
  Brick's surfaces share a hue with `error` and `removed`, so those two are a
  lighter, pinker red than any surface. Their glyphs, required everywhere
  anyway, carry the rest.
- **Timber: pine green.** Gold sinks into the wood, and a burnt-amber accent
  matched the wood's hue and stood out only by being darker. Pine green
  keeps everything you can act on in a different hue from the surface.
  Changed values move to a teal-green to stay apart from it.

Quartz was rendered the same day, from Obsidian's rules on a white ground.
The values for all six schemes are in Appendix A. The mockup that rendered
them, and the renders themselves, are kept in
[`docs/design-review/`](../design-review/README.md) as Stage 0's
specification and its reference.

**Naming guideline (not a hard rule):** a colour scheme is named after a
product from an Anno game. Older games such as 1404 or 2070 count, and the
name only needs to be close: "Quartz" is fine, though the in-game product is
quartz sand. A high-contrast scheme says so in its name.

**Drawing style** has two settings:

| Setting | Options | What changes |
|---|---|---|
| **Outlines** | **Boxed** (default) · **Flat** | Boxed draws group boxes, lists and headers with borders. Flat separates them by tone and a rule. Cards stay cards in both (section 3.4). |
| **Density** | **Comfortable** (default) · **Compact** | Padding, row height, gaps and corner radius. Compact suits a long modlist. Text size doesn't change: that is Interface size's job. |

Each option is a small block of style rules plus metric values (`radius`,
`pad`, `row-pad`, `gap`). It never sets a colour, which is what lets any style
combine with any scheme. Flat still draws its rules at the 3:1 floor, so
Flat stays readable in the high-contrast schemes.

**Settings.** Under *Appearance*:

- **Colour scheme:** Match Windows · Granite · Brick · Marble · Timber ·
  Obsidian (high contrast) · Quartz (high contrast)
- **Outlines:** Boxed · Flat
- **Density:** Comfortable · Compact
- **Interface size:** see section 3.6

How they behave:

- **Defaults.** Granite, Boxed, Comfortable, 100%.
- **Match Windows** picks Marble when Windows is in light mode and Granite
  when it is dark.
- **Windows contrast themes win.** When one is on, the Studio uses the
  contrast theme's own colours whichever scheme is picked, because people who
  turn those on may need those exact colours. A light contrast theme stays
  light. The scheme setting says this is happening.
- **No restart.** Schemes and styles apply at once, as Text size does today.

**Contrast floors.** These apply to every scheme: text at least 4.5:1,
including selected text and the XML preview; dim text at least 4.5:1; field
borders, focus indicators, checkbox borders and scrollbar handles at least
3:1. The high-contrast schemes aim for 7:1 text.

**Images.** Every image is SVG:

- **Indicators.** The checkbox, radio, spin-arrow, tree-branch and `?`
  images become SVG templates filled from the scheme's tokens, written to the
  cache folder when a scheme is applied. The PNGs and `tools/make_check.py`,
  `make_radio.py` and `make_spin_arrows.py` are removed.
- **mod.io logo.** It comes from mod.io's press kit, in all four versions. A
  scheme names the one that suits its background: Blue_White on dark schemes,
  Blue_Black on light ones, Mono_White on Obsidian, Mono_Black on Quartz.
  `tools/make_modio_logo.py` and the PNGs go.
- **App icon.** It stays as it is.

### 3.6 Interface size

**Interface size: 90% · 100% · 110% · 125% · 150% · 175% · 200%** replaces
Text size. The default is 100%.

- **How it works.** It sets Qt's own scale factor (`QT_SCALE_FACTOR`) before
  the application starts, so everything scales together: text, padding,
  indicators, the editor's `+`, the graph, and every fixed size in the code.
  A 150% render of the real app confirmed it.
- **Restart.** It takes effect after a restart, and the setting says so.
- **The game.** The variable is removed from the environment once Qt has
  read it, so the game doesn't inherit it when Play starts it.
- **Old settings.** None are migrated. The Studio isn't released, so a saved
  Text size is ignored and everyone starts at 100%.
- **Body text.** At 100%, body text is 14 px, today's Normal. 90% is there
  for anyone who wants things smaller, but it isn't the default.
- **Custom drawing.** Custom painting works in logical pixels and must stay
  sharp at fractional scales. The editor's half-pixel `+` drawing is reworked
  to do that.
- **Fonts.** The graph's fonts follow the application font instead of fixed
  point sizes.

### 3.7 Accessibility baseline

| Area | Requirement |
|---|---|
| Keyboard | Everything you can do with the mouse, you can do from the keyboard. Enter does what double-click does in every list. Ctrl+1–4 switch the main tabs. Buttons, checkboxes and labelled fields have mnemonics, unique within their dialog. |
| Focus | Every focusable control draws a focus indicator at 3:1 or better. Focus is never thrown away: Copy's feedback no longer disables the button. |
| Names | Every focusable control has an accessible name, from its own text, a buddy label, or `setAccessibleName`. Icon-only controls always set one. |
| Colour | No state is shown by colour alone. Errors, warnings, removals and conflicts each carry a glyph or a word. |
| Tooltips | Nothing essential lives only in a tooltip. Essential text is on screen or behind a `HelpTip`, which the keyboard can reach. |
| Reference graph | Nodes take focus, and arrow keys move between them. Enter moves the graph to the focused node, Ctrl+Enter opens it in the editor, and the menu key opens its menu. Each node's accessible name says what it is and how it links. |
| Screen readers | Each main tab and dialog is checked with Narrator during its stage, using a written checklist. |

### 3.8 Help without an encyclopedia

In-app help pages are out of scope. If they turn out to be needed after this
work, they can be revisited. The Help menu gets:

- **Keyboard shortcuts.** A dialog generated from the app's actions, so it
  can't drift from them.
- **User guide.** Opens the README.
- **Glossary.** Opens the README's glossary section.
- **About.**

`HelpTip`s link to README sections where a topic needs more than about 80
words.

### 3.9 Typography

- **Faces.** Segoe UI, or Segoe UI Variable where Windows 11 has it.
  Consolas for paths, GUIDs and XML, as now.
- **Sizes.** 13 for hints and secondary text, 14 for body, 16 for section
  headings, 19 for the asset title. Weights are 400 and 600 only.
- **No all-caps,** and no colour for emphasis.

---

## 4. The checks

These follow `tools/check.py`'s existing approach. Each rule records today's
count in `tools/check_baseline.json`. A change fails if any count grows, and
`--ratchet` writes a lower count back. Rules marked **hard** fail at once,
because they are either already at zero or cheap to reach zero in Stage 0.

### 4.1 Static checks: `tools/design_check.py`, run by `check.py --quick`

Read from the source's syntax tree (the survey's scripts are the starting
point). Each rule checks code in `ui/` and, for strings, `core/` too.

| Rule | Kind |
|---|---|
| A colour literal (`#rrggbb`, `QColor("…")`, a named colour) outside `ui/theme.py` | ratchet → 0 |
| `setStyleSheet` outside `ui/theme.py` | ratchet → 0 |
| A raster image added under `resources/` (anything but `.svg` and the app icon) | hard |
| `itemDoubleClicked` or `doubleClicked` connected without `itemActivated` or `activated` | ratchet → 0 |
| A `QMessageBox` with Yes/No or OK/Cancel buttons where the action has a name | ratchet |
| A label over its word budget: button or checkbox over 4 words, consequence line over 20, `HelpTip` over 80 | ratchet; exceptions need a `# design:` comment giving the reason |
| `setToolTip` with literal text over 15 words | ratchet → 0 |
| Voice patterns in UI strings: em-dash or `--`, semicolon, "rather than", a sentence opening "Nothing", the Studio as subject of says/asks/knows/tells, "exactly", "simply" | ratchet, per pattern |
| A `QLabel` ending in ":" built beside a field without a buddy, or with `QFormLayout.addRow(str, …)` | ratchet → 0 |

### 4.2 The window walk: `tests/test_design.py`

This uses the same approach as `tools/untranslated_widgets.py`. It builds the
main window and every dialog through a registry of dialog factories, which
the test owns. Then it walks every widget:

- every focusable widget has an accessible name (hard once Stage 1 is done)
- no plain tooltip is over 15 words at runtime, which catches text built in
  code (ratchet)
- every `HelpTip` can take focus and has an accessible description (hard)
- no widget carries a style sheet of its own, so every colour comes from
  the application's theme (hard once Stage 0 is done)
- every button, checkbox and labelled field in a dialog has a mnemonic, and
  none repeats within the dialog (ratchet)

### 4.3 Colour scheme tests: `tests/test_design.py`

These run for every scheme in the registry, so a new scheme is checked
without writing a test. They also run against a scheme built from a sample
Windows contrast palette, one light and one dark:

- every scheme defines every token
- every text and background pair the scheme uses meets 4.5:1, or 7:1 in a
  high-contrast scheme
- every non-text pair (focus, field borders, checkbox borders, scrollbar
  handles, Flat's rules) meets 3:1
- `warn`, `error`, `removed`, `changed` and `accent` are distinguishable
  by hue from each other and from the scheme's surfaces, not only by
  lightness. The threshold is set in Stage 0 from the approved schemes.
- every SVG template renders without error and fills from tokens only

And for the drawing styles:

- a style block sets no colour. This keeps schemes and styles independent.

### 4.4 Renders for review: `tools/render_themes.py`

Not a gate, because the look needs eyes, except once: at the end of Stage 0
it has to match the reference renders. It renders the four main tabs and
every registered dialog into a folder with an HTML contact sheet:

- every colour scheme, with the default style
- Flat and Compact, on Granite and Marble
- 100% and 200%
- English and German It uses the scratch method from the survey: the
`windows` platform, no `show()`, `grab()`, and isolated settings. A PR that
changes a screen attaches or links its renders.

### 4.5 The standards document

`docs/design-standards.md` holds sections 3.1 to 3.9 as rules, the glossary,
the voice examples, and a short checklist for a PR that adds or changes UI.
`docs/architecture.md` points to it, and `check.py` makes sure the new tools
and tests are listed there, as it does for modules.

---

## 5. The README

The README is 3,622 lines. Its order follows when features were built, and
it is written in the same voice as the UI. It gets a full rewrite, ordered
from general to specific:

1. **What the Studio is.** One screen: what it does, for whom, and a
   picture.
2. **Getting started.** Installing and running it, loading the game files,
   and choosing one of the three ways of working. The first ten minutes.
3. **The window.** The tabs, the changes panel, Play, and where the menus
   put things.
4. **Words you'll see.** ModOp, patch, load order, mods folder, library,
   loadout, profile, collection, rule, project, and your mod versus other
   people's. Each gets a sentence or two. This is the glossary the Help menu
   and the `HelpTip`s link to.
5. **Playing a set of mods.** Loadouts, the library, Play, profiles,
   conflicts, mod.io collections and updates, unloading.
6. **Changing a mod you downloaded.** Importing, patches, mod options,
   what to do when the original updates.
7. **Writing your own mod.** Finding assets, editing, adding fields and
   entries, new assets and GUID ranges, references, exporting, versioning,
   publishing to mod.io.
8. **How the Studio reads and writes mods.** One ModOp per change, path
   predicates, what it can't read and what it does then, values worked out
   while patching, rules, the check against the game's loader.
9. **Settings, shortcuts, and files the Studio keeps.**
10. **Troubleshooting.**

How the code is built, the tests and the sample files move to a new
`docs/development.md`. The design notes keep their content but stop mirroring
the README's headings (decision 10).

---

## 6. Decisions

All settled on 2 Oct.

1. **No modes.** Layers, not an easy/advanced switch.
2. **Interface size replaces Text size,** and needs a restart. It runs from
   90% to 200%, with 100% as the default. Saved Text
   sizes aren't migrated, since the Studio isn't released.
3. **A theme is a colour scheme plus a drawing style.** There are six
   schemes: Granite (the default), Brick, Marble, Timber, Obsidian (high
   contrast) and Quartz (high contrast). Brick is the redder version and
   Timber has the pine-green accent. Match Windows picks Granite or
   Marble. Windows contrast themes win, using their own colours. The old
   Slate is Granite, Light is Marble, and High contrast splits into Obsidian
   and Quartz. Graphite's flattening and density become the Outlines and
   Density settings, which work with every scheme.
4. **Schemes are named after Anno products.** A guideline, not a rule,
   recorded in `design-standards.md`.
5. **Cards stay cards in every style.** Only group boxes, lists and headers
   flatten.
6. **Every string is in scope,** including `core/`'s. The German is redone
   area by area as the English settles.
7. **The README gets a full rewrite** in the order of section 5.
8. **In-app help pages are out of scope** for now.
9. **mod.io's SVG logos** come in from the press kit, all four versions.
10. **The design notes stop mirroring the README.** They keep their content,
    are organised by subject, and link to and from the README at the
    section level.
11. **"User guide" opens the local `README.md`** with the system's default
    handler, falling back to showing its folder. Revisit when the Studio has
    a public home.
12. **The References list view waits.** It gets built in Stage 1 only if
    keyboard navigation in the graph proves awkward.

---

## 7. The stages

### Stage 0: foundations (medium; blocks everything)

- **Tokens.** `ui/theme.py` moves to semantic tokens: a registry of colour
  schemes, and the two style settings as blocks of rules plus metrics.
  The six schemes, the indicator templates and the style rules are ported
  from `docs/design-review/mockup/schemes.py` (values in Appendix A).
- **Appearance settings.** The Appearance settings and Match Windows.
- **Windows contrast detection.** It uses `QStyleHints.colorScheme()` plus
  `SPI_GETHIGHCONTRAST` through ctypes, because PySide6 6.6, the oldest
  version the Studio supports, can't report a contrast preference. The
  scheme built from Windows' contrast colours comes from `GetSysColor`.
- **Application palette.** It is set from the tokens, so Fusion's own
  drawing follows them.
- **Images.** SVG indicators and the `?` icon, generated from templates. The
  mod.io SVGs, chosen per scheme and loaded at the device pixel ratio.
- **Inline colours.** The 30 inline style sheets and the XML highlighter
  colours move to tokens.
- **Interface size.** The setting, `QT_SCALE_FACTOR` at startup, removing
  Text size, and the `+` and graph fixes.
- **`HelpTip` and tooltips.** The `HelpTip` widget, and the app-wide filter
  that keeps tooltips from timing out.
- **Checks.** `tools/design_check.py` and `tests/test_design.py`, with
  today's counts as the baseline. `tools/render_themes.py`.
- **Acceptance.** Stage 0 is done when `tools/render_themes.py`, run on the
  mockup's scenario, matches the reference renders in
  `docs/design-review/renders/`. Any difference is a bug or a deliberate,
  approved change (see `docs/design-review/README.md`). After Stage 0, the
  built renders become the references for later stages.
- **Standards.** `docs/design-standards.md`, first version, glossary
  included.

### Stage 1: accessibility mechanisms (medium)

- Focus styles in every scheme and style. Enter wherever double-click works. Ctrl+1–4.
  Copy keeps focus.
- Glyphs and words beside colour wherever status is shown: the changes
  list, the Export problem list, warnings.
- Keyboard navigation and accessible names in the reference graph. The list
  view too, if decision 12 calls for it.
- A Narrator pass over the main window, with the checklist written into
  `design-standards.md`.

Mnemonics, buddies and accessible names for individual widgets are done
per area in Stage 2, alongside the text, so each widget is touched once.

### Stage 2: the rewrite, area by area (large; one PR per area)

Each area gets:

- labels cut to their budgets
- long tooltips moved behind `HelpTip`s
- tab-top paragraphs replaced by empty states
- page titles that repeat the tab removed
- the voice
- buddies, names and mnemonics
- its tests' English assertions updated
- its German retranslated

The areas, in the order a newcomer meets them:

| Area | Files |
|---|---|
| 1. Start and Settings | `source_panel`, `start_controller`, `settings_dialog`, `language`, `preferences` |
| 2. Asset editor | `editor_panel`, `browser_panel`, `add_field_dialog`, `new_asset_dialog`, `asset_picker`, `rule_dialog`, `navigation` |
| 3. Changes panel | `changes_dock`, `conflicts_dialog`, `unread_dialog`, `history_dialog` |
| 4. References | `reference_panel` |
| 5. Export and publishing | `output_panel`, `modio_publish_dialog`, `modio_account_dialog`, `backup_dialog` |
| 6. Reading mods | `import_dialog`, `mod_import_dialog`, `mods_folder_dialog`, `mod_options_dialog`, `scripts_dialog`, `unload_dialog` |
| 7. Playing and the library | `live_controller`, `library_controller`, `patches_controller`, `convert_dialog`, `install_plan_dialog`, `loadout_check_dialog`, `unsubscribe_dialog`, `modio_get_dialog`, `modio_updates_dialog`, `modio_task_dialog` |
| 8. Menus, prompts, status bar, `core/` messages | `main_window`, `prompts`, `workers`, the 41 `core/` modules that call `tr` |

Areas 6 and 7 share the long explanations that are duplicated today. Each
explanation is written once, as one `HelpTip` text, and both dialogs use it.

### Stage 3: Help menu (small)

The shortcuts dialog, guide and glossary links, and About.

### Stage 4: the README (medium)

The rewrite in section 5, `docs/development.md`, and the design notes'
links, following decision 10. It can start once Stage 2 has settled the
glossary, and run alongside the later areas.

### Stage 5: close out (small)

- Ratchet every count.
- Make every rule that has reached zero a hard rule.
- Do a final render review of every scheme and both styles, at 100% and
  200%.
- Record the pass in `REVIEW.md`.

---

## 8. How to run it

- **Order.** Stage 0 blocks the rest. Stage 1 and the first areas of Stage 2
  can overlap once Stage 0's tokens and `HelpTip` exist. Stage 4 starts
  after area 3.
- **German.** A changed English string loses its German translation, which
  falls back to English until it is redone. Each area PR runs `python
  tools/translations.py todo` and isn't done until the area's entries are
  translated again, under `docs/translation.md`'s rules. German strings run
  about 30% longer, so the renders for each area include German.
- **Feature work.** An area that is mid-rewrite is frozen for features, one
  area at a time, never the whole UI.
- **Verification per PR:**
  - `tools/check.py` passes and no design count grows
  - `tools/untranslated_widgets.py` is clean
  - the area's renders are reviewed in every scheme, and in Flat and
    Compact
  - Narrator has been run over any screen whose structure changed
- **Record it** in `REVIEW.md` as Pass 9, using the same ID and severity
  scheme.
- **Relative size.** Stage 0 and Stage 2 are the bulk. Stage 2 has eight
  PRs, and areas 2, 6 and 7 are the largest. Stages 1 and 4 are medium.
  Stages 3 and 5 are small.

---

## Appendix A: rendered colour values

The values behind the renders approved on 2 Oct. The source of truth is
[`docs/design-review/mockup/schemes.py`](../design-review/mockup/schemes.py),
which this table is generated from, and the pictures are in
[`docs/design-review/renders/`](../design-review/README.md).

All six pass the floors in section 3.5 (`mockup/contrast.py` checks them),
with one exception: Marble's gold `accent` fill sits at 2.3:1 on white. That
is acceptable because a filled button is identified by its text, and Marble's
focus and checked edges use its bronze `focus`. `syn-attr` uses
`accent-text`. In the high-contrast schemes, `selection` is drawn with
`text-on-accent`. `added` is new and is set in Stage 0. Brick's
`field-border` was raised from `#93675f` after approval, to meet 3:1 against
its raised surface.

| Token | Granite | Brick | Marble | Timber | Obsidian | Quartz |
|---|---|---|---|---|---|---|
| `bg` | `#181b20` | `#1f1311` | `#f4f5f7` | `#efe3cf` | `#000000` | `#ffffff` |
| `surface` | `#1f232a` | `#291715` | `#ffffff` | `#fbf6ee` | `#000000` | `#ffffff` |
| `raised` | `#262b33` | `#4a221c` | `#e9ebef` | `#e3d2b6` | `#0d0d0d` | `#f2f2f2` |
| `alt-row` | `#22262e` | `#251513` | `#f7f8fa` | `#f6efe3` | `#000000` | `#ffffff` |
| `border` | `#363d49` | `#5f322b` | `#d3d7de` | `#cfb894` | `#ffffff` | `#000000` |
| `field-border` | `#6e7582` | `#9a7068` | `#80868f` | `#8b7251` | `#ffffff` | `#000000` |
| `focus` | `#e0a44c` | `#e2a64e` | `#8a5800` | `#2f6146` | `#ffd84a` | `#00308a` |
| `text` | `#e3e7ee` | `#f0e7e3` | `#1c2129` | `#2b2117` | `#ffffff` | `#000000` |
| `text-dim` | `#9ba4b2` | `#c2aaa3` | `#59616e` | `#5c4c3a` | `#e0e0e0` | `#262626` |
| `text-disabled` | `#5d6675` | `#7d5f58` | `#9aa1ad` | `#9a8a74` | `#a0a0a0` | `#6b6b6b` |
| `text-on-accent` | `#201a10` | `#201a10` | `#1f1606` | `#ffffff` | `#000000` | `#ffffff` |
| `accent` | `#e0a44c` | `#e2a64e` | `#d8962f` | `#2f6146` | `#ffd84a` | `#00308a` |
| `accent-hover` | `#efb75f` | `#f0b862` | `#e4a541` | `#3a7356` | `#ffe57a` | `#0040b0` |
| `accent-text` | `#e8b25f` | `#eab562` | `#8a5800` | `#285a3f` | `#ffd84a` | `#00308a` |
| `selection` | `#6e5226` | `#6e3f1e` | `#f6e2bd` | `#cfe2c9` | `#ffd84a` | `#00308a` |
| `ok / changed` | `#7ec699` | `#8fd3a4` | `#1d7a45` | `#17666b` | `#5cff9a` | `#00602a` |
| `warn` | `#f0894a` | `#f39257` | `#b4530f` | `#a8460a` | `#ffa040` | `#7a3300` |
| `error / removed` | `#f07272` | `#ff8fa3` | `#c0312f` | `#a3142a` | `#ff7a7a` | `#a0001c` |
| `rule` | `#7aa7e6` | `#93b6e8` | `#2a5fae` | `#23559c` | `#8ec5ff` | `#5a1d8c` |
| `syn-comment` | `#818c9c` | `#a8948e` | `#6b7280` | `#6f604c` | `#d0d0d0` | `#3d3d3d` |
| `syn-tag` | `#7aa6d6` | `#93b6e8` | `#2257a3` | `#23559c` | `#8ec5ff` | `#00308a` |
| `syn-value` | `#a8d99a` | `#a9d99c` | `#2f7a32` | `#2c6a2c` | `#5cff9a` | `#00602a` |
| `scrollbar` | `#616874` | `#90615a` | `#898d93` | `#917e62` | `#ffffff` | `#000000` |
| `scrollbar-hover` | `#727a87` | `#a3716a` | `#767b83` | `#7d6a50` | `#ffd84a` | `#00308a` |
| `mod.io logo` | Blue_White | Blue_White | Blue_Black | Blue_Black | Mono_White | Mono_Black |
