"""Read-only inbox views. No message text is stored outside Apple's database."""
import mimetypes
from pathlib import Path

from .config import normalize
from .messages import Messages, decode_text

APPLE_EPOCH = 978307200


def unix_date(value):
    if not value:
        return None
    return APPLE_EPOCH + (value/1e9 if abs(value) > 1e12 else value)


def supports_replies(chat, cfg):
    if chat['service'] not in ('iMessage','SMS','RCS') or chat['style'] not in (43,45):
        return False
    members=[]
    for value in chat['participants']:
        try:members.append(normalize(value))
        except ValueError:continue
    others=[value for value in members if value not in cfg['own_identities']]
    return bool(others) and (chat['style']==43 or len(chat['participants'])==1)


class Inbox:
    def __init__(self, path):
        self.source = Messages(path)

    def chats(self):
        rows = self.source.query("""
            SELECT c.ROWID AS id,c.guid,c.display_name,c.chat_identifier,c.service_name AS service,c.style,
                (SELECT group_concat(h.id,char(31)) FROM chat_handle_join ch JOIN handle h ON h.ROWID=ch.handle_id WHERE ch.chat_id=c.ROWID) AS handles,
                m.text,m.attributedBody,m.date,m.is_from_me,m.is_read,m.cache_has_attachments,
                m.ROWID AS latest_id
            FROM chat c LEFT JOIN message m ON m.ROWID=(
                SELECT cm.message_id FROM chat_message_join cm WHERE cm.chat_id=c.ROWID ORDER BY cm.message_id DESC LIMIT 1)
            ORDER BY coalesce(m.date,0) DESC,c.ROWID DESC
        """)
        result = []
        for row in rows:
            participants = (row.pop("handles") or "").split(chr(31))
            participants = [x for x in participants if x]
            text = decode_text(row)
            row.pop("attributedBody")
            row.pop("text")
            row.update(participants=participants,
                       title=row.pop("display_name") or ", ".join(participants) or row["chat_identifier"] or "Conversation",
                       preview=text[:180] or ("Attachment" if row.pop("cache_has_attachments") else "No text messages"),
                       timestamp=unix_date(row.pop("date")), unread=bool(row["latest_id"] and not row["is_from_me"] and not row["is_read"]))
            row.pop("cache_has_attachments", None)
            result.append(row)
        return result

    def chat(self, chat_id):
        return next((x for x in self.chats() if x["id"] == chat_id), None)

    def messages(self, chat_id, before=None, after=None, limit=60):
        where, args = "cm.chat_id=?", [chat_id]
        if before is not None:
            where += " AND m.ROWID<?"
            args.append(before)
        if after is not None:
            where += " AND m.ROWID>?"
            args.append(after)
        args.append(limit+1)
        order = "ASC" if after is not None else "DESC"
        rows = self.source.query(f"""
            SELECT m.ROWID AS id,m.guid,m.text,m.attributedBody,m.is_from_me,m.date,
                m.is_read,m.is_delivered,m.is_sent,m.associated_message_type,m.item_type,
                m.associated_message_guid,m.subject,h.id AS sender
            FROM chat_message_join cm JOIN message m ON m.ROWID=cm.message_id
            LEFT JOIN handle h ON h.ROWID=m.handle_id
            WHERE {where} ORDER BY m.ROWID {order} LIMIT ?
        """,args)
        has_more = len(rows)>limit
        rows = rows[:limit]
        result = []
        for row in rows:
            text = decode_text(row)
            row.pop("text")
            row.pop("attributedBody")
            attachments = self.source.query("""SELECT a.ROWID AS id,a.transfer_name AS name,a.mime_type,a.total_bytes AS size
                FROM message_attachment_join j JOIN attachment a ON a.ROWID=j.attachment_id WHERE j.message_id=?""",(row["id"],))
            row.update(text=text,timestamp=unix_date(row.pop("date")),attachments=attachments)
            result.append(row)
        if after is None:
            result.reverse()
        return dict(messages=result,has_more=has_more)

    def attachment(self, attachment_id):
        rows = self.source.query("SELECT filename,transfer_name,mime_type FROM attachment WHERE ROWID=?",(attachment_id,))
        if not rows or not rows[0]["filename"]:
            raise FileNotFoundError
        row = rows[0]
        path = Path(row["filename"]).expanduser().resolve()
        # A database entry cannot turn this API into an arbitrary file server.
        root = (Path.home()/"Library/Messages/Attachments").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise FileNotFoundError
        mime = row["mime_type"] or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        # Never render active content from a conversation in the app's origin.
        inline = mime in ("image/jpeg","image/png","image/gif","image/webp","video/mp4","audio/mpeg","audio/mp4","audio/x-caf")
        return path, mime, inline
