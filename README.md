# Directmail

### Machine-to-machine mail. Encrypted. No subjects. No servers that read you.

```
   you@laptop  ──X25519──►  ciphertext@IPFS  ──IPNS──►  them@studio
         ▲                                                  │
         └──────── sealed keystore · SQLite · TUI ──────────┘
```

**Directmail** is terminal webmail for people who think SMTP is a museum exhibit and “trust the cloud” is a personality flaw.

You run it on your machine. They run it on theirs. Bodies leave your disk as **AES-256-GCM**. Keys never leave a **Scrypt-sealed keystore**. Discovery is a **contact card** you paste once — not a global phone book, not a mesh gossip fishbowl.

No subject line. Just the message. Like passing a sealed envelope across a bench, except the bench is a content-addressed planet.

---

## Why this exists

| The old world | Directmail |
|---------------|------------|
| `user@gmail.com` → someone else’s disk | `user@machine` → your disk |
| Provider can read / subpoena / “improve” | Ciphertext on IPFS; unwrap is local |
| DNS + MX + spam industrial complex | IPNS outbox + contact cards |
| Subjects, tracking pixels, HTML hell | Body only. Text. Done. |
| Web UI that phones home | Textual TUI that stays home |

Built for **geeks, operators, lab mates, and paranoid friends** who already run Kubo and think “E2EE” should mean the key is actually on *their* laptop.

---

## Quick start

```bash
git clone https://github.com/sambitar/directmail.git
cd directmail
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# One command — downloads Kubo on first run if needed, starts the daemon, opens the TUI
directmail
```

You do **not** need to run `ipfs daemon` yourself. On launch, Directmail checks
`http://127.0.0.1:5001` and, if nothing is there, installs Kubo under `.tools/`
(or `$DIRECTMAIL_TOOLS` / `~/.local/share/directmail/tools`) and starts it.

Optional helpers (only if you want to manage Kubo by hand):

```bash
directmail ipfs status
directmail ipfs stop
directmail ipfs start   # same autostart path the UI uses
```

Already have a system Kubo on `:5001`? Directmail will use it and skip starting its own.

Data lives in `~/.local/share/directmail` (override with `--data-dir` / `DIRECTMAIL_DATA`).

---

## First contact (the only handshake)

There is no search. There is no “find friends on the mesh.” That is a feature.

1. Both of you create an identity: `user@hostname` + passphrase.
2. Hit **a** (Contacts) → **y** / **Copy my card**.
3. Paste the card to your peer (chat, USB, paper, carrier pigeon).
4. They paste it into **Add someone** → **Save contact**. You do the same with theirs.
5. **c** Compose → write a body → **Ctrl+Enter** Send.
6. They open Inbox (or **r** Refresh). Mail appears. Decrypt happens locally.

Card wire format:

```text
dm:v1:<handle>:<ipns-or-mailbox>:<pubkey_b64url>
```

Trust is **TOFU**: the card *is* the introduction. Verify fingerprints in person if you’re that kind of paranoid (you should be).

---

## The TUI (webmail, but in a terminal)

Arrow keys. Panes. Not a 1998 `dialog` menu.

| Key | Action |
|-----|--------|
| ↑ ↓ | Move the message list |
| Tab | Jump panes |
| **c** | Compose (To picker + body) |
| **a** | Contacts — copy/paste cards |
| **r** | Refresh — pull contacts’ outboxes + reload |
| **q** | Quit |

Opening **Inbox** refreshes quietly. Status bar shows `checking…` → `up to date` / `N new`.

---

## How it works (technical)

### Identity

- On register: **X25519** identity keypair.
- Private key sealed at rest: **Scrypt** → AES-GCM keystore (`directmail` domain separation).
- Public handle is local naming: `alice@studio`. It is **not** a global unique name.

### Contact card

Binds three things you need for first send/receive:

1. Handle (what you type in To)
2. Mailbox pointer (`/ipns/…` or local absolute path in demo mode)
3. 32-byte X25519 public key

Paste once. SQLite remembers forever (until you don’t).

### Send path

1. Generate a random **AES-256** message key.
2. Encrypt UTF-8 body with **AES-GCM**; AAD binds `message_id`, `from`, `to`.
3. Put ciphertext on IPFS → `ipfs://<cid>`.
4. **Wrap** the message key to the recipient’s pubkey: ephemeral X25519 → HKDF-SHA256 → AES-GCM (AAD binds `message_id`).
5. Append an envelope to your **outbox document** and **IPNS-publish** it.

### Receive path (Refresh)

1. For each contact, resolve their IPNS → fetch outbox JSON.
2. Import envelopes where `to == you` and id is new.
3. On open: unwrap message key with your privkey → decrypt body → cache plaintext in SQLite.

Nothing pushes. Refresh (and Inbox focus) **pulls**. Same honesty as `git fetch`.

### Outbox schema

```json
{
  "schema": "directmail-outbox-v1",
  "handle": "alice@laptop",
  "pubkey_b64": "…",
  "updated_at": "…",
  "messages": [
    {
      "id": "…",
      "from": "alice@laptop",
      "to": "bob@studio",
      "created_at": "…",
      "body_cid": "ipfs://…",
      "wrapped_key_b64": "…",
      "ciphertext_sha256": "…"
    }
  ]
}
```

Public metadata is intentional: who mailed whom, when, and a hash of **ciphertext** (not plaintext). Contents stay sealed.

### Local stack

| Piece | Role |
|-------|------|
| SQLite | Identity, contacts, inbox/sent index |
| Keystore dir | Sealed identity private key |
| Kubo HTTP API | `add` / `cat` / `name publish|resolve` (system or `directmail ipfs`) |
| Textual | Webmail TUI |
| `cryptography` | X25519, AES-GCM, Scrypt, HKDF |

Crypto construction mirrors the hardened patterns in **filerooms** (room-key wrap → message-key wrap), with Directmail domain strings.

### What this is not

- Not a replacement for email gateways, mailing lists, or spam filtering.
- Not a global identity / PKI. Cards are TOFU.
- Not anonymous against traffic analysis on a public IPFS swarm (metadata exists).
- Not multi-device sync for one identity (yet). One machine, one keystore, one outbox key.

---

## Architecture

```text
┌─────────────┐     compose      ┌──────────────┐
│  Textual TUI │ ───────────────► │  MailService │
└─────────────┘                  └──────┬───────┘
                                        │
                 ┌──────────────────────┼──────────────────────┐
                 ▼                      ▼                      ▼
           ┌──────────┐          ┌────────────┐         ┌────────────┐
           │ SQLite   │          │  Keystore  │         │ BlobStore  │
           │ metadata │          │  (sealed)  │         │ IPFS/local │
           └──────────┘          └────────────┘         └─────┬──────┘
                                                              │
                                                        ┌─────▼──────┐
                                                        │ IPNS outbox│
                                                        └────────────┘
```

---

## Dev / test

```bash
pytest -v
```

In-memory IPFS fakes cover two-peer card exchange → send → refresh → decrypt without a daemon.

---

## License

MIT — fork it, run it on two laptops, mail like machines.

---

*Sealed envelopes. Content-addressed. Keyboard-driven. Built for the terminal crowd.*
