# Design standards

The rules for everything a person reads, sees or operates in the Studio.
Follow them when you add or change UI. Section 15 is the checklist for a PR.

Paths are relative to `anno_modding_studio/` unless they start with `tests/`,
`tools/` or `docs/`.

The reasoning behind these rules is in
[`docs/proposals/design-review-proposal.md`](../proposals/design-review-proposal.md).
The approved look is in [`docs/design-review/`](../design-review/README.md).

## 1. Who the Studio is for

Two groups of people use the Studio, and most people sit between them.

- **Power users** maintain a long list of mods, or publish several mods meant
  to be used together. They want to know what a button will do: which file it
  writes, which ModOp, in which load order.
- **Newcomers** know what mods are and may already use a mod manager. They
  have probably never edited a mod's XML. They are not beginners at computers
  or at the game.

There is no easy mode and no advanced mode. One interface serves both, in
three layers:

1. **The surface is literal.** Labels use the real terms (ModOp, patch, load
   order, ModID, loadout) and name the action.
2. **The depth is one step away.** A term or option that needs explaining has
   a `?` beside it. The mechanism, the consequences and the edge cases are
   behind it.
3. **Guidance is temporary.** A tab with no content shows what it is for and
   how to start. Once you are working, that text is gone.

## 2. Text

### Three layers of text

| Layer | What it's for | Budget |
|---|---|---|
| **Label** (button, checkbox, radio, tab, menu entry, column) | Names the choice the click makes. A button is a verb and its object. | 1–4 words. Up to 6 where a real term needs them, for example "Export as zip". |
| **Consequence line** (optional, always visible) | Only where a click is irreversible, writes outside the loadout, or does something you would not expect. It states the consequence, not the mechanism. | One sentence, up to about 20 words. |
| **Help** (`?` beside the label) | The mechanism, why it matters, and the edge cases. | Up to about 80 words. Longer text belongs in the user guide, linked from the help. |

A button or checkbox label over 4 words needs a `# design:` comment on its
line giving the reason.

### Plain tooltips

A plain tooltip on a control has four uses only:

- naming an icon-only button
- showing a shortcut
- showing the full text of a truncated cell
- a short clarification of a terse label

A plain tooltip is at most 15 words. Longer text moves behind a `?`.

### Empty states

- A tab or list with no content shows, in one or two sentences, what belongs
  there and how to get it. Where the action exists, it is a button.
- Once there is content, the empty-state text is gone.
- A tab has no explanatory paragraph at the top.
- A page has no heading that repeats the tab's name.

### Literal means literal

- Anything that writes outside the loadout shows where before it writes: the
  folder, the file, or the mod.io page.
- Dialog buttons are named for their action ("Unload 3 mods", "Keep both").
  Yes/No and OK/Cancel are for questions whose action has no name.
  `widgets.ask(parent, title, text, action)` asks such a question: the
  action's name is on one button and Cancel on the other. Name the other
  button too (`cancel=`) where declining is also an action. The action is
  the default unless it destroys something (`default_action=False`).
- A status message uses the button's verb. The Export button leads to
  "Exported Better Bricks to …".
- A button that opens a dialog ends in an ellipsis.

### Question boxes

A message box has no room for a `?`, so its text is where explanations
pile up. Keep them on the buttons instead.

- **The text says what the question is about, and stops.** A sentence or
  two, and the question. At most 40 words, a list of names aside.
