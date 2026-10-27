# Credits

Anno Modding Studio stands on the modding community's work. Thank you.

The licences of the code the Studio contains or runs on are in
[THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md). This page thanks the people
and projects behind it.

## Tools the Studio runs or reads

- **Anno Mod Loader** by Jakob Harder, continuing meow's (Alexander Guettler's)
  original Anno 1800 mod loader. The Studio's model of how a ModOp is applied is
  a port of its `xml_operations.cc` (MIT).
  <https://github.com/jakobharder/anno-mod-loader>, and the original at
  <https://github.com/magicalcookie/anno1800-mod-loader>
- **Anno Modding Tools** for VS Code, by Jakob Harder. The Studio runs the mod
  loader's `xmltest` from your installed copy, as a second opinion on what a
  mod does.
  <https://marketplace.visualstudio.com/items?itemName=jakobharder.anno-modding-tools>
  and <https://github.com/anno-mods/vscode-anno>
- **iModYourAnno** by Taubenangriff, Jakob Harder and its other contributors.
  The Studio reads and writes a mod's tweaks the way iModYourAnno does, and its
  export is a port of iModYourAnno's own (MIT).
  <https://github.com/anno-mods/iModYourAnno>
- **Community GUID Ranges**, kept by anno-mods. New assets take their GUIDs
  from it, fetched from GitHub when needed.
  <https://github.com/anno-mods/GuidRanges>
- **RDAExplorer**, which the Studio recommends for extracting game files by
  hand. <https://github.com/anno-mods/RDAExplorer>
- **Anno 117 Mod Manager** by Taludas. The Studio notices it, and says how the
  two share the game's profile and options files.
- **mod.io**, for finding, installing and publishing mods. <https://mod.io>
- Built with **Python**, **Qt for Python (PySide6)** and **lxml**.
  <https://www.python.org>, <https://www.qt.io/qt-for-python> and
  <https://lxml.de>
- The Windows zip is built with **PyInstaller**. <https://pyinstaller.org>

## Projects that taught the Studio how the game and loader behave

- The **Anno Mod Loader reference documentation**.
  <https://jakobharder.github.io/anno-mod-loader/>
- The **mod loader's source**, read for how it merges, reads ModItems and
  follows includes, on both its Anno 117 `main` and its `stable/anno1800`
  branches.
- **iModYourAnno's source**, for its tweak format.
- **Ubisoft's Anno Union** announcement of the modders' safe GUID range, as the
  GuidRanges README links it.
  <https://anno-union.com/en/new-anno-union-history-edition-update-anno-afternoon/>
- The **mod.io REST API documentation**. <https://docs.mod.io>
- **RDAExplorer**, for the RDA archive format the Studio reads game files out
  of. <https://github.com/anno-mods/RDAExplorer>
- **anno-mods/Modinfo**, for the `modinfo.json` format.
  <https://github.com/anno-mods/Modinfo>
- **Wine** and **Steam's Proton**, for how a Windows program sees a Linux disk:
  Wine's source for its links and path calls, and Proton's for what its container
  lets a game see. <https://www.winehq.org> and
  <https://github.com/ValveSoftware/Proton>
- **Every mod author on mod.io.** The Studio's released source code contains
  machinery designed to verify the app's internal logic against a broad survey
  of Anno mods published on [mod.io](https://mod.io). Data from authors' mods
  is obtained via the [mod.io API](https://docs.mod.io) in accordance with the
  [mod.io API Access Terms](https://mod.io/legal/apiterms) and is never
  redistributed with the Studio's releases or source code.

## Designs inspired by other apps and projects

- **Mod Organizer 2**: profiles, where activating a set means only that set
  loads, and keeping mods in a library outside the game's folder, linked in.
  <https://github.com/ModOrganizer2/modorganizer>
- **iModYourAnno**: mod options applied by switching the author's ops off and
  appending edited copies, and showing what a setting does before writing it.
- **Anno 117's own mod profiles**: a loadout is a game profile.
- **The StarCraft II Galaxy Editor**: the asset editor's XML view toggle, and the
  strip of tabs across the top of the editor.
- **Unreal Editor's Reference Viewer**: the References graph, whose look and feel
  follow Unreal's closely.
  <https://dev.epicgames.com/documentation/en-us/unreal-engine/reference-viewer-in-unreal-engine>

StarCraft is a trademark of Blizzard Entertainment, and Unreal of Epic Games. The
Studio is not affiliated with either.

## Claude

The ambition and scope of this project would not have been possible without
**Claude**, Anthropic's AI model. Claude wrote almost all of the Studio's code,
its tests and its documentation, and did much of the research and gave much of
the advice behind its design, where the way forward was not known.
[AI_DISCLOSURE.md](../AI_DISCLOSURE.md) says how the Studio was made and how it is
checked. <https://www.anthropic.com/claude>

The German translation is Claude's too. It is machine translation, and the
language picker says so. Corrections from German speakers are welcome.
