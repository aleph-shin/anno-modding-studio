"""The Start tab, and what you are here to do.

The tab itself (``ui.source_panel``) is drawn from what this tells it: which
game's files are open and which games are installed, the loadout and where
it lives, and the three ways of working -- a mod of your own, a change to
somebody else's, a set of mods to play -- with what each has done so far.
Choosing one sets the loadout up for it: naming your mod, reading in what
the game loads, a collection, or mods picked out of the library.

Held as ``window.start`` (Stage 3.3 of
docs/proposals/consolidation-proposal.md).  The tab gate -- which tabs may
open yet -- is the window's navigation, and this asks it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QInputDialog, QMessageBox

from ..core import disk, games, library
from ..core.changes import AIM_MAKE, AIM_MANAGE, Loadout, LoadoutError
from ..core.i18n import date_text, tr, trn
from .navigation import TAB_EDIT
from .prompts import START_PROFILE
from .widgets import ask, busy_cursor, named, tell, wrappable_path


class StartController:
    """The window's start, moved off it; ``window`` is the MainWindow."""

    def __init__(self, window) -> None:
        self.window = window
        # The way of working just clicked, while its questions are open; and
        # whether setting it up left the loadout unsaved on purpose.
        self._choosing = ""
        self._aim_left_unsaved = False

    def forget_the_loadout_before(self) -> None:
        """Another loadout is here: a way of working half chosen was being
        chosen for the one before."""
        self._choosing = ""
        self._aim_left_unsaved = False

    def refresh_source_panel(self) -> None:
        self.window.update_tab_gates()
        open_game = self.window.game_files.open_game()
        # The language the index keeps for its names; a stand-in index of a
        # test has no such answer.
        language = getattr(self.window.index, "texts_language", "")
        self.window.source.set_state(
            self.window.index.source_path if self.window.index is not None else None,
            self.window.index.asset_count if self.window.index else 0,
            self.window.game_files.texts_path,
            self.window.index.text_count if self.window.index else 0,
            open_game,
            language=language if isinstance(language, str) else "",
        )
        self.window.source.set_loadout(
            self.window.loadout_path,
            self.window.on_disk,
            self.window.files.recent_loadouts(open_game),
            # The changes made here, as the title counts them: with mods
            # read in it counted theirs as well, 24 beside the title's 2.
            self.window.loadout.changes_made,
            beside=bool(self.window.loadout.layers),
            # The mods loaded, as the title counts them: a patch of yours is
            # not another mod read in.
            mods=sum(1 for layer in self.window.loadout.layers if not layer.patches),
            # With no file, a new loadout or none, as the title says (D8).
            held=self.window.holds_something(),
            game=games.get(open_game).label if self.window.index is not None else "",
            live=str(self.window.live.live_for(open_game) or ""),
            live_profile=self.window.live.live_profile_shown(open_game),
        )
        self.window.source.set_profiles(
            self.window.loadout.profile_names(),
            self.window.loadout.chosen_profile().name,
            live=self.window.live.live_profile_here(),
            shown={"": self.window.files.loadout_name() or tr("This loadout")},
            visible=self.window.loadout_path is not None,
        )
        self.window.source.set_aim(
            self.window.loadout.aim,
            self._aim_status(),
            ready=self.window.index is not None,
            pending=self._choosing,
        )
        self.window.source.set_truth(self.window.truth)
        self.refresh_games()

    def refresh_games(self) -> None:
        """A row per game: what has been read for it, and what to do about it.

        Both games, always, because this is also where you switch between them
        -- and switching used to be a secondary button on a card that is folded
        away as soon as anything is loaded.
        """
        from ..core import gamefiles

        rows = []
        open_game = self.window.game_files.open_game()
        for key, game in games.GAMES.items():
            stamp, state, why = self.window.game_files.last_read(key)
            loaded = open_game == key
            when = (stamp or {}).get("read_at") or 0
            day = date_text(when, year=False) if when else ""
            archive = (stamp or {}).get("source_archive") or ""
            if archive and day:
                # Translators: how a game's files were read, shown on its own
                # line; the sentences below continue it after a dash.
                read_from = tr("read from %(archive)s on %(day)s", archive=archive, day=day)
            elif archive:
                read_from = tr("read from %s", archive)
            elif day:
                read_from = tr("read from the game files on %s", day)
            else:
                read_from = tr("read from the game files")
            if state == gamefiles.READ_CURRENT:
                detail = tr("%s. The game has not changed since.", read_from)
            elif state == gamefiles.READ_PATCHED:
                detail = tr("%s. The game's files have changed since.", read_from)
            else:
                # Only the first letter raised: capitalize() would lower every
                # other one, and German capitalises its nouns.
                detail = tr("Not read yet.") if stamp is None else why[:1].upper() + why[1:] + "."
            can_use = state == gamefiles.READ_CURRENT and not loaded
            rows.append(
                {
                    "key": key,
                    "label": game.label,
                    "detail": detail,
                    "loaded": loaded,
                    "can_use": can_use,
                    "action": (
                        tr("Use these files")
                        if can_use
                        else tr("Read from game files…")
                    ),
                }
            )
        self.window.source.set_games(rows)
        self.window.source.set_creator(self.window.preferences.creator_name())

    def _aim_status(self) -> dict:
        """What each way of working would say to do next, given where we are.

        Read off the loadout rather than scripted: telling somebody to read in a
        mod when they have sixty loaded is worse than saying nothing.
        """
        from ..core import patching
        from ..core.changes import AIM_MAKE, AIM_MANAGE, AIM_TWEAK

        if self.window.index is None:
            return {}

        layers = self.window.loadout.layers
        status = {}

        home = self.window.loadout.home_layer()
        # The changes made here, the number the title bar says, and "of
        # yours" where the title says so. It counted every change of the mod
        # the card is about, so a mod carried on with from its folder was
        # "24 changes so far" under a title saying "2 changes of yours".
        mine = self.window.loadout.changes_made
        if home is not None:
            status[AIM_MAKE] = (
                tr("Continuing %s, which exports to its own folder. Other "
                   "mods are loaded beside it.", home.label)
                if len(layers) > 1
                else tr("Continuing %s, which exports to its own folder.",
                        home.label)
            )
        elif not layers:
            status[AIM_MAKE] = tr(
                "No other mods are loaded. Your mod is checked against the "
                "base game, which is what people who install it have."
            )
        else:
            status[AIM_MAKE] = tr(
                "Other mods are loaded. Your mod still exports on its own, "
                "but the editor shows the game with those mods applied."
            )
        if mine and layers:
            # Translators: %(status)s is one of the sentences above.
            status[AIM_MAKE] = trn(
                "%(status)s  %(n)s change of yours so far.",
                "%(status)s  %(n)s changes of yours so far.",
                mine, status=status[AIM_MAKE],
            )
        elif mine:
            # Translators: %(status)s is one of the sentences above.
            status[AIM_MAKE] = trn(
                "%(status)s  %(n)s change so far.",
                "%(status)s  %(n)s changes so far.",
                mine, status=status[AIM_MAKE],
            )

        patched = patching.patched_by(self.window.loadout)
        if not layers:
            status[AIM_TWEAK] = tr("No mods read in yet.")
        elif patched:
            names = sorted(patched)
            status[AIM_TWEAK] = (
                trn("Patched: %(names)s and %(n)s more.",
                    "Patched: %(names)s and %(n)s more.",
                    len(names) - 3, names=", ".join(names[:3]))
                if len(names) > 3
                else tr("Patched: %s.", ", ".join(names))
            )
        else:
            status[AIM_TWEAK] = tr(
                "Mods are read in. Pick the one to change beside the editor, "
                "then “Patch this mod…”."
            )

        if not layers:
            status[AIM_MANAGE] = tr("No mods read in yet.")
        elif self.window.loadout.mods_folder:
            status[AIM_MANAGE] = tr(
                "A loadout read from %s.", wrappable_path(self.window.loadout.mods_folder)
            )
        else:
            status[AIM_MANAGE] = tr("A loadout.")
        return status

    def choose_aim(self, aim: str, existing: bool = False, collection: bool = False,
                   from_library: bool = False, start_dir: str = "") -> None:
        """Set the workspace up for one of the three ways of working.

        ``existing`` is writing a mod of your own by opening one of yours rather
        than starting a new one -- the same way of working, a different start.
        ``collection`` is the same for a loadout: started from a collection on
        mod.io rather than from what the game loads now.  ``from_library`` is
        a third start for one: the mods ticked in the library's list.
        ``start_dir`` is the mod an aim that starts from one starts from,
        when it has been chosen already: one dropped on the window.

        The button and the answer are the same click: saying "I am writing a mod
        of my own" and then having to work out that the next move is File ▸ New
        loadout is most of the problem this is here to solve.

        Each way wants the workspace set up differently -- one wants nothing
        else loaded, another wants everything -- and the wrong mix is invisible
        until export.  So a card clicked while mods are still loaded is the one
        moment to ask about them, rather than quietly letting them ride along.

        And a click is all a card is: it writes the facts that way of working
        starts from (:meth:`Loadout.set_up_as`) and reads things in.  It is
        not a mode.  Nothing afterwards is refused for straying from the card,
        and nothing afterwards is inferred from what was loaded since -- the
        places that used to ask which card it was ask for the fact they need.

        The card clicked is picked out straight away, while its questions are
        open, and only sets the loadout up if they are answered: a cancelled
        name or an import dialog closed empty leaves things as they were.
        """
        if self.window.index is None:
            return
        self._choosing = aim
        self._aim_left_unsaved = False
        self.refresh_source_panel()
        try:
            landed = self._set_up_aim(aim, existing, collection, from_library, start_dir)
        finally:
            self._choosing = ""
            self.refresh_source_panel()
        if landed and not self._aim_left_unsaved:
            # Where this piece of work starts from. A card names the mod and
            # settles where it will live without writing anything -- the bytes
            # land when there is something to put in them -- so what it wrote
            # into the loadout is not unsaved work, it is the blank page. From
            # here on, a detail that moves is one somebody moved.
            self.window.files.mark_saved()
        self.window.update_title()
        # The click and the answer were the same gesture; having to come back
        # here and press "Open the asset editor" a second time would not be.
        # A loadout of what the game loads, the first time it holds mods
        # outside the library, stays here instead: the offer to convert them
        # is about the fourth card, and it was made over the editor.  One
        # picked out of the library holds none.
        if (landed and aim == AIM_MANAGE and not collection and not from_library
                and self.window.library_controller.offer_conversion()):
            return
        if landed:
            self.window.open_tab(TAB_EDIT)

    def play_now_with(self, source: Path, origin: str) -> bool:
        """*What I play now…* from the Export tab's Add to loadout, for
        somebody with no loadout of the mods they play
        (docs/proposals/export-tab-proposal.md, section 5.7): this card's
        *What I play now…*, with the offer to move the mods into the library
        that comes with it, then ``origin`` of the loadout saved at
        ``source`` added to the new loadout, which is opened again to read
        it.  Whether it was added."""
        from ..core import adding, projects

        window = self.window
        self.choose_aim(AIM_MANAGE)
        made = window.loadout_path
        if (window.loadout.aim != AIM_MANAGE or made is None
                or disk.same_file(made, source)):
            # Not made: a question along the way was turned down.
            return False
        if (not window.on_disk or window.dirty) and not window.files.save_loadout():
            return False
        mods_folder, library_dir = window.where.mods_folder(), window.where.library_dir()
        name = tr("your mod")
        try:
            name = Loadout.load(Path(source)).label_of(origin) or name
            with busy_cursor():
                adding.add_by_files(made, source, origin, mods_folder, library_dir)
        except (OSError, ValueError, LoadoutError, projects.ProjectError) as exc:
            tell(window, tr("Not added"), tr(
                "%(loadout)s was made, but %(mod)s was not added to it: %(why)s",
                loadout=named(window.files.loadout_name()), mod=named(name), why=exc,
            ), warning=True)
            return False
        # Opened again, so it reads the mod where the game sorts it.
        if not window.load_loadout(made, asked=True):
            return False
        # Translators: "Play" is the Studio's button that builds your mods
        # and starts the game.
        window.status_label.setText(tr(
            "%(loadout)s has the mods the game loads now, and %(mod)s. Press Play "
            "to play it.", loadout=window.files.loadout_name(), mod=name,
        ))
        return True

    def _set_up_aim(self, aim: str, existing: bool = False, collection: bool = False,
                    from_library: bool = False, start_dir: str = "") -> bool:
        """The work behind :meth:`choose_aim`; whether the aim was taken up."""
        from ..core.changes import AIM_TWEAK

        # With nothing in the library that is said, with where its mods come
        # from, before anything is cleared.
        if aim == AIM_MANAGE and from_library:
            with busy_cursor():
                held = library.catalogue(self.window.where.library_dir())
            if not held:
                QMessageBox.information(
                    self.window, tr("No mods in the library"),
                    tr("The library holds no mods for %s yet.\n"
                       "\n"
                       "A mod installed from mod.io goes there. Convert to "
                       "library… on the fourth card puts the mods the game loads "
                       "now there too.",
                       games.get(self.window.loadout.game).label),
                )
                return False
        # Any card, while anything is loaded or written: a card is a fresh
        # piece of work, and whether it is the same card as last time says
        # nothing about whether the mods sitting there belong in it. It used
        # to compare against the card clicked last, which a loadout that had
        # drifted into something else since made meaningless.
        #
        # And clearing is the only way through: a card is a setup, and a
        # setup laid over whatever was loaded is not one. There used to be a
        # middle answer that kept the mods and only relabelled the work, for
        # checking compatibility -- but that is a mod to import *after* the
        # card, not a reason to skip what the card does.
        #
        # A collection with a loadout live asks what to start from first:
        # as a new profile of the live loadout it clears nothing, and adds
        # to that loadout instead -- which is then unsaved, not a blank page.
        start = ""
        if aim == AIM_MANAGE and collection:
            live = self.window.live.live_loadout()
            if live:
                start = self.window.prompts.ask_start_from(self.window.files.name_of(live))
                if not start:
                    return False
                if start == START_PROFILE:
                    self._aim_left_unsaved = True
                    landed = self.window.modio.collection_as_profile(live)
                    self.window.update_tab_gates()
                    return landed
        if self.window.loadout.layers or self.window.loadout.authored():
            answer = ask(
                self.window,
                tr("Start fresh?"),
                tr("This starts a clean loadout.\n"
                   "\n"
                   "Clear what is loaded and start again?"),
                tr("&Start fresh"),
            )
            if not answer:
                return False
            if not self.window.new_loadout():
                return False

        # Naming the space is part of choosing a way of working, but only one of
        # the three can be asked about it up front. Writing a mod of your own
        # means you have a name in mind and the loadout simply is that mod;
        # changing somebody else's means the name comes from the mod you have
        # not picked yet; running your list means the loadout is not a mod at
        # all. So the first is asked and the other two are named on arrival.
        if aim == AIM_MAKE and not existing and not self._name_your_mod():
            return False
        # Changing somebody else's ends in a patch mod with your name on it,
        # so the one question it needs answering is asked before the reading
        # starts rather than in the middle of it. A name left blank only
        # means the Studio cannot tell your own mods from anybody's; the
        # question cancelled is the aim cancelled.
        if aim == AIM_TWEAK and self.window.preferences.ensure_creator() is None:
            return False

        was = (self.window.loadout.aim, self.window.loadout.is_the_mod, self.window.loadout.own_mod, self.window.loadout.own.loads_last)
        self.window.loadout.set_up_as(aim)
        landed = False
        if aim == AIM_MAKE and existing:
            # Named after the mod once it has arrived, the way the other aim
            # that starts from a mod is.
            landed = self.window.patches.open_your_own_mod(start_dir)
        elif aim == AIM_MAKE:
            landed = True
            # Named a moment ago, which is as deliberate as making a mod gets.
            self.window.loadout.make_own_mod()
            self.window.status_label.setText(
                tr("Writing %s.  Find an asset, change it, then Export when you "
                   "are ready.", self.window.loadout.own.name)
                if self.window.loadout.own.name
                else tr("Writing a mod of your own.  Find an asset, change it, "
                        "then Export when you are ready.")
            )
        elif aim == AIM_TWEAK:
            before = {layer.origin for layer in self.window.loadout.layers}
            landed = self.window.import_mod(separate=True, start_dir=start_dir)
            if landed:
                loadout = self.window.loadout
                arrived = [
                    layer.origin
                    for layer in loadout.layers
                    if layer.origin not in before
                ]
                if len(arrived) == 1:
                    self.window.patches.patch_what_just_arrived(arrived[0])
                    if self.window.loadout is not loadout:
                        # Opened the loadout that keeps it instead: that is
                        # what landed, named and aimed already.
                        arrived = []
                    elif loadout.layer(arrived[0]) is None:
                        # Yours, kept in a project another loadout has, and
                        # let go of again: nothing was read in after all, and
                        # naming the loadout after it named it after nothing.
                        arrived = []
                        landed = False
                if self.window.loadout_path is None and arrived:
                    patch = self.window.loadout.patch_for(arrived[0])
                    home = self.window.loadout.home_layer()
                    self.window.files.name_loadout(
                        home.label
                        if home is not None
                        else patch.origin
                        if patch is not None
                        else arrived[0]
                    )
        elif aim == AIM_MANAGE and collection:
            landed = self.window.modio.loadout_from_collection(start)
        elif aim == AIM_MANAGE:
            # From the library: its list, every mod named and none read
            # until it is ticked.
            landed = (
                self.window.load_installed_mods(picked=[]) if from_library
                else self.window.load_installed_mods()
            )
            if landed:
                # What *your* mod is called here: the patch over this
                # loadout's list, named after the loadout. The default is the
                # one a standalone mod gets, and the two are not the same
                # thing at all; and two loadouts' patches named alike were one
                # mod to the library and the game (LoadoutFiles.name_list_patch).
                if self.window.loadout_path is None:
                    self.window.files.name_loadout(self.list_loadout_name())
                else:
                    self.window.files.name_list_patch()
                # Read from what the game loads, so with nothing live yet it
                # is what is live: making it so would change nothing.
                if self.window.truth is not None and self.window.truth.matches and not self.window.live.live_loadout():
                    self.window.live.note_live()
                    self.window.refresh_truth(say=False)
        if not landed:
            # Nothing was read in, so this is not the way you are working yet.
            (
                self.window.loadout.aim,
                self.window.loadout.is_the_mod,
                self.window.loadout.own_mod,
                self.window.loadout.own.loads_last,
            ) = was
        self.window.update_tab_gates()
        return landed

    def _name_your_mod(self) -> bool:
        """Ask what the mod being written is called, and place the loadout by it.

        Asked here rather than left to the Generate tab, where it used to be the
        last thing on the last page: for this way of working the loadout *is*
        the mod, so its name is the one fact that settles both what you are
        making and where it lives.  Cancelling means the way of working was not
        chosen either -- half-choosing it would be the state this exists to
        avoid.
        """
        if self.window.loadout_path is not None and self.window.loadout.aim == "":
            # An opened loadout already has both, and is not being renamed by
            # someone pressing the card again.
            return True
        suggested = self.window.loadout.own.name or "My Anno Mod"
        name, ok = QInputDialog.getText(
            self.window,
            tr("Name your mod"),
            tr("This names the mod, the folder you export, and the "
               "loadout.\n"
               "You can rename it later."),
            text=suggested,
        )
        if not ok:
            return False
        # Both halves of "what am I making, and who is making it" in one go,
        # and cancelling either is cancelling both.
        if self.window.preferences.ensure_creator() is None:
            return False
        name = name.strip() or suggested
        self.window.loadout.own.name = name
        self.window.output.load_loadout_fields()
        self.window.changes_panel.refresh()
        self.window.file_panel.mark_stale()
        self.window.files.name_loadout(name)
        return True

    def list_loadout_name(self) -> str:
        """What to call a loadout of mods to play, read from what the game
        loads now or started from the library: the game's name.  It was the name of the mods folder's parent, which is
        the game's in a game's own folder ("…/Anno 1800/mods") and anything
        at all in a folder chosen elsewhere: "hands-on loadout" of
        "…/hands-on/mods-150"."""
        return "%s loadout" % (games.get(self.window.loadout.game).label or "Anno")

    def took_mods_in(self) -> None:
        """Mods were just read in with no way of working chosen and no
        loadout file to read them into: from mod.io, a dropped zip or the
        menus, rather than a card.  Mods read in make a loadout, and with no
        card to say otherwise it is a set of mods to play, so it is set up
        as *Play a set of mods* sets one up: named after the game,
        with your edits going to the patch over its whole list that loads
        last.  Left alone, they went to a standalone mod called My Anno Mod
        that was neither a patch of the mod read in nor of the list."""
        loadout = self.window.loadout
        if loadout.aim or self.window.loadout_path is not None or not loadout.layers:
            return
        loadout.set_up_as(AIM_MANAGE)
        self.window.files.name_loadout(self.list_loadout_name())
