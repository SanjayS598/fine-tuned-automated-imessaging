# fine-tuned-automated-imessaging

A local Mac bot that learns your texting style from your own outgoing messages
and automatically replies in selected one-to-one Messages conversations.
Built from the workflow in [devk03/imessage-finetuning](https://github.com/devk03/imessage-finetuning);
see [NOTICE.md](NOTICE.md) and the preserved [original scripts](upstream/).

It starts **paused, in preview mode, with an empty contact allowlist**.
An always-running worker watches only new incoming messages. You control which
contacts it can reply to and whether it saves previews or sends replies.

## Open your inbox

After installing dependencies and initializing your number below, double-click
**`start.command`** in Finder, or run:

```sh
./start.command
# Alternatively:
uv run --extra mac imessage-bot ui
```

The app runs at `http://127.0.0.1:8765` on your Mac. The launcher opens a private
browser session automatically; use it again if the server restarts. Keep its
terminal open. The inbox includes every conversation in your Mac's Messages
database, including group chats and RCS. Search conversations, load earlier
messages, and view available attachments. Missing or iCloud-only attachment
files cannot be displayed; attachment previews are limited to 40 MB.

Select a chat to:

- Toggle **Auto reply** for that exact conversation.
- Add **A little direction** instructions, then click **Save instructions**.
- Choose **Preview** or **Send automatically**, and use the top master switch
  to enable or pause replies across all selected chats.
- Inspect generated previews and delivery attempts in **Activity**.

**Setup** shows the Messages connection, lets you confirm your sending number,
and starts local training. Training pauses replies and continues while you
browse. Once trained, enabling the master switch starts the reply worker
automatically. Closing the UI browser tab leaves the server and worker running;
stopping the UI server pauses replies and stops the worker it started.

Auto replies currently support direct iMessage and SMS conversations. Group
chats and RCS are available to browse. Per-chat settings override the CLI's
contact allowlist, so two conversations with the same contact can be controlled
independently. Saved chat instructions are supplied to the model alongside
the conversation, and never become global instructions for other chats.

The UI binds only to loopback. Message APIs and attachments require a private
session, and changes require same-origin requests. No conversations, phone
numbers, or screenshots are published with the public repository.

## Training progress and Contacts

The main inbox shows real training progress: export coverage, preparation stage,
completed/total optimizer steps, percentage of the current stage, elapsed time,
and an estimated remaining time. Estimates update from MLX reports and can vary
with conversation length. Reactions, system events and attachment-only messages
are counted but excluded from text training. Incoming text provides context;
your outgoing text supplies the assistant targets. Messages without chat links
are also scanned, with standalone owner messages using neutral context.

Use **Contacts → Connect contacts** to import names from macOS Contacts. Grant
**Inbox Contacts** (or the app hosting Inbox) access under **System Settings →
Privacy & Security → Contacts** if needed, then retry. A small helper app is
compiled locally on first use so macOS can display a proper permission prompt;
this requires Apple's command line developer tools. It only reads Contacts.
Names match normalized phone numbers and email
addresses. Only names and addresses are cached, in ignored `.local/contacts.json`.
Contact names are used for display and do not change the recipient identifiers
or get added to training targets. Limited Contacts access shows only the names
macOS permits. Use Refresh contacts after editing your address book.

The interface uses a monochrome glass theme: translucent panels, rounded
controls, and black-and-white message bubbles. Reduced-motion and
reduced-transparency preferences are supported.

## Hardware and accounts

- An Apple Silicon Mac, macOS Messages signed in to your Apple Account, and Python 3.12 with [uv](https://docs.astral.sh/uv/).
- Start with 16 GB unified memory and the default 4-bit Qwen2.5 1.5B model.
  This is a practical starting point, not a guaranteed memory budget. Training
  uses batch size 1, sequence length 1024, and gradient checkpointing.
- **No separate NVIDIA GPU, cloud server, or paid model API is required.**
  MLX uses your Mac's GPU. Larger models may need more memory or a different
  training setup; this implementation specifically targets MLX on Apple Silicon.
- Your Mac must stay awake, logged in, connected, and running the worker to reply.
  This is not a bot running directly on the iPhone.

## 1. Install and initialize

```sh
uv sync --extra mac
uv run imessage-bot init --number YOUR_PHONE_NUMBER
uv run imessage-bot doctor
```

Use your full international number, or a US ten-digit number. Your number goes
only into the ignored `.local/config.json`, never into public source files.
If already initialized, skip `init`; it will preserve existing settings.

In **System Settings → Privacy & Security → Full Disk Access**, enable the app
hosting the worker (your terminal, or Codex if running from Codex), then fully
quit and reopen that app. `doctor` should report the database as readable.
The first live send may also request permission under **Automation → Messages**.

## 2. Configure your existing number

On your Mac, open **Messages → Settings → iMessage**. Enable your phone number
and choose it under **Start new conversations from**. Add any other identities
you receive messages at so the bot excludes them:

```sh
uv run imessage-bot identity your-apple-id@example.com
uv run imessage-bot verify-sender
```

For carrier SMS, enable this Mac in **iPhone Settings → Apps → Messages → Text
Message Forwarding**, or use Messages in iCloud on both devices. The iPhone and
Mac need the same Apple Account. See [Apple's forwarding setup](https://support.apple.com/en-us/102545).

The bot replies into existing chats through Messages, preserving their service.
It cannot register a phone number or override Apple's sending account. Check an
actual message on the receiving device to confirm the identity. AppleScript
acceptance does not prove delivery. RCS automation is not supported here;
only conversations whose database service is `iMessage` or `SMS` are eligible.

## 3. Export your writing and train

```sh
uv run imessage-bot chats
# Default: all conversations, including groups and RCS.
uv run imessage-bot export
# Optional: restrict training to selected contacts (repeat --contact).
# uv run imessage-bot export --contact +12025550101
uv run imessage-bot train --iters 200
uv run imessage-bot chat
```

The model downloads from Hugging Face on first use. Training and inference run
locally. The dataset maps incoming text to `user` and **your outgoing replies**
to `assistant`. Prompt masking trains on the final assistant reply. Six-hour
conversation sessions stay together in the train/validation split, avoiding
overlapping context across those splits. At least three usable sessions are
required. Reactions, system entries, and attachment-only messages are skipped;
attributed text is decoded with macOS Foundation when possible. Unsupported
content is skipped rather than guessed.

Before training, examples are checked against the real tokenizer's 1024-token
budget. Older context pairs are removed first. Long replies are split into bounded
targets with recent incoming context, preserving their text instead of dropping
them. Outbound-first sessions receive a neutral conversation prompt.

Review `.local/data/` and try the local chat before enabling live replies.
Fine-tuning aims to learn style; it cannot guarantee accurate facts or a perfect
imitation. Generated bot replies are tracked and sessions containing their
matching outgoing text are excluded from future exports. This filtering only
covers sends recorded by this bot installation.

The GUI trains a new version in `.local/runs/<run-id>/` and preserves your
current model until the new run completes successfully. Each GUI run makes one
full pass through every prepared training example (batch size one); about 10%
of conversation sessions are held out for validation. The original 200-step CLI
command above is a short smoke run, not a full training pass. To train a manual
replacement, set a different `adapter_path` while paused and train again.
Changing the base `model` also requires a matching adapter. Restart the worker
after changing model files or database paths.

## 4. Choose contacts and preview

```sh
uv run imessage-bot allow +12025550101
uv run imessage-bot mode preview
uv run imessage-bot run
```

Leave `run` open. In a second terminal, in this same project folder:

```sh
uv run imessage-bot on
uv run imessage-bot status
# After an approved contact sends a NEW message:
uv run imessage-bot previews
uv run imessage-bot off
```

Previews are saved locally and never sent. Changing settings discards pending
replies. Starting or restarting the worker ignores the existing inbox, including
messages received while it was stopped. Enabling after a pause also starts fresh.

## 5. Enable automated replies

After testing your model and verifying the number:

```sh
uv run imessage-bot mode live
uv run imessage-bot on
# The worker must still be running in your other terminal.
```

Controls you can use at any time:

| Command | Effect |
| --- | --- |
| `uv run imessage-bot off` | Pause new replies |
| `uv run imessage-bot on` | Enable replies to approved contacts |
| `uv run imessage-bot toggle` | Switch enabled/paused |
| `uv run imessage-bot deny +12025550101` | Remove a contact |
| `uv run imessage-bot mode preview` | Stop sending and return to previews; pauses replies |
| `uv run imessage-bot status` | Show worker, mode, and approved contacts |
| Ctrl-C in worker terminal | Stop worker and pause replies |

The worker waits 8 seconds after the last incoming text to combine a burst.
It skips messages older than 3 minutes, group chats, reactions, your own
identities, and unapproved senders. It checks again after generation for manual
replies, newer incoming messages, removed contacts, or a pause. Defaults limit
it to one reply per contact chat per 30 seconds and 30 total attempts per hour.
Rate-limited or failed attempts are not queued for later delivery.

A persistent reservation prevents duplicate attempts after errors. If a send
times out, it is recorded as failed and never automatically retried because
delivery is uncertain. There is a small unavoidable gap between the final
check and handing text to Messages: `off` cannot recall a message already handed
off. Only one worker may run per private data directory.

## Private data and configuration

Everything personal lives under ignored `.local/`: settings, database of send
attempts and preview text, training examples, and adapters. Never force-add this
directory to Git. The public repository contains code and synthetic tests only.
No message database needs to be copied into the project; it is opened read-only
with SQLite WAL support. This project does not upload conversations to GitHub,
Hugging Face, or an inference API. Model downloads still require internet.

You can edit `.local/config.json` while paused to change the model, paths,
prompt, timing, or limits. Prefer CLI controls for mode, contacts, and toggles.
`IMESSAGE_BOT_HOME=/absolute/private/path` overrides the private data directory;
use the same value in both terminals. Keep it outside any public repository.

## Tests

```sh
uv run python -m unittest discover -s tests -v
```

Core tests use synthetic conversations and require no Apple account, GPU, or
access to personal messages. Real macOS permissions, trained-model quality,
AppleScript delivery, and iPhone forwarding need testing on your own devices.
