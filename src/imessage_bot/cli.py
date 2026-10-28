import argparse
import fcntl
import json
import platform
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from . import config
from .messages import Messages


def doctor():
    cfg = config.load()
    print(f"Platform: {platform.system()} {platform.machine()}")
    print(f"Private settings: {config.HOME / 'config.json'}")
    try:
        print(f"Messages database: readable (latest row {Messages(cfg['db_path']).max_id()})")
    except sqlite3.Error:
        print("Messages database: BLOCKED. Give the hosting app/terminal Full Disk Access in macOS Settings, then restart it.")
    try:
        from importlib.metadata import version
        print(f"MLX LM: {version('mlx-lm')}")
    except Exception:
        print("MLX LM: not installed. Run uv sync --extra mac.")
    try:
        import Foundation  # noqa: F401
        print("Rich-text message decoder: installed")
    except ImportError:
        print("Rich-text decoder: missing. Run uv sync --extra mac.")
    print("Trained adapter:", "present" if (Path(cfg['adapter_path'])/'personal-model.json').exists() else "not trained yet")
    print("Sending number:", "manually verified" if cfg['sender_verified'] else "needs verification in Messages Settings")
    print("Apple Automation permission is requested on the first live send; doctor sends nothing.")


def main():
    parser = argparse.ArgumentParser(description="Local personal iMessage bot")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create private config, paused and preview-only")
    init.add_argument("--number", required=True)
    for name in ("doctor", "status", "chats", "on", "off", "toggle", "run", "previews", "verify-sender", "chat"):
        sub.add_parser(name)
    for name in ("allow", "deny", "identity"):
        command = sub.add_parser(name)
        command.add_argument("contact")
    mode = sub.add_parser("mode")
    mode.add_argument("value", choices=["preview", "live"])
    export = sub.add_parser("export")
    export.add_argument("--contact", action="append", help="Restrict training to this contact (repeatable). Default: all direct chats")
    train = sub.add_parser("train")
    train.add_argument("--iters", type=int, default=200)
    args = parser.parse_args()
    try:
        dispatch(args)
    except (ValueError, FileNotFoundError, sqlite3.Error, ImportError, subprocess.CalledProcessError) as exc:
        if isinstance(exc, sqlite3.Error):
            parser.exit(1, "Messages database unavailable. Run doctor and grant Full Disk Access.\n")
        if isinstance(exc, FileNotFoundError):
            parser.exit(1, "Required file missing. Run init/doctor and finish training setup.\n")
        if isinstance(exc, ImportError):
            parser.exit(1, "Missing dependency. Run uv sync --extra mac.\n")
        parser.exit(1, f"{exc}\n")


def dispatch(args):
    cmd = args.command
    if cmd == "init":
        config.initialize(args.number)
        print(f"Created {config.HOME / 'config.json'}. Paused; preview mode; no approved contacts.")
        return
    cfg = config.load()
    if cmd == "doctor":
        doctor()
    elif cmd == "status":
        with (config.HOME / "worker.lock").open("a+") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                running = False
            except BlockingIOError:
                running = True
        print(json.dumps(dict(worker_running=running, enabled=cfg["enabled"],
                              mode="preview" if cfg["dry_run"] else "live",
                              allowed_contacts=cfg["allowed_contacts"], sender_verified=cfg["sender_verified"]), indent=2))
    elif cmd == "chats":
        for row in Messages(cfg["db_path"]).chats():
            print(f"{row['chat_id']:>6}  {row['service']:<10} {row['contact']}  ({row['messages']} messages)")
    elif cmd in ("allow", "deny", "identity"):
        contact = config.normalize(args.contact)
        def edit(value):
            key = "own_identities" if cmd == "identity" else "allowed_contacts"
            entries = set(value[key])
            if cmd == "deny":
                entries.discard(contact)
            else:
                entries.add(contact)
            if cmd == "allow" and contact in value["own_identities"]:
                raise ValueError("Your own identities cannot be auto-reply recipients.")
            value[key] = sorted(entries)
        mutate(edit)
        print("Contact settings updated.")
    elif cmd in ("on", "off", "toggle"):
        def edit(value):
            enabled = (not value["enabled"]) if cmd == "toggle" else cmd == "on"
            if enabled and not value["allowed_contacts"]:
                raise ValueError("Add an approved contact with allow first.")
            if enabled and not value["dry_run"] and not value["sender_verified"]:
                raise ValueError("Run verify-sender after checking your number in Messages Settings.")
            value["enabled"] = enabled
        result = mutate(edit)
        print("Enabled. Start run if no worker is running." if result["enabled"] else "Paused.")
    elif cmd == "mode":
        def edit(value):
            value.update(dry_run=args.value == "preview", enabled=False)
        mutate(edit)
        print(f"Mode: {args.value}. Paused. Use on to enable.")
    elif cmd == "verify-sender":
        print("In Messages > Settings > iMessage, confirm your number is enabled and selected under 'Start new conversations from'.")
        print("For SMS, enable this Mac under your iPhone's Text Message Forwarding settings.")
        number = input("Enter the sending phone number you verified: ").strip()
        if config.normalize(number) != cfg["own_identities"][0]:
            raise ValueError("Number does not match your configured primary identity.")
        mutate(lambda value: value.update(sender_verified=True))
        print("Recorded your verification. This app cannot independently set or verify Apple's sending identity.")
    elif cmd == "run":
        from .runner import run
        run()
    elif cmd == "previews":
        from .state import State
        state = State()
        try:
            for row in reversed(state.previews()):
                print(f"{datetime.fromtimestamp(row['created']).isoformat(timespec='seconds')} chat={row['chat_id']} {row['status']}")
                print(row["reply"])
                if row["error"]:
                    print("Error:", row["error"])
        finally:
            state.close()
    elif cmd == "export":
        from .training import export
        export({config.normalize(x) for x in args.contact} if args.contact else None)
    elif cmd == "train":
        from .training import train
        train(args.iters)
    elif cmd == "chat":
        from .model import Model
        model = Model(cfg)
        history = []
        print("Local test chat. Type /quit to exit. Nothing is sent to Messages.")
        while True:
            try:
                text = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text == "/quit":
                break
            if not text:
                continue
            history.append(dict(role="user", content=text))
            reply = model.reply(history[-15:], cfg)
            print("Bot:", reply)
            history.append(dict(role="assistant", content=reply))


def mutate(change):
    def revisioned(cfg):
        change(cfg)
        cfg["revision"] = cfg.get("revision", 0) + 1
    return config.update(revisioned)


if __name__ == "__main__":
    main()
