"""Local training job with an observable, content-free status file."""
import fcntl
import json
import os
import subprocess
import sys
import time

from . import config


def write_status(**values):
    path = config.HOME / "training-status.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(dict(updated=time.time(), **values)))
    temporary.replace(path)


def status():
    path = config.HOME / "training-status.json"
    result = json.loads(path.read_text()) if path.exists() else {"stage": "idle"}
    if result.get("stage") in ("exporting", "training"):
        try:
            os.kill(result["pid"], 0)
        except (OSError, KeyError):
            result.update(stage="interrupted", error="Training stopped. Start it again from the setup panel.")
    return result


def main():
    config.private_dir()
    with (config.HOME / "training.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        pid = os.getpid()
        try:
            config.update(lambda c: c.update(enabled=False, revision=c.get("revision", 0)+1))
            write_status(stage="exporting", pid=pid)
            subprocess.run([sys.executable, "-m", "imessage_bot.cli", "export"], check=True)
            write_status(stage="training", pid=pid)
            subprocess.run([sys.executable, "-m", "imessage_bot.cli", "train", "--iters", "200"], check=True)
            write_status(stage="ready", pid=pid)
        except Exception:
            write_status(stage="failed", pid=pid, error="Training failed. Details are in the private .local/training.log.")
            raise


if __name__ == "__main__":
    main()
