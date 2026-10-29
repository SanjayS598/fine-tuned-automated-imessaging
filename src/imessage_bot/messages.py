import sqlite3
from contextlib import closing
from pathlib import Path

from .config import normalize


def connect(path):
    # mode=ro keeps SQLite WAL visibility while preventing writes to Apple's DB.
    db = sqlite3.connect(Path(path).expanduser().resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    return db


def decode_text(row):
    text = row["text"]
    if not text and row["attributedBody"]:
        try:
            from Foundation import NSData, NSUnarchiver
            body = bytes(row["attributedBody"])
            value = NSUnarchiver.unarchiveObjectWithData_(NSData.dataWithBytes_length_(body, len(body)))
            text = str(value.string()) if value is not None else ""
        except Exception:
            # Never guess at binary content; unsupported messages are skipped.
            return ""
    return (text or "").replace("\ufffc", "").strip()


def clean(row):
    return not row["associated_message_type"] and not row["item_type"]


SELECT = """
SELECT m.ROWID AS rowid, m.guid AS message_guid, m.text, m.attributedBody,
       m.is_from_me, m.date, m.associated_message_type, m.item_type,
       c.ROWID AS chat_id, c.guid AS chat_guid, c.service_name AS service,
       c.style, h.id AS sender,
       (SELECT count(*) FROM chat_handle_join ch WHERE ch.chat_id=c.ROWID) AS participants,
       (SELECT hh.id FROM chat_handle_join ch JOIN handle hh ON hh.ROWID=ch.handle_id
        WHERE ch.chat_id=c.ROWID LIMIT 1) AS contact
FROM message m JOIN chat_message_join cm ON cm.message_id=m.ROWID
JOIN chat c ON c.ROWID=cm.chat_id LEFT JOIN handle h ON h.ROWID=m.handle_id
"""


class Messages:
    def __init__(self, path):
        self.path = path

    def query(self, sql, args=()):
        with closing(connect(self.path)) as db:
            return [dict(row) for row in db.execute(sql, args)]

    def max_id(self):
        return self.query("SELECT coalesce(max(ROWID),0) AS n FROM message")[0]["n"]

    def new(self, after):
        return self.query(SELECT + " WHERE m.ROWID>? ORDER BY m.ROWID LIMIT 500", (after,))

    def history(self, chat_id, limit=20):
        return list(reversed(self.query(SELECT + " WHERE c.ROWID=? ORDER BY m.ROWID DESC LIMIT ?", (chat_id, limit))))

    def chats(self):
        return self.query("""SELECT c.ROWID AS chat_id, c.service_name AS service,
            h.id AS contact, count(cm.message_id) AS messages
            FROM chat c JOIN chat_handle_join ch ON ch.chat_id=c.ROWID
            JOIN handle h ON h.ROWID=ch.handle_id
            LEFT JOIN chat_message_join cm ON cm.chat_id=c.ROWID
            WHERE c.style=45 AND (SELECT count(*) FROM chat_handle_join x WHERE x.chat_id=c.ROWID)=1
            GROUP BY c.ROWID ORDER BY messages DESC""")

    def all_history(self, chat_id):
        return self.query(SELECT + " WHERE c.ROWID=? ORDER BY m.date,m.ROWID", (chat_id,))

    def training_chats(self):
        return self.query("""SELECT c.ROWID AS chat_id,c.service_name AS service,
            (SELECT h.id FROM chat_handle_join j JOIN handle h ON h.ROWID=j.handle_id
             WHERE j.chat_id=c.ROWID LIMIT 1) AS contact
            FROM chat c WHERE EXISTS(SELECT 1 FROM chat_message_join j JOIN message m ON m.ROWID=j.message_id
                WHERE j.chat_id=c.ROWID AND m.is_from_me=1)
            ORDER BY c.ROWID""")

    def training_history(self,chat_id):
        return self.query(SELECT + " WHERE c.ROWID=? AND m.is_from_me=1 ORDER BY m.date,m.ROWID",(chat_id,))

    def orphan_history(self):
        return self.query("""SELECT m.ROWID AS rowid,m.guid AS message_guid,m.text,m.attributedBody,
            m.is_from_me,m.date,m.associated_message_type,m.item_type,0 AS chat_id
            FROM message m WHERE m.is_from_me=1 AND NOT EXISTS(SELECT 1 FROM chat_message_join j WHERE j.message_id=m.ROWID)
            ORDER BY m.date,m.ROWID""")


def eligible(row, cfg):
    try:
        contact, sender = normalize(row["contact"] or ""), normalize(row["sender"] or "")
    except ValueError:
        return False
    chat = cfg.get("chat_settings", {}).get(str(row["chat_id"]))
    approved = chat["enabled"] if chat is not None else contact in cfg["allowed_contacts"]
    return (not row["is_from_me"] and clean(row) and row["participants"] == 1
            and row["style"] == 45 and row["service"] in ("iMessage", "SMS")
            and contact == sender and approved
            and contact not in cfg["own_identities"] and bool(decode_text(row)))


def conversation(rows, max_chars=6000, keep_leading=False):
    turns = []
    for row in rows:
        if not clean(row):
            continue
        text = decode_text(row)
        if not text:
            continue
        role = "assistant" if row["is_from_me"] else "user"
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"] = turns[-1]["content"] + "\n" + text
            if max_chars:
                turns[-1]["content"] = turns[-1]["content"][-max_chars:]
        else:
            turns.append(dict(role=role, content=text[-max_chars:] if max_chars else text))
    if keep_leading and turns and turns[0]["role"] == "assistant":
        turns.insert(0, dict(role="user", content="Continue the conversation."))
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    return turns
