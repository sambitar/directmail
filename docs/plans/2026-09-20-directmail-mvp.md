# Directmail MVP Implementation Plan

**Goal:** Local-first encrypted mail over IPFS with contact-card identity and a Textual webmail TUI.

**Architecture:** Each install has `user@machine`, X25519 identity, and an IPNS outbox. Bodies are AES-256-GCM ciphertext on IPFS; message keys are X25519-wrapped to the recipient (same pattern as filerooms). First contact is a pasted contact card only — no mesh search. Receive = sync known contacts’ IPNS outboxes for envelopes addressed to you.

**Tech Stack:** Python 3.11+, cryptography, Textual, SQLite, Kubo HTTP RPC

**Spec:** this conversation

## Global Constraints

- Contact card paste only for first contact (no mesh discovery UI)
- No subject — body only (plaintext → encrypted blob)
- SQLite local index
- Reuse filerooms crypto/keystore/IPFS patterns
- CLI is a Textual app: folders | message list | reading pane; arrow keys
- Never edit protected trunk; work on feature branch

### Task 1: Package + crypto + keystore

**Files:**
- Create: `pyproject.toml`, `src/directmail/{__init__,crypto,keystore}.py`, `tests/test_crypto.py`

- [ ] Crypto: identity keypair, AES-GCM, message wrap/unwrap, keystore seal (domain `directmail`)
- [ ] Keystore: Scrypt passphrase, sealed identity privkey
- [ ] `pytest tests/test_crypto.py -v` PASS

### Task 2: Models, cards, SQLite, mail core (local backend)

**Files:**
- Create: `src/directmail/{models,cards,store,blobs,mail}.py`, `tests/test_mail_local.py`

- [ ] Contact card `dm:v1:handle:ipns:pubkey_b64url`
- [ ] SQLite: identity, contacts, messages
- [ ] Local blob store; send/list/read without IPFS
- [ ] `pytest tests/test_mail_local.py -v` PASS

### Task 3: IPFS outbox publish + sync

**Files:**
- Create: `src/directmail/{ipfs_client,mailbox}.py`, `tests/test_mailbox_memory.py`

- [ ] Add/cat/name publish/resolve; in-memory fake for tests
- [ ] Publish outbox JSON; sync contacts’ outboxes into inbox
- [ ] Tests PASS with memory IPFS

### Task 4: Textual webmail TUI

**Files:**
- Create: `src/directmail/{app,tui}.py`, `src/directmail/tui.tcss`

- [ ] Unlock/setup screens; main: folders | DataTable | body
- [ ] Compose, contacts (add card / show my card), sync
- [ ] Entry point `directmail`

### Task 5: Verify

- [ ] `pytest -v`
- [ ] `pip install -e ".[dev]"` and `directmail --help` / local backend smoke
