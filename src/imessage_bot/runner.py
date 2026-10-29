import fcntl
import logging
import time

from . import config
from .messages import Messages, conversation, eligible,clean,decode_text
from .model import Model,reply_config,sends_automatically
from .state import State
from .transport import send,SendError
from .context import CONTEXT_MESSAGES

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
            # The UI records the inbox cursor at the click, so a message arriving
            # before this poll is still observed. Old workers/configs start fresh.
            if cfg.get('watch_after_rowid') is not None and (self.active_cfg is not None or time.time()-cfg.get('watch_after_at',0)<10):
                self.cursor=cfg['watch_after_rowid']
            else:
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
            if row['is_from_me']:
                self.pending.pop(row['chat_id'],None)
            if eligible(row, cfg) and fresh(row, cfg, now):
                self.pending[row["chat_id"]] = (row, now)
                self.state.event(row['chat_id'],'queued','Waiting briefly for a message burst')
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
                self.state.event(chat_id,'failed',str(exc) if isinstance(exc,SendError) else type(exc).__name__)

    def current(self, row, cfg):
        if self.loader() != cfg or not cfg["enabled"]:
            return None
        history = self.messages.history(row["chat_id"],limit=cfg.get('context_messages',CONTEXT_MESSAGES))
        self.context_message_count=len(history)
        if any(item['is_from_me'] and item['rowid']>row['rowid'] for item in history):
            return None
        text_rows=[item for item in history if clean(item) and decode_text(item)]
        if (not text_rows or text_rows[-1]["rowid"] != row["rowid"]
                or not eligible(text_rows[-1], cfg) or not fresh(text_rows[-1], cfg, time.time())):
            return None
        return conversation(history,label_senders=row['style']==43,label_dates=True,keep_leading=True)

    def respond(self, row, cfg):
        turns = self.current(row, cfg)
        if not turns:
            self.state.event(row['chat_id'],'cancelled','A newer message, manual reply or settings change cancelled this reply')
            return
        key = (cfg["model"], cfg["adapter_path"])
        if self.model is None or key != self.model_key:
            self.state.event(row['chat_id'],'loading_model','Loading the local models')
            self.model = self.factory(cfg)
            self.model_key = key
        model_cfg = reply_config(cfg,row['chat_id'],group=row['style']==43)
        model_cfg['_context_message_count']=self.context_message_count
        model_cfg['_progress']=lambda stage:self.state.event(row['chat_id'],stage)
        reply = self.model.reply(turns, model_cfg)
        # Model loading/generation may take a while. Check stop switch, allowlist,
        # freshness and manual replies again immediately before reserving a send.
        if not reply or not self.current(row, cfg):
            self.state.event(row['chat_id'],'skipped','No usable reply, or the conversation changed during generation')
            return
        automatic=sends_automatically(cfg,row['chat_id'])
        if automatic and not cfg['sender_verified']:
            raise ValueError('Verify the sending number before automatic sending.')
        effective=dict(cfg,dry_run=not automatic)
        if not self.state.reserve(row, reply, effective):
            self.state.event(row['chat_id'],'skipped',self.state.last_block_reason or 'Reply could not be reserved')
            return
        self.state.context(row['message_guid'],getattr(self.model,'last_context',{}))
        if not automatic:
            self.state.event(row['chat_id'],'preview','Draft ready; waiting for Send')
            LOG.info("Preview saved. Use previews to inspect it.")
            return
        if not self.current(row, cfg):
            self.state.finish(row["message_guid"], "cancelled")
            return
        try:
            self.state.event(row['chat_id'],'sending','Handing the reply to Messages')
            self.sender(row["chat_guid"], reply)
        except Exception as exc:
            # Delivery can be ambiguous after an AppleScript timeout: never retry.
            self.state.finish(row["message_guid"], "failed",str(exc) if isinstance(exc,SendError) else type(exc).__name__)
            raise
        self.state.finish(row["message_guid"], "sent")
        self.state.event(row['chat_id'],'sent','Reply handed to Messages; checking delivery status')
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
