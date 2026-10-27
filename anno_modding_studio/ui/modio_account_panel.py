"""Your mod.io API key: the one place it is entered, and the one place it is not.

A personal key is what every mod.io feature needs and all that the read-only
ones need.  It is the user's own -- got from their account page, not shipped
with the Studio -- so this asks for it once, checks it against mod.io, and
keeps it wrapped for this Windows user (see :mod:`core.secrets`).  The dialog
says what will be done with it in plain words, because a key is the first
thing the Studio has ever asked for that it must not lose.

Publishing needs a sign-in as well, and that is here too: an address, a code
mod.io emails to it, and a token good for a year.  mod.io's terms are shown
and must be agreed before the code is even asked for -- their rule, and the
reason the account is the one place the terms dialog opens from.

It was a dialog of its own, opened from a button on Settings' mod.io tab
that had nothing else on it.  It is that tab now: Settings is where the
Studio keeps what belongs to you, and a "Set up mod.io account" button
anywhere else opens Settings there.
"""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core import modio, secrets
from ..core.i18n import date_text, tr
from .help_tip import HelpTip
from .modio_brand import branding
from .modio_publish_dialog import TermsDialog
from .widgets import busy_cursor, rich_label

ACCESS_URL = "https://mod.io/me/access"

Check = Callable[[str], None]
ClientFactory = Callable[[str, str], modio.Client]


def _check_with_modio(key: str) -> None:
    modio.Client(key).check_key()


def _client(key: str, token: str = "") -> modio.Client:
    return modio.Client(key, token=token)


def signed_in_sentence(credentials: secrets.Credentials) -> str:
    """How a sign-in that is held is said, here and in Settings alike."""
    if credentials.email:
        return tr("Signed in as %s.", credentials.email)
    return tr("Signed in to your mod.io account.")


def sign_in_state(credentials: secrets.Credentials) -> str:
    """Whether you are signed in, said with what is coming: a sign-in that
    runs out within two weeks says when, and one that has run out says so,
    rather than being taken for a sign-in until mod.io refuses it."""
    if credentials.runs_out_soon():
        return tr(
            "%(signed)s The sign-in runs out on %(when)s. Sign out and in again to renew it.",
            signed=signed_in_sentence(credentials), when=date_text(credentials.token_expires),
        )
    if credentials.signed_in():
        return signed_in_sentence(credentials)
    if credentials.expired():
        return tr("Your mod.io sign-in ran out on %s. Sign in again to publish.",
                  date_text(credentials.token_expires))
    return tr("Not signed in.")


