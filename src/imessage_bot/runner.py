import fcntl
import logging
import time

from . import config
from .messages import Messages, conversation, eligible
from .model import Model
from .state import State
from .transport import send

LOG = logging.getLogger(__name__)
APPLE_EPOCH = 978307200


def fresh(row, cfg, now):
    date = row["date"]
    timestamp = APPLE_EPOCH + (date / 1_000_000_000 if abs(date) > 1e12 else date)
    return -30 <= now - timestamp <= cfg["max_age_seconds"]


class Runner:
    def __init__(self, messages, state, loader=config.load, factory=Model, sender=send):
        self.messages, self.state = messages, state
        self.loader, self.factory, self.sender = loader, factory, sender
        self.cursor = messages.max_id()  # Never reply to the historical inbox.
        self.pending = {}
        self.model = None
        self.model_key = None
        self.active_cfg = None

    def tick(self, now=None):
        now = time.time() if now is None else now
        cfg = self.loader()  # Invalid/missing config stops the worker.
        # Every settings change discards pending work, including fast off/on.
        # Revision is incremented by all CLI mutations.
        if cfg != self.active_cfg:
            self.pending.clear()
            self.cursor = self.messages.max_id()
            self.active_cfg = cfg
        if not cfg["enabled"]:
            self.cursor = self.messages.max_id()
            self.pending.clear()
            return
        if not cfg["dry_run"] and not cfg["sender_verified"]:
            raise ValueError("Verify the sending number before enabling live replies.")
        for row in self.messages.new(self.cursor):
            self.cursor = max(self.cursor, row["rowid"])
            self.pending.pop(row["chat_id"], None)
            if eligible(row, cfg) and fresh(row, cfg, now):
                self.pending[row["chat_id"]] = (row, now)
        for chat_id, (row, received) in list(self.pending.items()):
            if now - received < cfg["debounce_seconds"]:
                continue
            del self.pending[chat_id]
            if not fresh(row, cfg, now):
                continue
            try:
                self.respond(row, cfg)
            except Exception as exc:
                # Do not copy message bodies, addresses or subprocess arguments into logs.
                LOG.error("Reply skipped (%s). Check setup with doctor.", type(exc).__name__)

    def current(self, row, cfg):
        if self.loader() != cfg or not cfg["enabled"]:
            return None
        history = self.messages.history(row["chat_id"])
        if (not history or history[-1]["rowid"] != row["rowid"]
                or not eligible(history[-1], cfg) or not fresh(history[-1], cfg, time.time())):
            return None
        return conversation(history)

    def respond(self, row, cfg):
        turns = self.current(row, cfg)
        if not turns:
            return
        key = (cfg["model"], cfg["adapter_path"])
        if self.model is None or key != self.model_key:
            self.model = self.factory(cfg)
            self.model_key = key
        model_cfg = dict(cfg)
        instructions = cfg.get("chat_settings", {}).get(str(row["chat_id"]), {}).get("instructions", "").strip()
        if instructions:
            model_cfg["system_prompt"] += "\n\nOwner's instructions for this conversation:\n" + instructions
        reply = self.model.reply(turns, model_cfg)
        # Model loading/generation may take a while. Check stop switch, allowlist,
        # freshness and manual replies again immediately before reserving a send.
        if not reply or not self.current(row, cfg):
            return
        if not self.state.reserve(row, reply, cfg):
            return
        if cfg["dry_run"]:
            LOG.info("Preview saved. Use previews to inspect it.")
            return
        if not self.current(row, cfg):
            self.state.finish(row["message_guid"], "cancelled")
            return
        try:
            self.sender(row["chat_guid"], reply)
        except Exception as exc:
            # Delivery can be ambiguous after an AppleScript timeout: never retry.
            self.state.finish(row["message_guid"], "failed", type(exc).__name__)
            raise
        self.state.finish(row["message_guid"], "sent")
        LOG.info("Reply handed to Messages (delivery not confirmed).")


def run():
    config.private_dir()
    with (config.HOME / "worker.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("A worker is already running.") from None
        cfg = config.load()
        state = State()
        worker = Runner(Messages(cfg["db_path"]), state)
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        LOG.info("Worker ready. Use on/off in another terminal. Ctrl-C stops it.")
        try:
            while True:
                worker.tick()
                time.sleep(config.load()["poll_seconds"])
        except KeyboardInterrupt:
            pass
        finally:
            state.close()
            config.update(lambda value: value.update(enabled=False, revision=value.get("revision", 0)+1))
