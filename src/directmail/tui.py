"""Textual webmail UI for Directmail — arrow-key navigation."""

from __future__ import annotations

from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from directmail.cards import CardError, card_summary, encode_card
from directmail.clipboard import copy_text, read_via_system
from directmail.keystore import KeystoreAuthError
from directmail.mail import MailService, default_handle
from directmail.models import Folder, Message


class UnlockScreen(ModalScreen[str | None]):
    """Passphrase unlock."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="auth-box"):
            yield Label("Unlock Directmail")
            yield Input(placeholder="Keystore passphrase", password=True, id="pass")
            yield Horizontal(
                Button("Unlock", variant="primary", id="ok"),
                Button("Quit", id="cancel"),
            )

    def on_mount(self) -> None:
        self.query_one("#pass", Input).focus()

    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        self.dismiss(self.query_one("#pass", Input).value)

    @on(Button.Pressed, "#cancel")
    def cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted, "#pass")
    def submit(self) -> None:
        self.dismiss(self.query_one("#pass", Input).value)


class RegisterScreen(ModalScreen[tuple[str, str] | None]):
    """First-run registration."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="auth-box"):
            yield Label("Create your Directmail identity")
            yield Label("Handle (user@machine)")
            yield Input(value=default_handle(), id="handle")
            yield Label("Keystore passphrase")
            yield Input(placeholder="Choose a passphrase", password=True, id="pass")
            yield Input(placeholder="Confirm passphrase", password=True, id="pass2")
            yield Horizontal(
                Button("Create", variant="primary", id="ok"),
                Button("Quit", id="cancel"),
            )

    def on_mount(self) -> None:
        self.query_one("#handle", Input).focus()

    @on(Button.Pressed, "#ok")
    def ok(self) -> None:
        handle = self.query_one("#handle", Input).value.strip()
        p1 = self.query_one("#pass", Input).value
        p2 = self.query_one("#pass2", Input).value
        if not p1 or p1 != p2:
            self.app.notify("Passphrases do not match", severity="error")
            return
        self.dismiss((handle, p1))

    @on(Button.Pressed, "#cancel")
    def cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ComposeScreen(ModalScreen[tuple[str, str] | None]):
    """Full-sheet compose — webmail-style To + body, pick contacts with arrows."""

    BINDINGS = [
        Binding("escape", "cancel", "Discard", show=True),
        Binding("ctrl+enter", "send", "Send", show=True),
        Binding("ctrl+s", "send", "Send", show=False),
    ]

    def __init__(self, contacts: list[str], *, default_to: str = "") -> None:
        super().__init__()
        self.contacts = sorted(contacts, key=str.lower)
        self.default_to = default_to
        self._filter = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="compose-sheet"):
            with Horizontal(id="compose-toolbar"):
                yield Label("New message", id="compose-title")
                yield Static("  no subject  ·  body only", id="compose-meta")
                yield Horizontal(
                    Button("Send  ^⏎", variant="primary", id="send", compact=True),
                    Button("Discard  Esc", id="cancel", compact=True),
                    id="compose-actions",
                )
            with Horizontal(id="compose-to-row"):
                yield Label("To", id="compose-to-label")
                yield Input(
                    value=self.default_to,
                    placeholder="type a handle or pick below",
                    id="to",
                )
            yield Static(
                "Address book — ↑↓ then Enter to fill To",
                id="compose-picker-hint",
            )
            yield OptionList(id="compose-picker")
            yield Static("Message", id="compose-body-label")
            yield TextArea(id="body", language=None)
            yield Footer()

    def on_mount(self) -> None:
        self._rebuild_picker(self.contacts)
        body = self.query_one("#body", TextArea)
        body.show_line_numbers = False
        if self.default_to:
            body.focus()
        elif self.contacts:
            self.query_one("#compose-picker", OptionList).focus()
        else:
            self.query_one("#to", Input).focus()
        if not self.contacts:
            self.query_one("#compose-picker-hint", Static).update(
                "No contacts yet — press a to add a card, or type a handle you already know"
            )

    def _rebuild_picker(self, handles: list[str]) -> None:
        picker = self.query_one("#compose-picker", OptionList)
        picker.clear_options()
        if not handles:
            picker.add_option(Option("(no matching contacts)", id="__none__", disabled=True))
            return
        for h in handles:
            picker.add_option(Option(h, id=h))

    @on(Input.Changed, "#to")
    def to_changed(self, event: Input.Changed) -> None:
        q = event.value.strip().lower()
        self._filter = q
        if not q:
            matched = self.contacts
        else:
            matched = [h for h in self.contacts if q in h.lower()]
        self._rebuild_picker(matched)

    @on(OptionList.OptionSelected, "#compose-picker")
    def pick_contact(self, event: OptionList.OptionSelected) -> None:
        if event.option_id in (None, "__none__"):
            return
        handle = str(event.option_id)
        to = self.query_one("#to", Input)
        to.value = handle
        self.query_one("#body", TextArea).focus()

    @on(Button.Pressed, "#send")
    def send_btn(self) -> None:
        self.action_send()

    @on(Button.Pressed, "#cancel")
    def cancel_btn(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_send(self) -> None:
        to = self.query_one("#to", Input).value.strip()
        body = self.query_one("#body", TextArea).text
        if not to:
            self.notify("Choose a recipient", severity="error")
            self.query_one("#to", Input).focus()
            return
        if to not in self.contacts:
            self.notify(
                f"{to} is not in your address book — add their contact card first",
                severity="error",
            )
            return
        if not body.strip():
            self.notify("Write a message first", severity="error")
            self.query_one("#body", TextArea).focus()
            return
        self.dismiss((to, body))


class ContactsScreen(ModalScreen[None]):
    """Share / import contact cards with one-key copy and easy paste."""

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("y", "copy_mine", "Copy my card"),
        Binding("ctrl+y", "copy_mine", "Copy my card", show=False),
        Binding("ctrl+s", "add", "Save contact"),
    ]

    def __init__(self, mail: MailService) -> None:
        super().__init__()
        self.mail = mail
        self._my_card = ""
        self._contact_cards: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with Vertical(id="contacts-sheet"):
            with Horizontal(id="contacts-toolbar"):
                yield Label("Contacts", id="contacts-title")
                yield Static("  y = copy yours", id="contacts-meta")
                yield Button("Close  Esc", id="close", compact=True)
            with Vertical(id="contacts-share"):
                yield Label("Your card — Copy my card, then send it to your peer")
                yield TextArea(id="my-card", classes="card-field")
                with Horizontal(id="contacts-share-actions"):
                    yield Button("Copy my card", variant="primary", id="copy-mine", compact=True)
                    yield Static(id="my-fp", classes="fp")
            with Vertical(id="contacts-import"):
                yield Label("Add someone — paste their dm:v1:… card below")
                yield TextArea(id="card-input")
                with Horizontal(id="contacts-import-actions"):
                    yield Button(
                        "Save contact",
                        variant="success",
                        id="add",
                        compact=True,
                    )
                    yield Button("Clear", id="clear-paste", compact=True)
                    yield Static("  Ctrl+S also saves", classes="hint")
            yield Label("Address book — Enter copies their card")
            yield OptionList(id="contact-list")
            yield Footer()

    def on_mount(self) -> None:
        mine = self.query_one("#my-card", TextArea)
        mine.show_line_numbers = False
        mine.read_only = True
        paste = self.query_one("#card-input", TextArea)
        paste.show_line_numbers = False
        self._refresh()
        paste.focus()

    def _refresh(self) -> None:
        mine = self.query_one("#my-card", TextArea)
        try:
            self._my_card = self.mail.my_card()
            mine.load_text(self._my_card)
            self.query_one("#my-fp", Static).update(
                f"fingerprint  {self.mail.my_fingerprint()}"
            )
        except Exception as exc:
            self._my_card = ""
            mine.load_text(str(exc))
            self.query_one("#my-fp", Static).update("")

        picker = self.query_one("#contact-list", OptionList)
        picker.clear_options()
        self._contact_cards.clear()
        contacts = self.mail.list_contacts()
        if not contacts:
            picker.add_option(Option("(no contacts yet)", id="__none__", disabled=True))
            return
        for c in contacts:
            card = encode_card(handle=c.handle, ipns=c.ipns, pubkey=c.pubkey)
            self._contact_cards[c.handle] = card
            picker.add_option(
                Option(f"{card_summary(c.handle, c.pubkey)}", id=c.handle)
            )

    def action_copy_mine(self) -> None:
        if not self._my_card:
            self.notify("No card to copy", severity="error")
            return
        ok = copy_text(self.app, self._my_card)
        mine = self.query_one("#my-card", TextArea)
        mine.load_text(self._my_card)
        mine.focus()
        try:
            mine.action_select_all()
        except Exception:  # noqa: BLE001
            pass
        if ok:
            self.notify("Your card copied — paste it to your peer")
        else:
            self.notify(
                "Clipboard blocked — card selected; use Ctrl+Shift+C (or Cmd+C)",
                severity="warning",
            )

    @on(Button.Pressed, "#copy-mine")
    def copy_mine_btn(self) -> None:
        self.action_copy_mine()

    def action_add(self) -> None:
        # Prefer paste box; fall back to OS / Textual clipboard
        paste = self.query_one("#card-input", TextArea)
        card = paste.text.strip()
        if not card:
            clip = (self.app.clipboard or "").strip()
            if clip.startswith("dm:v1:"):
                card = clip
        if not card:
            sys_clip = (read_via_system() or "").strip()
            if sys_clip.startswith("dm:v1:"):
                card = sys_clip
        if not card:
            self.notify("Paste a contact card first", severity="error")
            paste.focus()
            return
        try:
            contact = self.mail.add_contact_card(card)
        except CardError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"Added {contact.handle}")
        paste.clear()
        self._refresh()
        paste.focus()

    @on(Button.Pressed, "#add")
    def add_btn(self) -> None:
        self.action_add()

    @on(Button.Pressed, "#clear-paste")
    def clear_paste(self) -> None:
        self.query_one("#card-input", TextArea).clear()
        self.query_one("#card-input", TextArea).focus()

    @on(OptionList.OptionSelected, "#contact-list")
    def copy_contact_card(self, event: OptionList.OptionSelected) -> None:
        if event.option_id in (None, "__none__"):
            return
        handle = str(event.option_id)
        card = self._contact_cards.get(handle)
        if not card:
            return
        ok = copy_text(self.app, card)
        if ok:
            self.notify(f"Copied {handle}'s card")
        else:
            self.notify(
                f"Could not reach clipboard — card for {handle} is in app memory; paste via Add",
                severity="warning",
            )

    @on(Button.Pressed, "#close")
    def close_btn(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class MailScreen(Screen):
    """Three-pane webmail: folders | list | reading."""

    BINDINGS = [
        Binding("c", "compose", "Compose"),
        Binding("a", "contacts", "Contacts"),
        Binding("r", "refresh", "Refresh"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("tab", "focus_next", "Next pane", show=False),
        Binding("shift+tab", "focus_previous", "Prev pane", show=False),
    ]

    def __init__(self, mail: MailService) -> None:
        super().__init__()
        self.mail = mail
        self.folder = Folder.INBOX
        self._messages: list[Message] = []
        self._selected_id: str | None = None
        self._status_note = ""

    def compose(self) -> ComposeResult:
        identity = self.mail.store.get_identity()
        handle = identity.handle if identity else "directmail"
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="folders"):
                yield Label("  Folders")
                yield ListView(
                    ListItem(Label("Inbox"), id="folder-inbox"),
                    ListItem(Label("Sent"), id="folder-sent"),
                    id="folder-list",
                )
            with Vertical(id="message-pane"):
                yield DataTable(id="message-table", cursor_type="row", zebra_stripes=True)
            with Vertical(id="reading-pane"):
                yield Static("Select a message", id="reading-header")
                with VerticalScroll():
                    yield Static("", id="reading-body")
        yield Static(f"  {handle}", id="status-bar")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#message-table", DataTable)
        table.add_columns("●", "From / To", "Preview", "When")
        table.focus()
        self.reload_list()
        identity = self.mail.store.get_identity()
        if identity:
            self.app.title = f"Directmail — {identity.handle}"
        # Check contacts' outboxes once on open (like opening a webmail inbox)
        self.fetch_mail(quiet=True)

    def reload_list(self) -> None:
        """Redraw the message table from local SQLite only."""
        table = self.query_one("#message-table", DataTable)
        table.clear()
        self._messages = self.mail.list_messages(self.folder)
        for msg in self._messages:
            peer = msg.from_handle if self.folder == Folder.INBOX else msg.to_handle
            flag = "●" if not msg.read else " "
            when = msg.created_at.strftime("%Y-%m-%d %H:%M")
            style = "bold" if not msg.read else ""
            table.add_row(
                Text(flag, style=style),
                Text(peer, style=style),
                Text(msg.preview[:60], style=style),
                Text(when, style=style),
                key=msg.id,
            )
        self._paint_status()

    def _paint_status(self) -> None:
        unread = self.mail.unread_count(Folder.INBOX)
        folder_name = "Inbox" if self.folder == Folder.INBOX else "Sent"
        identity = self.mail.store.get_identity()
        handle = identity.handle if identity else ""
        note = f"  ·  {self._status_note}" if self._status_note else ""
        self.query_one("#status-bar", Static).update(
            f"  {handle}  ·  {folder_name}  ·  {len(self._messages)} messages"
            f"  ·  {unread} unread{note}"
        )

    def action_refresh(self) -> None:
        """Check for new mail, then reload the list (one user-facing action)."""
        self.fetch_mail(quiet=False)

    @work(exclusive=True, thread=True)
    def fetch_mail(self, *, quiet: bool = False) -> None:
        self.app.call_from_thread(self._set_status_note, "checking…")
        try:
            n = self.mail.sync()
        except Exception as exc:
            self.app.call_from_thread(self._set_status_note, "check failed")
            self.app.call_from_thread(self.notify, str(exc), severity="error")
            return
        if n:
            note = f"{n} new"
            self.app.call_from_thread(self._set_status_note, note)
            if not quiet:
                self.app.call_from_thread(self.notify, f"{n} new message(s)")
        else:
            self.app.call_from_thread(self._set_status_note, "up to date")
            if not quiet:
                self.app.call_from_thread(self.notify, "Up to date")
        self.app.call_from_thread(self.reload_list)

    def _set_status_note(self, note: str) -> None:
        self._status_note = note
        self._paint_status()

    @on(ListView.Selected, "#folder-list")
    def folder_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "folder-inbox":
            self.folder = Folder.INBOX
            self.query_one("#reading-header", Static).update("Select a message")
            self.query_one("#reading-body", Static).update("")
            self.reload_list()
            self.fetch_mail(quiet=True)
        elif item_id == "folder-sent":
            self.folder = Folder.SENT
            self.query_one("#reading-header", Static).update("Select a message")
            self.query_one("#reading-body", Static).update("")
            self.reload_list()
        self.query_one("#message-table", DataTable).focus()

    @on(DataTable.RowHighlighted, "#message-table")
    def row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None:
            return
        self._show_message(str(event.row_key.value))

    @on(DataTable.RowSelected, "#message-table")
    def row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key is None:
            return
        self._show_message(str(event.row_key.value))

    def _show_message(self, message_id: str) -> None:
        self._selected_id = message_id
        try:
            msg = self.mail.read_message(message_id)
        except Exception as exc:
            self.query_one("#reading-header", Static).update("Error")
            self.query_one("#reading-body", Static).update(str(exc))
            return
        when = msg.created_at.strftime("%Y-%m-%d %H:%M:%S %Z")
        header = (
            f"From: {msg.from_handle}\n"
            f"To:   {msg.to_handle}\n"
            f"Date: {when}"
        )
        self.query_one("#reading-header", Static).update(header)
        self.query_one("#reading-body", Static).update(msg.body_plaintext or "")
        self.reload_list()

    def action_compose(self) -> None:
        contacts = [c.handle for c in self.mail.list_contacts()]

        def done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            to, body = result
            try:
                self.mail.send(to, body)
            except (CardError, Exception) as exc:
                self.notify(str(exc), severity="error")
                return
            self.notify(f"Sent to {to}")
            if self.folder == Folder.SENT:
                self.reload_list()

        self.app.push_screen(ComposeScreen(contacts), done)

    def action_contacts(self) -> None:
        self.app.push_screen(ContactsScreen(self.mail), lambda _: None)

    def action_cursor_down(self) -> None:
        table = self.query_one("#message-table", DataTable)
        table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self.query_one("#message-table", DataTable)
        table.action_cursor_up()


class DirectmailApp(App[None]):
    CSS_PATH = "tui.tcss"
    TITLE = "Directmail"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("question_mark", "help", "Help", show=False),
    ]

    def __init__(self, mail: MailService) -> None:
        super().__init__()
        self.mail = mail

    def on_mount(self) -> None:
        if not self.mail.is_registered():
            self.push_screen(RegisterScreen(), self._after_register)
        else:
            self.push_screen(UnlockScreen(), self._after_unlock)

    def _after_register(self, result: tuple[str, str] | None) -> None:
        if result is None:
            self.exit()
            return
        handle, passphrase = result
        try:
            self.mail.register(passphrase, handle=handle)
        except Exception as exc:
            self.notify(str(exc), severity="error")
            self.push_screen(RegisterScreen(), self._after_register)
            return
        self.notify(f"Welcome, {handle}")
        self.push_screen(MailScreen(self.mail))

    def _after_unlock(self, passphrase: str | None) -> None:
        if passphrase is None:
            self.exit()
            return
        try:
            self.mail.unlock(passphrase)
        except KeystoreAuthError:
            self.notify("Wrong passphrase", severity="error")
            self.push_screen(UnlockScreen(), self._after_unlock)
            return
        self.push_screen(MailScreen(self.mail))

    def action_help(self) -> None:
        self.notify(
            "c compose · a contacts · r refresh (check mail) · ↑↓ navigate · q quit",
            timeout=6,
        )


def run_tui(mail: MailService) -> None:
    DirectmailApp(mail).run()