class ModioAccountPanel(QWidget):
    """Paste the key, have it checked, keep it -- or forget it.

    ``check`` is asked to try the key and raise if mod.io will not have it;
    tests hand in their own.  Afterwards ``saved`` holds what was written,
    ``forgotten`` says the file went, and ``note`` is a sentence for the status
    bar.

    Its buttons' mnemonics are picked around the rest of Settings, whose
    dialog they share: a letter is the dialog's, whichever tab it is on.
    """

    def __init__(
        self,
        store: secrets.Store,
        parent=None,
        check: Check = _check_with_modio,
        client: ClientFactory = _client,
        show_terms: Optional[Callable[[modio.Terms], bool]] = None,
    ):
        super().__init__(parent)
        self.store = store
        self._check = check
        self._client = client
        self._show_terms = show_terms or self._terms_dialog
        self.saved: Optional[secrets.Credentials] = None
        self.forgotten = False
        self.note = ""
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # The title says whose account. Where a key comes from stays in view,
        # because it is the first thing to do here. The rest is behind the ?.
        # Translators: shown just before mod.io's logo.
        outer.addWidget(branding(tr("Your account on")))

        head = QHBoxLayout()
        hint = rich_label(tr(
            "Get an API key from <a href=\"%(url)s\">%(url)s</a> and paste it here.",
            url=ACCESS_URL,
        ))
        hint.setObjectName("StepHint")
        hint.setWordWrap(True)
        hint.setOpenExternalLinks(True)
        hint.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        head.addWidget(hint, 1)
        head.addWidget(HelpTip(tr(
            "The Studio uses your own API key to read which mods are on mod.io "
            "and which file of each is current. A key only reads, and mod.io "
            "allows it sixty requests a minute. The key is sent to mod.io with "
            "every request and to nobody else. It is kept encrypted for your "
            "Windows account on this machine, in the Studio's local folder. "
            "It is never written to the settings file, a loadout or an export."
        ), tr("API key")))
        outer.addLayout(head)

        row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(tr("API key"))
        self.key_edit.setAccessibleName(tr("API key"))
        # The buttons beside it are longer in German and took the field's room.
        self.key_edit.setMinimumWidth(220)
        self.key_edit.textChanged.connect(self._refresh_buttons)
        row.addWidget(self.key_edit, 1)
        # Translators: a tick box that shows the hidden key as plain text.
        self.show_box = QCheckBox(tr("&Show", context="reveal key"))
        self.show_box.toggled.connect(
            lambda on: self.key_edit.setEchoMode(
                QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
            )
        )
        row.addWidget(self.show_box)
        # The key's own buttons sit with the key: they are about it and
        # nothing below it.
        self.forget_button = QPushButton(tr("Forget &key"))
        self.forget_button.clicked.connect(self._forget)
        row.addWidget(self.forget_button)
        self.save_button = QPushButton(tr("Check and s&ave"))
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(self._save)
        row.addWidget(self.save_button)
        outer.addLayout(row)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # Publishing needs more than a key: a sign-in, by a code mod.io emails.
        sign_box = QGroupBox(tr("Sign in, for publishing"))
        sign = QVBoxLayout(sign_box)
        self.signed_label = QLabel("")
        self.signed_label.setWordWrap(True)
        signed_row = QHBoxLayout()
        signed_row.addWidget(self.signed_label, 1)
        signed_row.addWidget(HelpTip(tr(
            "Reading needs only the key. Sending a new version of your mod to "
            "mod.io needs you signed in. Enter the email of your mod.io "
            "account, agree to mod.io's terms, and type the code mod.io sends "
            "you. The sign-in lasts a year and is kept the same way as the key."
        ), sign_box.title()))
        sign.addLayout(signed_row)
        email_row = QHBoxLayout()
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText(tr("Email of your mod.io account"))
        self.email_edit.setAccessibleName(self.email_edit.placeholderText())
        self.email_edit.textChanged.connect(self._refresh_buttons)
        email_row.addWidget(self.email_edit, 1)
        self.code_request_button = QPushButton(tr("Send &me a code"))
        self.code_request_button.clicked.connect(self._send_code)
        email_row.addWidget(self.code_request_button)
        sign.addLayout(email_row)
        code_row = QHBoxLayout()
        self.code_edit = QLineEdit()
        self.code_edit.setPlaceholderText(tr("The code from the email"))
        self.code_edit.setAccessibleName(self.code_edit.placeholderText())
        self.code_edit.textChanged.connect(self._refresh_buttons)
        code_row.addWidget(self.code_edit, 1)
        self.sign_in_button = QPushButton(tr("Si&gn in"))
        self.sign_in_button.clicked.connect(self._sign_in)
        code_row.addWidget(self.sign_in_button)
        self.sign_out_button = QPushButton(tr("Sign o&ut"))
        self.sign_out_button.clicked.connect(self._sign_out)
        code_row.addWidget(self.sign_out_button)
        sign.addLayout(code_row)
        outer.addWidget(sign_box)
        outer.addStretch(1)

        # Enter in a field does what that field is for, and nothing else.
        # Left to the dialog, it pressed the first button that would take it,
        # which was Forget key: typing the emailed code and pressing Enter
        # deleted the saved key and the sign-in.
        for button in (self.forget_button, self.save_button, self.code_request_button,
                       self.sign_in_button, self.sign_out_button):
            button.setAutoDefault(False)
        # click() does nothing on a button that is switched off.
        self.key_edit.returnPressed.connect(self.save_button.click)
        self.email_edit.returnPressed.connect(self.code_request_button.click)
        self.code_edit.returnPressed.connect(self.sign_in_button.click)

        existing = self.store.load()
        if existing.api_key:
            self.key_edit.setText(existing.api_key)
            self.status.setText(tr("A key is saved. Paste a different one to replace it."))
        if existing.email:
            self.email_edit.setText(existing.email)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        key = self.key_edit.text().strip()
        self.save_button.setEnabled(bool(key))
        self.forget_button.setEnabled(bool(self.store.load().api_key))
        held = self.store.load()
        # A sign-in past its day is no sign-in: it can be replaced.
        signed = held.signed_in()
        self.signed_label.setText(sign_in_state(held))
        self.code_request_button.setEnabled(bool(key) and "@" in self.email_edit.text() and not signed)
        self.sign_in_button.setEnabled(bool(key) and bool(self.code_edit.text().strip()) and not signed)
        self.sign_out_button.setEnabled(signed)
        self.email_edit.setEnabled(not signed)
        self.code_edit.setEnabled(not signed)

    # -- signing in ----------------------------------------------------------
    def _terms_dialog(self, terms: modio.Terms) -> bool:
        return TermsDialog(terms, self).exec() == int(QDialog.DialogCode.Accepted)

    def _send_code(self) -> None:
        key, email = self.key_edit.text().strip(), self.email_edit.text().strip()
        if not key or not email:
            return
        try:
            with busy_cursor():
                terms = self._client(key, "").terms()
        except modio.ModioError as exc:
            self.status.setText(tr("mod.io could not be asked for its terms: %s", exc))
            return
        if not self._show_terms(terms):
            self.status.setText(tr("Not signed in: mod.io's terms were not agreed to."))
            return
        try:
            with busy_cursor():
                said = self._client(key, "").request_code(email)
        except modio.ModioError as exc:
            self.status.setText(tr("mod.io would not send a code: %s", exc))
            return
        self._agreed = True
        # Translators: follows mod.io's own sentence saying a code was sent.
        self.status.setText(tr("%s Type it below and sign in.", said))
        self.code_edit.setFocus()

    def _sign_in(self) -> None:
        key, code = self.key_edit.text().strip(), self.code_edit.text().strip()
        if not key or not code:
            return
        try:
            with busy_cursor():
                token = self._client(key, "").exchange_code(code, terms_agreed=True)
                me = self._client(key, token.access_token).me()
        except modio.ModioError as exc:
            self.status.setText(tr("mod.io did not sign you in: %s", exc))
            return
        credentials = self.store.load()
        credentials.api_key = key
        credentials.token = token.access_token
        credentials.token_expires = token.expires
        credentials.email = self.email_edit.text().strip()
        self.store.save(credentials)
        self.saved = credentials
        self.code_edit.clear()
        self.note = tr("Signed in to mod.io as %s.", me.username or credentials.email)
        self.status.setText(self.note)
        self._refresh_buttons()

    def _sign_out(self) -> None:
        credentials = self.store.load()
        if credentials.signed_in():
            with busy_cursor():
                self._client(credentials.api_key or self.key_edit.text().strip(), credentials.token).logout()
        self.saved = self.store.drop_token()
        self.note = tr("Signed out of mod.io. The key is kept.")
        self.status.setText(self.note)
        self._refresh_buttons()

    def _save(self) -> None:
        key = self.key_edit.text().strip()
        if not key or any(c.isspace() for c in key):
            self.status.setText(tr("A key is one word with no spaces in it."))
            return
        try:
            with busy_cursor():
                self._check(key)
        except modio.KeyRejected:
            self.status.setText(tr("mod.io did not accept this key. Check it against your account page."))
            return
        except modio.ModioError as exc:
            # Not a verdict on the key: mod.io could not be reached to ask.
            # Keep the key; the first feature to use it will say if it is wrong.
            self.note = tr("Key saved. mod.io could not be reached to check it (%s).", exc)
        else:
            self.note = tr("Key saved and accepted by mod.io.")
        credentials = self.store.load()
        credentials.api_key = key
        self.store.save(credentials)
        self.saved = credentials
        self.status.setText(self.note)
        self._refresh_buttons()

    def _forget(self) -> None:
        """Forget the key, and with it anything the key was needed for."""
        credentials = self.store.load()
        credentials.api_key = ""
        if credentials.token:
            # A sign-in without a key cannot be used, so it goes too -- said
            # in the note rather than left to be discovered.
            with_token = True
            credentials.token = ""
            credentials.token_expires = 0
        else:
            with_token = False
        if credentials.email:
            self.store.save(credentials)
        else:
            self.store.forget()
        self.forgotten = True
        self.key_edit.clear()
        self.note = (tr("mod.io key forgotten, and the sign-in with it.") if with_token
                     else tr("mod.io key forgotten."))
        self.status.setText(self.note)
        self._refresh_buttons()
