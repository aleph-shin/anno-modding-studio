Anno Modding Studio {version} (beta) for Windows
================================================

A Windows app for Anno 1800 and Anno 117 mods. It plays a set of mods,
changes a mod you downloaded, and builds a mod of your own.

This folder is the whole program. It needs no installation and no Python.


Running it
----------

1. Unzip the zip into a folder of your own, for example
   %LOCALAPPDATA%\Programs\AnnoModdingStudio-{version}. Don't run it from
   inside the zip, and don't unzip it straight into C:\ or Downloads, where
   other programs or other users can change the files beside it.
2. Start AnnoModdingStudio.exe.

Windows may say "Windows protected your PC", because this build is not
signed. Choose "More info", then "Run anyway". If the file's Properties show
an "Unblock" box, you can tick it before the first start instead.


Where your data is
------------------

Not in this folder. The Studio keeps:

- your loadouts, your own mods (projects), exports and backups in
  Documents\AnnoModdingStudio
- the library of mods, the index of the game files, your encrypted
  mod.io key and its settings (settings.ini) in
  %LOCALAPPDATA%\AnnoModdingStudio

To keep all of it in one folder of your choice instead, set the
environment variable ANNO_STUDIO_FOLDER to that folder before you start the
Studio. A folder beside the program is not used: an update is a new folder.


Updating
--------

1. Close the Studio.
2. Unzip the new version into a new folder, and start it from there. It
   finds your data where the old one left it.
3. Once the new version works, delete the old folder.

Don't unzip a new version over an old one: files the old version had and
the new one doesn't would stay behind.

If you set ANNO_STUDIO_FOLDER, leave it set: the new version reads it too.

Going back to the old version is starting the old folder's
AnnoModdingStudio.exe. If the new version keeps your data in a way the old
one would misread, the old one says so and doesn't start.


More
----

The user guide:      {readme_url}
The source of this
version:             {tag_url}
Releases:            {releases_url}

LICENSE.txt is the Studio's licence, the GNU General Public License,
version 3 or later. It comes with ABSOLUTELY NO WARRANTY.
THIRD-PARTY-NOTICES.txt lists the work of others it contains, and licenses\
has their licences. SOURCES.txt says where the source of every part of this
build is.

Anno Modding Studio is a fan-made community tool. It isn't made, endorsed or
supported by Ubisoft or by mod.io. Anno, Anno 1800 and Anno 117: Pax Romana
are trademarks of Ubisoft Entertainment in the US and/or other countries.
