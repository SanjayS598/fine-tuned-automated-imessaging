import hashlib
import sqlite3
import time
import uuid

from . import config


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


class State:
    def __init__(self, path=None):
        config.private_dir()
        self.db = sqlite3.connect(path or config.HOME / "state.sqlite3", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS attempts (
            guid TEXT PRIMARY KEY, chat_id INTEGER, created REAL, status TEXT,
            reply TEXT, reply_hash TEXT, error TEXT)""")
        self.db.commit()

    def reserve(self, row, reply, cfg, now=None):
        now = time.time() if now is None else now
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            recent = self.db.execute("SELECT count(*) FROM attempts WHERE created>? AND status!='manual_preview'",
                                     (now - 3600,)).fetchone()[0]
            last = self.db.execute("SELECT max(created) FROM attempts WHERE chat_id=? AND status!='manual_preview'",
                                   (row["chat_id"],)).fetchone()[0]
            if recent >= cfg["max_per_hour"] or (last is not None and now-last < cfg["cooldown_seconds"]):
                return False
            cur = self.db.execute("INSERT OR IGNORE INTO attempts VALUES (?,?,?,?,?,?,?)",
                                  (row["message_guid"], row["chat_id"], now,
                                   "preview" if cfg["dry_run"] else "sending",
                                   reply, digest(reply), None))
            return cur.rowcount == 1

    def finish(self, guid, status, error=None):
        with self.db:
            self.db.execute("UPDATE attempts SET status=?,error=? WHERE guid=?", (status,error,guid))

    def generated(self, chat_id, text):
        return self.db.execute("SELECT 1 FROM attempts WHERE chat_id=? AND reply_hash=? AND status IN ('sent','sending','failed')",
                               (chat_id, digest(text))).fetchone() is not None

    def save_manual_preview(self,row,reply):
        guid='manual-preview-'+uuid.uuid4().hex
        with self.db:
            self.db.execute('INSERT INTO attempts VALUES (?,?,?,?,?,?,?)',
                            (guid,row['chat_id'],time.time(),'manual_preview',reply,digest(reply),None))
        return guid

    def previews(self,chat_id=None):
        query='SELECT guid,chat_id,created,status,reply,error FROM attempts'
        args=()
        if chat_id is not None:
            query+=' WHERE chat_id=?'
            args=(chat_id,)
        return self.db.execute(query+' ORDER BY created DESC LIMIT 20',args).fetchall()

    def close(self):
        self.db.close()