- **What an answer does goes on its button,** as a tip: a tooltip that a
  screen reader also reads when the button takes the focus, at most 15
  words. The text never quotes its own buttons ("“Add to loadout” reads
  it…").
- **A consequence stays in the text** where it is the reason to stop and
  think: a change that is lost, something that goes live on mod.io.
- `widgets.ask` takes `action_tip` and `cancel_tip`. `widgets.choose`
  asks between several actions, each `(label, tip)`, with Cancel.
  `widgets.tell` says something with one button. `widgets.tip(button,
  text)` puts a tip on a button of a box built by hand.

| Before | After |
|---|---|
| Custom Walls is somebody else's mod, so the Studio does not change it. What you want different about it goes into a mod of yours that loads after it. A patch is a mod of yours that holds your changes to Custom Walls wherever you use it… *(125 words, the name four times)* | **Custom Walls** is somebody else's mod, so your changes go into a mod of yours. Which one? *(each button's tip says where that leads)* |

### Names in text

A name the reader did not write in this sentence (a mod's, a loadout's, a
collection's, an author's) runs on with the words around it and is often
long.

- **Once per block of text.** Say the name once, then "it", "the mod" or
  "the original".
- **In the title or the text, not both.** Prefer the text: a window title
  is plain.
- **In a question box, set it apart** with `widgets.named(value)`, which the
  box shows in the heavier weight. Put it in through `tr`'s values, so no
  translated sentence holds markup. Only a box shown through `ask`,
  `choose`, `tell` or `show_box_text` understands it. A title, a status
  line or a tooltip takes the name as it is.
- A value the reader has to type or compare, such as a path to paste into
  Steam, may be said twice. Say why in a `# design:` comment.

### Keywords

A few terms the Studio uses in one sense only are **keywords**: patch, fork,
project, generated copy and live. `core/keywords.py` holds what each means,
said to the reader in a sentence or two that starts with the term. The
glossary in section 14 has a row for each.

- **Mark a keyword where a sentence uses it in that sense:** `[[patch]]`, or
  `[[patch|patches]]` where the words shown are another form of it. Mark
  every time it is said. "Where your mods live" and "the loader's patches"
  are not the keyword, and are not marked.
- **A box and a `?` panel show it** as a link in the accent colour. Resting
  the mouse on it, or clicking it, opens what it means in a panel like a
  `HelpTip`'s. Enter opens it from the keyboard, and Esc closes it. A screen
  reader hears the meanings after the text.
- **Put the sentence through `tr_kw`** (or `trn_kw`) where it goes to a box
  or a `HelpTip`. Plain `tr` shows the words without the mark, so a marked
  sentence can also go to a status line, a tooltip or a label. A label,
  title or tooltip given `tr_kw`'s text shows the marks. The window walk
  fails on it (`marks-shown`).
- **A sentence that marks a keyword does not explain it again.** Say what
  this case adds: why the change goes to a patch here, not what a patch is.
- A button, a menu entry or a window title cannot show a keyword. Its tip
  says what the click does, as it would anyway.

## 3. Voice

Write the way a knowledgeable person explains something to a friend sitting
beside them.

- **Lead with the outcome.** Say what happens, then why if the reader needs
  it.
- **Use second person, present tense, active voice.** "You", not "the user".
- **Keep sentences short.** One idea per sentence, usually under 20 words.
- **Use sentence case everywhere,** including titles and buttons.
- **Errors** say what happened and what to do next, in that order. They don't
  apologise.
- **Use one word for one thing.** The glossary in section 14 lists each term
  and the words not to use for it.
- **German** keeps the du-form and the rules in
  [`docs/standards/translation.md`](translation.md).

Avoid these in UI text:

| Avoid | Do this |
|---|---|
| Em-dashes and `--` | Use a full stop, a comma or brackets. |
| Semicolons | Split the sentence. |
| "rather than" and "not X but Y" contrasts | Say what it does. |
| Reassurance openers ("Nothing is…") | State the specific fact, such as "Your mods folder isn't changed". |
| Giving the app a personality | The Studio *shows*, *checks* or *writes*. It doesn't *say*, *ask*, *tell*, *know* or *decide*. |
| Punchline closers, aphorisms, lists of three for rhythm | Stop when the instruction is complete. |
| Filler words: "exactly", "simply", "just" | Delete them. |

### Other tools

The Studio is one tool among several the modding community has built, and
many people use more than one. Text about another tool (iModYourAnno, a mod
manager, the game's mod browser) follows these rules:

- **Another tool being installed is never a warning.** Don't tint it `warn`
  or `error`, and don't open a dialog because one was found.
- **Say what both tools do to the same files,** and where the Studio shows
  the result. Leave the choice of tool to the reader.
- **Never ask the reader to switch to the Studio,** to stop using the other
  tool, or to make the Studio their default for anything. Don't compare the
  two.
- **Mention another tool only where it changes what a click does.** Say it
  once, at that click.
- **Name the tool by the name its author gives it,** and credit a format or
  an idea to the tool it came from.
- **Leave the other tool's own files alone.** The Studio reads what a tool
  leaves in a mod's folder. It doesn't write a tool's settings or records.

| Before | After |
|---|---|
| Its profiles switch mods by renaming folders, which would fight the Studio's links: after this, switch mods with Play. | It switches a mod by renaming its folder, and the Studio switches a converted mod by its link. The fourth card shows when the mods folder has changed since Play. |
| After this, use it to browse mods, not to switch them. | It and Play write the same profile and options files, so the game loads what the last one to write set. |

### Examples

| Before | After |
|---|---|
| Until this is set, every mod loaded counts as somebody else's — so the Studio offers a patch rather than editing one. | Mods with this author name are yours, and the Studio edits them directly. Other mods get a patch. |
| What the Studio remembers about you and this machine. None of it belongs to the mod you are working on, and all of it is still here the next time you open one. | *(removed: the dialog's title covers it)* |
| ☐ This is my own mod — carry on with it under its name, ModID and version | ☐ **This is my mod** `?` *(Help: keeps its name, ModID and version, and edits it directly instead of making a patch.)* |
| ☐ Header comment (mod details, game version, counts) | ☐ **Header comment** `?` |
| Revert all changes to this asset | **Revert asset** |

## 4. Help icons and tooltips

### `HelpTip`

Every `?` is one widget, `HelpTip` (`ui/help_tip.py`). It sits beside the
label it explains.

- **Drawing.** A circled question mark one text-height tall, in `text-dim`.
  It takes the `focus` colour on hover and focus.
- **Keyboard.** It takes focus with Tab. Space, Enter or F1 opens it. Esc
  closes it.
- **Popup.** It opens on hover after a short delay, or on click. It is a
  floating panel, not a `QToolTip`. It wraps at about 45 characters a line.
  You can select and copy its text. It stays open until the mouse has left
  both the icon and the panel, or until you press Esc. It never times out.
- **Position.** It opens next to the icon, where the focus and a magnifier
  already are.
- **Links.** It can end with a "More in the guide" link to a section of the user guide.
  Use the link where a topic needs more than about 80 words.
- **Screen readers.** Its accessible name is "Help: \<label\>". Its
  accessible description is the help text, so a screen reader reads it
  without opening it.

Write a help text once. Where two dialogs explain the same thing, both use
the same `HelpTip` text.

### The tooltip filter

Plain tooltips never time out. An event filter on the application handles
`QEvent.ToolTip` for plain widgets and for item views (through `indexAt` and
`ToolTipRole`), and shows the text with no expiry. The tooltip hides when the
mouse leaves or a key is pressed.

The filter also wraps the text at about 80 characters. Qt shows a line of a
plain tooltip as one line, however wide.

A row of a list or tree always waits the full delay for its tooltip. Qt shows
a tooltip at once when another was up in the last two seconds, which down a
list is one for every row the mouse crosses. Buttons and tabs keep Qt's
behaviour.

You don't need to do anything to get this. Call `setToolTip` as usual, or
return the text for `ToolTipRole` from a model.

### Row tooltips

A row in a list or tree may carry more than 15 words, because it has no room
for a `?`. It still has to be read at a glance:

- It says what the row cannot. It does not repeat the row's own columns, the
  row above it, or what is true of every row of its kind.
- Anything of unbounded length is cut and says so: a path or pattern from the
  middle (`keptops.clipped`), a block of XML or a mod's comment to its first
  lines (`change_rows.first_lines`), a list of names to the first three and a
  count.
- Each fact is one short paragraph. What needs more goes in the dialog the row
  opens.

## 5. Containers

- **Cards** (`WayCard`, `StepCard`) mean "these are alternatives, or steps in
  order". Cards are drawn as cards in every drawing style.
- **Group boxes** mean "these settings belong together". The Outlines setting
  draws them as boxes (Boxed) or as a heading over a dividing line (Flat).
  The meaning is the same in both.
- **No container is nested for decoration.** A card holds content. It doesn't
  hold other cards that are only there for looks.
- **Form labels are left-aligned.** They sit above or beside their field, the
  same way throughout a dialog. Help and consequence lines align with the
  field they belong to.
- **Spacing** uses a 4, 8, 12, 16 and 24 px scale. Dialog margins are 16.
  Standard spacing is 8.

## 6. Colour schemes and drawing style

A theme has two independent parts:

- **A colour scheme** is a set of values for the semantic tokens below, and
  nothing else.
- **A drawing style** is how containers and spacing are drawn. Any style
  works with any scheme.

### Tokens

Every colour is a semantic token in `ui/theme.py`. Code asks for what a
colour means, never for a hex value.

| Group | Tokens |
|---|---|
| Surfaces | `bg`, `surface` (fields, lists), `raised` (headers, buttons), `alt-row` |
| Lines | `border` (decorative), `field-border` (where a control starts), `focus`, `scrollbar`, `scrollbar-hover` |
| Text | `text`, `text-dim`, `text-disabled`, `text-on-accent` |
| Accent | `accent` (fills), `accent-text` (the accent used as text), `accent-hover`, `selection` |
| Status | `ok`, `warn`, `error`, `rule` |
| Changes | `changed`, `removed`, `added` |
| XML preview | `syn-comment`, `syn-tag`, `syn-attr`, `syn-value` |

`ui/theme.py` is the source of truth for the token list and for every value.
The approved renders are in `docs/design-review/renders/`.

- A status or change colour always comes with a glyph or a word. Colour is
  never the only signal.
- `focus` is the accent, or a darker shade of it where the accent fill is too
  light to reach 3:1 against the background.
- `rule` is the colour for patterns in the glossary's sense. The token keeps
  the word the glossary used before "pattern", and has no link to drawn lines.

### The accent has one meaning

The accent marks something you can act on, or what is current. That covers
the primary button, focus, the current tab, the checked state, selection and
links.

- The accent is never used for a warning. Warnings use `warn`.
- The accent is never used for decoration.
- Asset titles and mod headings use weight, not colour.

### The six schemes

| Scheme | Description |
|---|---|
| **Granite** | Blue-grey dark with Anno gold. The default. |
| **Brick** | Dark, with brick red strongest in the raised bands: tabs, headers, buttons and the status bar. Anno gold is the accent. `error` and `removed` are a lighter, pinker red than any surface. |
| **Marble** | Light, a cool white. Gold fills the primary button. Bronze is used wherever the accent is read as text, and for focus. |
| **Timber** | Light, on pale pine wood, with a deep pine-green accent. Changed values are teal-green to stay apart from it. |
| **Obsidian (high contrast)** | Black, white and yellow, with every boundary drawn. |
| **Quartz (high contrast)** | White, black and a deep blue accent with white text on it, with every boundary drawn. |

**Naming.** A colour scheme is named after a product from an Anno game. This
is a guideline, not a rule. Older games such as 1404 or 2070 count, and the
name only needs to be close: "Quartz" is fine, though the in-game product is
quartz sand. A high-contrast scheme says so in its name.

### Drawing style

| Setting | Options | What changes |
|---|---|---|
| **Outlines** | **Boxed** (default) · **Flat** | Boxed draws group boxes, lists and headers with borders. Flat separates them by tone and a dividing line. Cards stay cards in both. |
| **Density** | **Comfortable** (default) · **Compact** | Padding, row height, gaps and corner radius. Compact suits a long list of mods. Text size doesn't change. |

- Each option is a small block of style rules plus metric values (`radius`,
  `pad`, `row-pad`, `gap`).
- A style block never sets a colour. This is what lets any style combine with
  any scheme.
- Flat draws its dividing lines at the 3:1 floor, so Flat stays readable in
  the high-contrast schemes.

### Appearance settings

The settings sit under *Appearance*:

- **Colour scheme:** Match Windows · Granite · Brick · Marble · Timber ·
  Obsidian (high contrast) · Quartz (high contrast)
- **Outlines:** Boxed · Flat
- **Density:** Comfortable · Compact
- **Interface size:** see section 7

How they behave:

- **Defaults.** Granite, Boxed, Comfortable, 100%.
- **Match Windows** picks Marble when Windows is in light mode and Granite
  when it is in dark mode.
- **Windows contrast themes win.** When one is on, the Studio uses the
  contrast theme's own colours, whichever scheme is picked. People who turn
  a contrast theme on may need those colours. A light contrast theme stays
  light. The Colour scheme setting shows that this is happening. A Windows
  palette has no status colours, so status is drawn in the text colour and
  its glyph or word carries the meaning. Outlines and Density still apply.
- **No restart.** Schemes and styles apply at once. Only Interface size needs
  a restart.

### Contrast floors

These apply to every scheme:

| Pair | Floor |
|---|---|
| Text on its background, including selected text and the XML preview | 4.5:1 |
| Dim text on its background | 4.5:1 |
| Text in a high-contrast scheme | 7:1 |
| Field borders, focus indicators, checkbox borders, scrollbar handles, Flat's dividing lines | 3:1 |

A filled accent button is identified by its text, so the fill has no 3:1
floor of its own. Its text meets 4.5:1, and focus and checked edges use
`focus`.

### Images

Every image is SVG. The app icon is the one exception.

- **Indicators.** The checkbox, radio, spin-arrow, tree-branch and `?` images
  are SVG templates filled from the scheme's tokens. They are written to the
  cache folder when a scheme is applied. A template uses tokens only.
- **mod.io logo.** The four versions from mod.io's press kit are in
  `resources/modio/`. Each scheme names the one that suits its background.
- **No raster images** are added under `resources/`.

## 7. Interface size

**Interface size** has seven steps: 90%, 100%, 110%, 125%, 150%, 175% and
200%. The default is 100%.

- **How it works.** It sets Qt's scale factor (`QT_SCALE_FACTOR`) before the
  application starts. Everything scales together: text, padding, indicators,
  custom drawing, and every fixed size in the code.
- **Restart.** It takes effect after a restart, and the setting shows that.
- **The game.** The variable is removed from the environment once Qt has read
  it, so the game doesn't inherit it when Play starts the game.
- **Body text** is 14 px at 100%.
- **Custom drawing** works in logical pixels and stays sharp at fractional
  scales. Don't draw on half-pixel offsets that assume a scale of 1.
- **Fonts** in custom drawing follow the application font. Don't set a fixed
  point size.

## 8. Accessibility baseline

| Area | Requirement |
|---|---|
| Keyboard | Everything you can do with the mouse, you can do from the keyboard. Enter does what double-click does in every list. Ctrl+1–4 switch the main tabs. Buttons, checkboxes and labelled fields have mnemonics, unique within their dialog. |
| Focus | Every focusable control draws a focus indicator at 3:1 or better. A button, checkbox, radio button or tab that got the focus from a mouse click draws it from the first key pressed on it, and not before. A field or a list always draws it. Focus is never thrown away. A button doesn't disable itself to show feedback. |
| Names | Every focusable control has an accessible name, from its own text, a buddy label, or `setAccessibleName`. Icon-only controls always set one. A "Label:" beside a field is that field's buddy. |
| Colour | No state is shown by colour alone. Errors, warnings, removals and conflicts each carry a glyph or a word. |
| Tooltips | Essential text is on screen or behind a `HelpTip`, which the keyboard can reach. A plain tooltip never holds the only copy of it. |
| Reference graph | Arrow keys move between nodes. Enter moves the graph to the node the keyboard is on. Ctrl+Enter opens it in the editor. The menu key or Shift+F10 opens its menu. A screen reader is told what each node is and how it links as the keyboard reaches it. |
| Names for Windows | Every control tells Windows its name, its kind and its state, which is what screen readers, voice control and Magnifier read. The window walk checks the names. A report from someone using one of them is fixed like any other bug. |

In code:

- Connect a list's action to `itemActivated` or `activated`, never to
  `itemDoubleClicked` or `doubleClicked`. A double-click emits it too. Call
  `widgets.enter_activates(view)` beside it, which keeps Enter from going on
  to the dialog's default button.
- Show a status on a label with `theme.tint(label, "warn")`, and on a list,
  tree or table item with `theme.mark(item, "warn")`. Both add the glyph.
  The statuses are `ok`, `warn` and `error`. Don't give an item the warning
  or error colour with `setForeground`.
- Confirm a silent action with `widgets.flash_button_feedback(button)`. It
  leaves the button enabled, so the focus stays on it.
- Give a `QDialogButtonBox` that has a button added to it
  `widgets.tab_as_laid_out(box)`, once the buttons are in. Otherwise Tab
  goes through them in the order they were made, so Cancel comes before
  the button on its left.
- Add a control to a window that is already built with
  `widgets.add_in_tab_order(layout, widget)`, or put it in place with
  `widgets.tab_after(before, widget)`. Otherwise Tab reaches it after
  everything else in the window.
- Switch Tab off in a table with `table.setTabKeyNavigation(False)`. Tab
  then leaves the table, and the arrow keys move in it. A table made with
  `widgets.rows_table(headings)` has it off already.
- Give a label a buddy with `setBuddy`. `QFormLayout.addRow(str, field)`
  makes the label and sets the buddy for you. Put the mnemonic in the
  string: `form.addRow(tr("&Name"), edit)`.

### Checking a screen with Narrator

No screen is checked with a screen reader on a schedule. Someone who needs one is
unlikely to play Anno, and so to mod it. What a screen reader reads, voice control
and Magnifier use as well, and the window walk checks that much (section 13). This is
for a report from someone who uses a screen reader, or for a screen the walk cannot
judge, such as the reference graph.

Start Narrator with Ctrl+Win+Enter and use the keyboard only. Tab through the screen.
Each control is reached once, in reading order, and Narrator says its name, its kind
and its state, such as checked or unavailable. A field's name is its label. "Button"
or "edit" alone is a failure. Each `?` reads its help when it has the focus. In a list,
the arrow keys read each row. Esc closes a dialog, and the focus goes back to the
control that opened it.

## 9. Help

The Studio has no in-app help pages. The Help menu holds:

- **Keyboard shortcuts.** A dialog generated from the app's actions, so it
  always matches them. A `QShortcut` is listed when it has a `shortcut_name`
  property. Keys a widget answers in its own `keyPressEvent` go in
  `shortcuts_dialog.WIDGET_KEYS`.
- **User guide.** Opens the local `docs/guide.md` with the system's default
  handler. If that fails, it shows the file's folder.
- **Studio folders.** Lists every folder the Studio uses, as it is on
  this PC: what you make, the library and the mods folder, what can be
  made again, the settings and the program folder. Each says whether to
  back it up or that it is safe to delete, has a `?` saying what it holds,
  and opens. A place another screen explains has that screen's help text.
  The Start tab's third card opens the same dialog. A status message about
  something put in a folder carries *Show in folder* until the next
  message.
- **Glossary.** Opens the guide's glossary section. Not in the menu until
  the Studio has a public home: a local Markdown file cannot be opened at a
  heading.
- **About.** Says the Studio's version, what it does, who owns the mod.io
  name, its licence, that it is not affiliated with Ubisoft or mod.io, and
  links to the third-party notices, the credits and the source. *Copy
  version info* copies what a bug report needs. It has no heading, since its
  title names it.

A `HelpTip` links to a section of the user guide where a topic needs more than about 80
words.

## 10. Typography

- **Faces.** Segoe UI, or Segoe UI Variable where Windows 11 has it. Consolas
  for paths, GUIDs and XML.
- **Sizes at 100%.** 13 px for hints and secondary text, 14 px for body,
  16 px for section headings, 19 px for the asset title.
- **Weights.** 400 and 600 only.
- **No all-caps.**
- **No colour for emphasis.** Use weight.

## 11. Adding a colour scheme

1. Add a new set of token values to `SCHEMES` in `ui/theme.py`. Define
   every token in `TOKENS`. You can leave out `changed`, `removed` and
   `syn-attr`, which default to `ok`, `error` and `accent-text`.
2. Name the mod.io logo version that suits the background.
3. If the scheme is high contrast, mark it so and say so in its name.
4. Follow the naming guideline in section 6.
5. Run `tests/test_design.py`. The colour scheme tests run for every scheme
   in the registry, so the new one is checked with no new test.
6. Run `tools/render_themes.py` and look at the result.

A scheme needs no style rules. If you find yourself writing one, the colour
belongs in a token.

## 12. Using colour in code

- **Name the token.** `theme.<TOKEN>` names resolve to the current scheme.
  The name is the token in capitals with underscores: `theme.WARN` is
  `warn` and `theme.FIELD_BORDER` is `field-border`. `theme.colour("warn")`
  does the same from a string.
- **Tint a widget** with `theme.tint(widget, "warn")`. Don't give a widget an
  inline style sheet. The tones are `text`, `text-dim`, `accent-text`, `ok`,
  `warn`, `error` and `rule`. `theme.tint(widget)` puts the usual colour
  back, and `strong=True` makes the text semi-bold.
- **Items in lists, trees and tables** take `QColor(theme.<TOKEN>)` as their
  foreground. A scheme change moves them to the new scheme's colours.
- **Anything else that keeps a colour** connects to
  `theme.notifier().changed` and reads its colours again: an icon drawn in
  code, a syntax highlighter, a graphics scene's background. Custom painting
  that reads `theme.<TOKEN>` as it paints needs nothing.
- **Spacing and corner radius** come from `theme.metric("gap")` and the other
  metrics of the density in effect.
- **Never write a hex literal** outside `ui/theme.py`. The same goes for
  `QColor("…")` with a literal and for named colours.
- **Never call `setStyleSheet`** outside `ui/theme.py`. Every colour comes
  from the application's theme.
- **Pick the token by meaning.** Use `accent-text` where the accent is read
  as text, and `accent` for fills.

## 13. The checks

`tools/check.py` runs the checks. Run it before a change goes in.

### Hard rules and the one count

- A **hard** rule fails at any finding. Every rule below is hard but one.
  Each began as a count that could only go down, and became hard when it
  reached zero.
- The one count left is the window walk's buttons without a mnemonic. It is
  recorded under `"walk"` in `tools/check_baseline.json`, and a change fails
  if it grows. When a change brings it down,
  `python tests/test_design.py --record` writes the lower count back.

### Static checks: `tools/design_check.py`

Run by `python tools/check.py --quick`. It reads the source's syntax tree.
Each rule covers `ui/` and, for strings, `core/` too. Every rule is hard:
the check prints each finding with its rule, file and line, and fails.
`python tools/design_check.py <rule>` lists one rule's findings.

| Rule | Notes |
|---|---|
| A colour literal (`#rrggbb`, `QColor("…")`, a named colour) outside `ui/theme.py` | |
| `setStyleSheet` outside `ui/theme.py` | |
| `setForeground` with the warning or error colour | It shows a status by colour alone. `theme.mark` sets the colour and the glyph together. |
| A raster image under `resources/` (anything but `.svg` and the app icon) | |
| `itemDoubleClicked` or `doubleClicked` connected without `itemActivated` or `activated` | |
| A `QMessageBox` with Yes/No or OK/Cancel buttons | Ask through `widgets.ask`, with the action's name on the button. |
| A button or checkbox label over 4 words, or a `HelpTip` over 80 | An exception needs a `# design:` comment with the reason. Consequence lines are not checked. |
| `setToolTip` with literal text over 15 words | Also a button's tip in a question: `tip`, a `choose` choice's, `ask`'s `action_tip` and `cancel_tip`. |
| A message box's text over 40 words, where the source spells it out | `ask`, `choose`, `tell`, `show_box_text` and the static `QMessageBox` calls. An exception needs a `# design:` comment. |
| Text that quotes, in curly quotes, a button the same function makes | Say what the button does on its tip. |
| A marked string that fills the same named value in twice (`%(mod)s … %(mod)s`) | A value inside markup is not counted. An exception needs a `# design:` comment. |
| Voice patterns in UI strings: em-dash or `--`, semicolon, "rather than", a sentence opening "Nothing", the Studio as subject of says/asks/knows/tells, "exactly", "simply" | |
| A `QLabel` ending in ":" beside a field without a buddy, or a `QFormLayout.addRow(str, …)` label without a mnemonic | A caption for a value that is only read has no field: it takes a `# design:` comment saying so, and a form row uses `widgets.shown_row`. |

### The window walk: `tests/test_design.py`

The test builds the main window and every dialog, through the registry of
dialog factories in `tests/design_dialogs.py`. Then it walks every widget.
Then it shows each one and uses the keyboard on it: Tab from the first
control round to it again, and Esc in a dialog.

| Check | Kind |
|---|---|
| Every focusable widget has an accessible name | hard |
| No plain tooltip is over 15 words at runtime | hard |
| Every `HelpTip` can take focus and has an accessible description | hard |
| No widget carries a style sheet of its own | hard |
| No widget shows the marks of a name or a keyword (`widgets.named`, `tr_kw`) in its text, title, tooltip or description | hard |
| No mnemonic repeats within a dialog | hard |
| Tab from the first control reaches every shown, enabled control, each once | hard |
| Tab leaves every control at the first press. A label with links keeps it for one press per link, and no table moves through its cells on Tab | hard |
| Tab goes in reading order: down the window, and left to right along a row | hard |
| Esc closes every dialog | hard |
| Every button, checkbox and labelled field in a dialog has a mnemonic | counted |

Reading order is judged where two controls part: a control on a scrolled
page is where it is on the page, and a panel beside the page is a column of
its own. Controls switched off are in it too, since they are switched on
another day.

What the walk does not ask for:

- **A name for a part of a control.** The field inside a combo box or spin
  box, and the list a combo box drops down, are named by the control.
- **A name for a hidden button with no text.** It is given its text as it is
  shown, like the changes panel's "3 conflicts".
- **A mnemonic for Qt's own OK, Cancel and Close.** Enter presses OK, and Esc
  presses Cancel or Close.
- **Where the focus goes after Esc.** It should go back to the control that
  opened the dialog. A factory builds the dialog on its own, without the
  menu item or button that opens it, so the walk has nothing to check that
  against.
- **The main window's other tabs.** Only the tab open when it starts is
  shown, so Tab goes through that one alone. Their tables are still checked
  for moving through cells on Tab.

**A new dialog goes into the registry.** The test fails for a dialog class
that has no factory and is not listed there as waiting for one. A dialog
listed as waiting needs more of a scenario than a bare window gives it, and
neither the walk nor the renders cover it until it has a factory.

### Colour scheme tests: `tests/test_design.py`

These run for every scheme in the registry. They also run against two schemes
built from sample Windows contrast palettes, one light and one dark.

- Every scheme defines every token.
- Every pair meets the contrast floors in section 6. The Windows samples are
  held to 4.5:1 for text, because their colours are Windows' own.
- `warn`, `error`, `removed`, `changed` and `accent` differ by hue from each
  other and from the scheme's surfaces, not only by lightness.
- Every SVG template renders without error and fills from tokens only.
- A drawing style block sets no colour.

### Renders: `tools/render_themes.py`

The renders are for review by eye. They are not a gate.

It renders the four main tabs, every registered dialog, and the asset editor
over two loaded mods that set one field (`5_stack`, with that row's menu, and
`6_xml`, the same asset in the XML view) into a folder with an HTML contact
sheet:

- every colour scheme, with the default style
- Flat and Compact, on Granite and Marble
- 100% and 200%
- English and German

`python tools/render_themes.py <folder> --every` renders every scheme as it
comes, Flat and Compact, each at 100% and 200%. Use it to review the whole
look, as when a scheme or a style rule changes.

`python tools/render_themes.py <folder> --compare` renders the six schemes
in the approved scenario and compares each picture with its reference in
`docs/design-review/renders/`, pixel for pixel.

The renders are looked at once per release, on the release branch
([release-checklist.md](release-checklist.md)). German strings run about 30%
longer than English, so look at the German renders for clipping.

## 14. Glossary

Use one word for one thing. This table is where each term's meaning is
written, for whoever writes the text. Two other documents carry the terms and
follow it:

- The guide's "Words you'll see" section gives the reader the same meanings,
  shortened. It is a copy because the user guide has to stand alone. Change a
  meaning here first, then there.
- [architecture.md](../architecture.md), section 3, says where each term is
  in the code and defines the words only the code uses. It doesn't repeat the
  meanings.

The German column gives the term `locale/de.po` and `docs/standards/translation.md`
have settled on.

| Term | What it means | Don't use | German |
|---|---|---|---|
| **ModOp** | One patch operation in a mod's XML, applied when the game loads its data. It has a type (replace, add, remove, merge) and a path to the nodes it targets, and it can change any game file the loader patches, not only assets. | operation, instruction, command, tweak | die ModOp (never translated) |
| **patch** | A mod of your own that loads straight after someone else's mod and holds your changes to it. An update to the original leaves the patch in place. | override, fix, tweak, fork | der Patch |
| **fork** | A copy of a loadout under a new name, to try something in without changing the original. A new profile is the other way to try something. | copy (alone), branch, duplicate | abzweigen, der Abzweig |
| **your own copy** | Someone else's mod made into a mod of yours, in its place, to change directly. It gets a ModID of its own, and its author's updates no longer reach it. | fork, clone, copy (alone), take over | deine eigene Kopie |
| **load order** | The order in which the game's loader applies mods. It decides which of two mods the game obeys. It is not the folder order. | priority, sort order, folder order | die Ladereihenfolge |
| **ModID** | The identifier in a mod's modinfo. The loader and the Studio identify a mod by it. Other mods name it to load after it. | mod name, folder name, GUID, ID | die ModID |
| **mods folder** | A folder the game loads mods from. A game can have more than one. | mod directory, mods directory, install folder, library | der Mod-Ordner |
| **library** | The folder where the Studio keeps the mods it installs, and the mod folders of your own a conversion moves in. The mods folder holds a link to each one. It isn't a cache: it can hold the only copy of a mod. | cache, store, repository, mods folder | die Bibliothek |
| **generated copy** | A library mod with something of the Studio's written into it, built beside the library and linked in place of the library's copy, which stays as its author shipped it. Built again whenever that changes; nothing anybody wrote is in one. | build, output, cache, fork, patched copy | die generierte Kopie |
| **loadout** | What the Studio opens: the mods you have open, in load order, which of them are on, what your changes come to, and the history of its list. What it has on is what the game loads when you press Play. A loadout can hold one mod alone: the one you are writing. Each mod of yours in it is kept in its project. | modlist, preset, set, profile, collection, workspace, session, save file | das Loadout |
| **project** | The file a mod of yours is kept in: its details, its ModOps, its history, its builds and its mod.io page. Every loadout that has the mod refers to its project, so an edit goes there whichever loadout it is made in. It is opened through a loadout, never on its own. | workspace, mod file, source, owner | das Projekt |
| **profile** | Within a loadout, a named record of which of its mods are on. A loadout has one list of mods and any number of profiles. Anno 117 and iModYourAnno keep profiles of their own, and the word means the same there. | preset, configuration, loadout | das Profil |
| **collection** | A set of mods somebody curated on mod.io. Say "mod.io collection" where it could be taken for a collection folder. | modpack, pack, bundle, loadout | die Sammlung |
| **collection folder** | The loader's term: a folder in the mods folder that holds several mods. It is not a mod, and has no ModID and no activation of its own. | collection (alone), pack, group | none yet |
| **pattern** | One edit aimed at every asset that matches a search, not at a list of assets. You can write one in the Studio, and the Studio reads one from a mod. It exports as a single ModOp, so it also covers matching assets that other mods add. The changes listed under it are what it comes to against the assets loaded now. | rule, filter, query, search | das Muster |
| **kept ModOp** | A ModOp held as it was written, with the changes it comes to against the assets loaded now listed under it. One that is on goes out as itself. Its row is marked for what the game works out as it loads: a pattern (which assets or nodes), a computed value, a condition (whether it runs). | rule, kept op, original op | die behaltene ModOp |
| **mod file** | The file a mod is exported as: its ModOps, in the order the game runs them. The **Mod file** tab holds the one of the mod your changes go to. | patch file, assets file, source | die Mod-Datei |
| **author** | Who made a mod: the `Creator` in its modinfo, and the name you publish under. A mod with one of your author names is yours: the one new mods get, or another you have published under. Which file keeps a mod is never put as a word of its own: say its loadout, or the library's copy. | creator (except naming the modinfo's `Creator` field), maker, owner, source | der Autor |
| **your mod** / **other people's mods** | A mod is yours when the Studio wrote its ModID, or when its author name is one of yours. The Studio edits your mod directly. Other people's mods get a patch, or, in the library, are edited directly, with the edits written into a generated copy. | my own mod, foreign mod, third-party mod, upstream | deine Mod / die Mods anderer |
| **game files** | The game's own data, which the Studio reads from the game's `.rda` archives. Everything is built against them. | game data, vanilla, base game, assets.xml (as a label) | die Spieldateien |
| **Install** | Puts a mod into the library and links it into the mods folder: downloaded from mod.io, or unpacked from a zip dropped on the window. "Download" is only the transfer: a size, a progress line, a failed download. | get, fetch, download (as the action), import, add | installieren |
| **convert** | Puts the mods the game already loads into the library and leaves a link to each in the mods folder. Your own folders move, and the mods the game's mod browser installed are copied. "The conversion" is one run of it, and it can be undone. | migrate, adopt, bring in, move in, import | umstellen, die Umstellung |
| **Export** | Writes a mod out as a mod folder: its ModOps, its `modinfo.json`, and everything else the mod shipped. Also the tab where you do it. | save, build, write out, publish | Export (the tab), exportieren |
| **Play** | Makes the loadout what the game loads, then starts the game. |
| **live** | The live loadout is the one the game loads now: Play, or Make this live, wrote its switches into the mods folder. "Make live" is the step without starting the game. | active, current, applied | live (das Live-Loadout, live schalten) | launch, run, start game | Spielen. In running text „Spielen“, in quotation marks |
| **colour scheme** | A set of values for the colour tokens. A theme is a colour scheme plus a drawing style. | theme, skin, palette | das Farbschema |
| **Interface size** | The setting that scales the whole interface, from 90% to 200%. | text size, font size, zoom, scale | die Oberflächengröße |

Related words that are easy to mix up:

- **Save** stores the loadout. **Export** writes a mod folder. **Publish**
  sends a mod to mod.io.
- **Install** puts a mod from mod.io, or from a zip, into the library.
  **Import** reads a mod that is already on disk into the loadout.
  Installing a mod imports it afterwards.
- **Install** brings a new mod into the library, from mod.io or from a zip.
  **Convert** is for mods that are on disk already, in the mods folder or
  the game's mod browser's folder. "Copy to library" and "Move to library" name what
  converting does to one place's mods.
- **base game** (das Basisspiel) is the game with no mods loaded. It is not
  another word for the game files.
- **Patch** with a capital is also the third part of a version number
  (Patch-Version in German). Give a translator a context where the two could
  be confused.
- **fork** (abzweigen) is for a loadout, never for a mod. Someone else's mod
  made yours is **your own copy**, though the code calls it a fork
  (`forking`).
- A mod **adds** a field the asset didn't have (hinzugefügt) and **sets** one
  that is there (gesetzt), whoever put it there. It **restates** a value
  (erneut gesetzt) when an earlier mod already set the field to it.

Where the Studio's words differ from the mod loader's reference
(https://jakobharder.github.io/anno-mod-loader/, checked 3 Oct 2026):

- **patch.** The loader's reference uses "patch" for what any ModOp does to
  a game file: "a single patch operation", "asset XML patches", "patching
  .cfg files". The Studio's patch is narrower: a mod of yours that changes
  someone else's mod. Keep "patch" for the Studio's meaning in UI text. For
  the loader's meaning, write "ModOp" or "change".
- **load order.** The reference calls it "loading order". It means the same
  thing. Mods load in two phases, ordered within a phase by what each lists
  under LoadAfter. Mods with no dependency information load alphabetically,
  and the reference says not to rely on that.
- **mods folder.** The reference says "mod folders" for the places the game
  loads mods from, and "mod folder" for one mod's own folder. The Studio
  says "mods folder" for the first and "mod folder" for the second.
- **on and off.** The reference says a mod is enabled, disabled or active,
  and calls the record its activation. The Studio says on and off.

## 15. Checklist for a PR that adds or changes UI

The checklist for every PR is [pr-checklist.md](pr-checklist.md). This is the part of
it for the interface.

Text:

- [ ] Labels are 1–4 words. Buttons are a verb and its object.
- [ ] Explanations are behind a `HelpTip`, at most about 80 words.
- [ ] Plain tooltips are for the four uses in section 2, at most 15 words.
- [ ] A consequence line appears only where section 2 calls for one.
- [ ] An empty tab or list shows what belongs there and how to get it.
- [ ] Dialog buttons name their action.
- [ ] A question box says what it is about in a sentence or two. What each
      answer does is on its button's tip.
- [ ] A name is said once per block of text, in a box through
      `widgets.named`.
- [ ] Anything that writes outside the loadout shows where first.
- [ ] The text follows the voice rules in section 3.
- [ ] Every term matches the glossary.
- [ ] Text about another tool follows "Other tools" in section 3.

Look:

- [ ] No hex literal, `QColor` literal or `setStyleSheet` outside
      `ui/theme.py`.
- [ ] The accent marks only what you can act on or what is current.
- [ ] Status and changes carry a glyph or a word beside the colour.
- [ ] Spacing is on the 4, 8, 12, 16, 24 scale. No container is nested for
      decoration.
- [ ] New images are SVG, filled from tokens.
- [ ] Custom drawing uses logical pixels and the application font.

Keyboard:

- [ ] Everything works from the keyboard. Enter does what double-click does.
- [ ] Every focusable control has an accessible name.
- [ ] Buttons, checkboxes and labelled fields have mnemonics, unique within
      the dialog. Labels have buddies.

Checks:

- [ ] A new dialog is in the registry in `tests/test_design.py`.
- [ ] `python tools/check.py` passes, with no design finding.
- [ ] `python tools/untranslated_widgets.py` is clean.
- [ ] `python tools/translations.py update` has run, and the changed strings
      are translated again under `docs/standards/translation.md`.

The renders are looked at once per release, not in each PR
([release-checklist.md](release-checklist.md)).
