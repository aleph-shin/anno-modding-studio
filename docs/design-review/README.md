# Design review: approved look

The colour schemes approved during the [design review](../proposals/design-review-proposal.md)
on 2 Oct 2026, as code and as pictures. Stage 0 builds the real themes from
these files and is checked against these renders.

## What's here

| Path | What it is |
|---|---|
| [`mockup/schemes.py`](mockup/schemes.py) | The six schemes, the two that were turned down, Graphite's style rules, and the SVG indicator templates. This is the specification for `ui/theme.py`. |
| [`mockup/render.py`](mockup/render.py) | Renders the Studio under one scheme, in a fixed scenario. |
| [`mockup/contrast.py`](mockup/contrast.py) | Checks every scheme against the proposal's contrast floors. Run it after changing a value. |
| `renders/` | The reference renders: each scheme on the Start, Asset editor, Export and References tabs. |
| `renders/scale-150/` | Granite at 150% Interface size. |

The mod.io logos the schemes name are in
`anno_modding_studio/resources/modio/`, copied from mod.io's press kit.

## The scenario

Every render uses the same setup, so a render of the built app can be compared
with these pixel for pixel:

- the sample game files from `tools/make_sample_assets.py`
- a 1500 × 950 window at 100%, with body text at 14 px
- a new mod called Better Bricks, with ResidenceUpgrade and Residents on the
  Artisan Residence each raised by 5
- the first button on the Asset editor tab focused
- References at depth 2

Two things on the Export tab change from run to run: the time in the XML
preview's header, and the temporary folder in "Export to". Everything else
should be identical. The folder was blanked out of the `*_3_export.png` references,
since it named the user who made them; the scripts now work under `%PUBLIC%`.

## How the mockup works, and when it stops working

The mockup doesn't change the app. It patches the theme of commit `cb10f5a`
from outside:

- it points the old module's colour constants at the scheme
- it swaps the six colours hard-coded in the old stylesheet
- it adds rules on top for fields, focus, indicators, Flat and high contrast
- it replaces the XML preview's three fixed colours and the mod.io logo
- it gives code that reads `theme.ACCENT` the accent's text shade, because
  all of those reads use it as text

Once Stage 0 replaces `ui/theme.py`, the patching has nothing to attach to.
To render the mockup again after that, run it against a checkout of
`cb10f5a`:

```bash
git worktree add ../studio-cb10f5a cb10f5a
```

```bash
python docs/design-review/mockup/render.py ../studio-cb10f5a granite out
```

## Stage 0's acceptance check

Stage 0 is done when the built themes match these renders.
`tools/render_themes.py` renders the same scenario from the real themes, with
the clock and the export folder pinned so those two lines stop changing. It
then compares each picture with its reference here and writes a contact
sheet: built version, reference, and a difference image, with the changed
pixel count for each.

A difference is either a bug in Stage 0 or a deliberate change. A deliberate
change goes through the same approval as the original: re-render the
reference with the mockup or replace it with the built render, and say why
in the PR. Once Stage 0 lands, the built renders become the references for
every later stage.

## Changes since approval

- **The mod.io logo** is drawn from its SVG at the screen's pixel ratio, as
  the proposal asks. The mockup drew it at twice the size and let Qt scale
  it down, which is softer at 100%. The six Export references were replaced
  with the built renders on 3 Oct for this reason, with the clock pinned to
  23:40. Every reference now matches the built themes pixel for pixel.
- **Status glyphs** came with Stage 1: a warning, error or ok label has a
  glyph before its text, so the status does not rest on colour alone. On the
  Export tab that is the tick before "All 2 changes verified". The six Export
  references were replaced with the built renders on 3 Oct for this.
- **The Start tab** was rewritten in Stage 2, area 1: the introduction and
  the paragraph under each step moved behind a `?`, and each way card keeps
  one line. The six Start references were replaced with the built renders on
  3 Oct for this.
- **The Asset editor tab** was rewritten in Stage 2, area 2: its heading and
  the paragraph under it went, and labels were shortened. The six editor
  references were replaced with the built renders on 3 Oct for this.
- **Combo box arrows** are the scheme's chevron on the field's own surface.
  Fusion drew the drop-down as a raised button, which read as dated, most of
  all in the light schemes. The Asset editor and Export references were
  replaced with the built renders on 3 Oct for this.
- **`5_stack`** joined the render set on 5 Oct: the asset editor over two
  loaded mods that set one field, and the menu on that row. It shows the
  wordings that exist only over a stack ("set by this mod", "Stop setting
  this", a restated value in the Mod changes panel). It has no approved
  render, so `--compare` does not render it and the references are unchanged.
- **`added`** has a value in each scheme. The proposal left it to Stage 0 and
  no render shows it. The values are in `ui/theme.py`.
- **Flat's dividing lines** are drawn in `field-border`, not `border`, to meet
  the 3:1 floor the proposal sets for them. No reference render is Flat.
- **Brick's field border** went from `#93675f` to `#9a7068`. The approved
  value was 2.83:1 against Brick's raised surface, below the 3:1 floor. The
  render on the comparison sheet didn't check that pair, and `contrast.py`
  does.
- **0.1.0's look** replaced all 24 references on 28 Oct, approved on the
  release branch (`docs/standards/release-checklist.md`). They had stood
  since 4 Oct, and three weeks of work had changed every tab: the Mods,
  Game and Check menus and the Mod file tab, the Start tab's *Your loadout*
  and Studio folders rows, the Export tab's three rows for where a mod goes,
  and the Mod changes panel's buttons and row badges. From here on each
  release replaces them, so `--compare` shows what changed since the last
  one. `scale-150/` is from before these changes and `--compare` does not
  render it.
