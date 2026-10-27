# Security

## Reporting a vulnerability

Please report a security problem privately, not in a public issue.

1. Open the repository's **Security** tab on GitHub.
2. Choose **Report a vulnerability**. This is GitHub's private vulnerability
   reporting: only you and the maintainers can see the report.
3. Say what you found, which version it affects (the About box's copy button gives
   the version), and how to reproduce it. A small mod, zip or project file that
   shows it helps most.

You'll get an answer within a week. Once a fix is released, the advisory is
published, and you are credited unless you ask not to be.

## Supported versions

The Studio is in public beta. Only the latest release gets security fixes.

## In scope

The Studio's own code, as source and as the frozen Windows build:

- reading mods, zips, project files and game archives, where a crafted file could
  write outside the folder it should, run code, or read files it shouldn't
- installing a mod from mod.io or a zip into the library and linking it into a mods
  folder
- how the mod.io API key, access token and email address are stored and sent
- the requests the Studio makes, listed in the user guide under
  [What the Studio connects to](docs/guide.md#what-the-studio-connects-to)
- the frozen build's own folder: the DLLs and files it loads at start, and the
  `AnnoModdingStudioData` folder beside it

## Out of scope

- mod.io's service, the game, Steam, Ubisoft Connect, the mod loader and the
  Anno Modding Tools extension. Please report those to their owners.
- A problem in Python, Qt, PySide6 or lxml themselves. Report it upstream. Do tell
  us if the version the frozen build ships is affected.
- Attacks that need software already running as your Windows user. Windows' data
  protection, which keeps the mod.io key, guards against other accounts and copied
  files, not against that.
- A mod doing in the game what its ModOps say. The Studio shows what a mod changes,
  but it doesn't vet mods.
