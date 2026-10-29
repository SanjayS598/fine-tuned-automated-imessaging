"""Loopback-only UI with an authenticated session and same-origin mutations."""
import fcntl
import json
import logging
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
import re
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, jobs, contacts
from .inbox import Inbox, supports_replies
from .policy import owner_adapter
from .transport import SendError

STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, port):
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.worker = None
        self.training = None
        self.contacts_process = None
        self.process_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.cached_chats, self.cached_at = [], 0
        self.preview_lock=threading.Lock()
        self.preview_job=dict(stage='idle')
        self.preview_model=None
        self.preview_model_key=None

    @property
    def origin(self):
        return f"http://127.0.0.1:{self.port}"

    def inbox(self):
        return Inbox(config.load()["db_path"])

    def chats(self):
        with self.cache_lock:
            if time.monotonic()-self.cached_at > 4:
                self.cached_chats = self.inbox().chats()
                self.cached_at = time.monotonic()
            cfg = config.load()
            names = contacts.snapshot().get('index',{})
            def named(row):
                participants=[]
                for address in row['participants']:
                    try:
                        participants.append(names.get(config.normalize(address),address))
                    except ValueError:
                        participants.append(address)
                title = row['title']
                if row['style']==45 and len(participants)==1:
                    title=participants[0]
                elif title == ', '.join(row['participants']):
                    title=', '.join(participants)
                return dict(row,title=title,participant_names=participants)
            return [dict(named(row), reply_supported=supports_replies(row,cfg),
                         auto_reply=self.chat_settings(row,cfg)["enabled"],
                         reply_mode=self.chat_settings(row,cfg).get('reply_mode','manual'),
                         instructions=self.chat_settings(row,cfg)["instructions"]) for row in self.cached_chats]

    @staticmethod
    def chat_settings(chat,cfg):
        fallback = any(config.normalize(x) in cfg["allowed_contacts"] for x in chat["participants"] if "@" in x or any(c.isdigit() for c in x)) if chat['style']==45 and supports_replies(chat,cfg) else False
        return cfg.get("chat_settings",{}).get(str(chat["id"]),dict(enabled=fallback,instructions="",reply_mode='manual'))

    def chat(self,chat_id):
        return next((c for c in self.chats() if c["id"] == chat_id),None)

    def mutate(self,edit):
        def revisioned(cfg):
            edit(cfg)
            cfg["revision"] = cfg.get("revision",0)+1
            if cfg['enabled']:
                from .messages import Messages
                cfg.update(watch_after_rowid=Messages(cfg['db_path']).max_id(),watch_after_at=time.time())
        return config.update(revisioned)

    def worker_running(self):
        with (config.HOME/"worker.lock").open("a+") as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                return False
            except BlockingIOError:
                return True

    def spawn(self,module,*args,log):
        env = dict(os.environ, IMESSAGE_BOT_HOME=str(config.HOME))
        # Also works when Finder has marked editable-install .pth files hidden.
        env["PYTHONPATH"] = str(config.ROOT/"src") + os.pathsep + env.get("PYTHONPATH", "")
        with (config.HOME/log).open("a") as stream:
            return subprocess.Popen([sys.executable,"-m",module,*args],stdout=stream,stderr=stream,env=env)

    def ensure_worker(self):
        with self.process_lock:
            if not self.worker_running() and (not self.worker or self.worker.poll() is not None):
                self.worker = self.spawn("imessage_bot.cli","run",log="worker.log")

    def status(self):
        cfg = config.load()
        try:
            from .messages import Messages
            Messages(cfg["db_path"]).max_id()
            connected,error = True,None
        except sqlite3.Error:
            connected,error = False,"Give this app Full Disk Access in macOS Settings, then reopen it."
            if cfg.get('packaged_data'):
                error='Enable Inbox in System Settings → Privacy & Security → Full Disk Access. Add Inbox from your Applications folder, then quit and reopen Inbox.'
        trained = owner_adapter(cfg)
        automatic=sum(bool(value['enabled'] and value.get('reply_mode')=='auto') for value in cfg['chat_settings'].values())
        manual=sum(bool(value['enabled'] and value.get('reply_mode','manual')=='manual') for value in cfg['chat_settings'].values())
        return dict(enabled=cfg["enabled"],mode="preview" if cfg["dry_run"] else "live",
                    number=cfg["own_identities"][0],sender_verified=cfg["sender_verified"],
                    connected=connected,error=error,trained=trained,worker_running=self.worker_running(),
                    automatic_chats=automatic,manual_chats=manual,
                    training=jobs.status(),model=cfg["model"],
                    contacts={key:value for key,value in contacts.snapshot().items() if key!='index'})

    def control(self,payload):
        allowed = {"enabled","mode","verified_number"}
        if not payload or set(payload)-allowed:
            raise ValueError("Unknown control.")
        if payload.get('enabled') and not self.status()['connected']:
            raise ValueError('Messages access is required before enabling replies. Open Full Disk Access settings in Setup.')
        def edit(cfg):
            if "mode" in payload:
                if payload["mode"] not in ("preview","live"):
                    raise ValueError("Choose preview or live.")
                cfg.update(dry_run=payload["mode"] == "preview",enabled=False)
            if "verified_number" in payload:
                if config.normalize(payload["verified_number"]) != cfg["own_identities"][0]:
                    raise ValueError("The number must match your configured sending number.")
                cfg["sender_verified"] = True
            if "enabled" in payload:
                if type(payload["enabled"]) is not bool:
                    raise ValueError("Enabled must be true or false.")
                if payload["enabled"]:
                    if jobs.status().get("stage") in jobs.BUSY:
                        raise ValueError("Replies stay paused while your model is training.")
                    if not owner_adapter(cfg):
                        raise ValueError("Train your model before starting replies.")
                    if (not cfg["dry_run"] or any(value['enabled'] and value.get('reply_mode')=='auto' for value in cfg['chat_settings'].values())) and not cfg["sender_verified"]:
                        raise ValueError("Confirm your sending number in Setup first.")
                    if not cfg["allowed_contacts"] and not any(x["enabled"] for x in cfg["chat_settings"].values()):
                        raise ValueError("Enable auto replies for at least one conversation first.")
                cfg["enabled"] = payload["enabled"]
        self.mutate(edit)
        if payload.get("enabled"):
            self.ensure_worker()
        return self.status()

    def update_chat(self,chat_id,payload):
        chat = self.chat(chat_id)
        if not chat:
            raise ValueError("Conversation not found.")
        if not payload or set(payload)-{"enabled","instructions","reply_mode"}:
            raise ValueError("Unknown conversation setting.")
        if "enabled" in payload and type(payload["enabled"]) is not bool:
            raise ValueError("Enabled must be true or false.")
        if payload.get("enabled") and not chat["reply_supported"]:
            raise ValueError("This conversation has no supported Messages recipients.")
        if "instructions" in payload and (not isinstance(payload["instructions"],str) or len(payload["instructions"])>4000):
            raise ValueError("Instructions must be text, up to 4000 characters.")
        if 'reply_mode' in payload and payload['reply_mode'] not in ('manual','auto'):
            raise ValueError('Choose Manual or Auto.')
        activate='reply_mode' in payload and payload.get('enabled',True)
        if activate:
            if not self.status()['connected']:
                raise ValueError('Messages access is required. Open Full Disk Access settings in Setup.')
            if not owner_adapter(config.load()):
                raise ValueError('Train your owner-only model before enabling replies.')
            if jobs.status().get('stage') in jobs.BUSY:
                raise ValueError('Replies stay paused during training.')
            if payload['reply_mode']=='auto' and not config.load()['sender_verified']:
                raise ValueError('Confirm the sending number in Setup first.')
        def edit(cfg):
            current = dict(self.chat_settings(chat,cfg))
            current.update(payload)
            if activate:
                current['enabled']=True
                cfg['enabled']=True
            cfg["chat_settings"][str(chat_id)] = current
            if not any(value['enabled'] for value in cfg['chat_settings'].values()) and not cfg['allowed_contacts']:
                cfg['enabled']=False
        self.mutate(edit)
        if activate:self.ensure_worker()
        return self.chat(chat_id)

    def manual_send(self,chat_id,payload):
        from .transport import send
        from .state import State
        if not {'text','request_id'}<=set(payload) or set(payload)-{'text','request_id','preview_guid'} or not isinstance(payload['text'],str) or not payload['text'].strip() or len(payload['text'])>4000:
            raise ValueError('Write a message up to 4000 characters.')
        if not isinstance(payload['request_id'],str) or not re.fullmatch(r'[A-Za-z0-9-]{16,80}',payload['request_id']):
            raise ValueError('A unique send request is required.')
        if payload.get('preview_guid') is not None and (not isinstance(payload['preview_guid'],str) or len(payload['preview_guid'])>256):
            raise ValueError('Invalid draft identifier.')
        chat=self.chat(chat_id)
        if not chat or not chat['reply_supported']:
            raise ValueError('Choose a supported Messages conversation.')
        cfg=config.load()
        if not cfg['sender_verified']:
            raise ValueError('Confirm your sending number in Setup first.')
        if not self.status()['connected']:
            raise ValueError('Messages access is required before sending.')
        state=State()
        try:
            preview_guid=payload.get('preview_guid')
            guid,reserved=state.reserve_manual_send(chat_id,payload['text'].strip(),payload['request_id'],preview_guid)
            if not reserved:
                previous=state.attempt(guid)
                if previous['chat_id']!=chat_id or previous['reply']!=payload['text'].strip():
                    raise ValueError('This send request was already used for another message.')
                return dict(attempt=previous,duplicate=True)
            # Cancels a pending automatic reply for this chat before an explicit
            # manual response is handed to Messages. No global mode is changed.
            self.mutate(lambda c:None)
            try:
                send(chat['guid'],payload['text'].strip())
            except Exception as exc:
                state.finish(guid,'failed',str(exc) if isinstance(exc,SendError) else type(exc).__name__)
                if preview_guid:state.finish(preview_guid,'manual_preview' if isinstance(exc,SendError) and not exc.uncertain else 'approval_failed')
                raise
            state.finish(guid,'sent')
            if preview_guid:state.finish(preview_guid,'approved_sent')
            return dict(attempt=state.attempt(guid),duplicate=False)
        finally:state.close()

    def preview_status(self,chat_id):
        from .state import State
        state=State()
        try:
            rows=state.previews(chat_id)
            preview=dict(rows[0]) if rows and rows[0]['status'] in ('preview','manual_preview') else None
        finally:state.close()
        job=dict(self.preview_job) if self.preview_job.get('chat_id')==chat_id else dict(stage='idle')
        return dict(preview=preview,job=job)

    def start_preview(self,chat_id):
        cfg=config.load()
        chat=self.chat(chat_id)
        if not chat or not chat['reply_supported']:
            raise ValueError('Choose a supported Messages conversation.')
        if jobs.status().get('stage') in jobs.BUSY:
            raise ValueError('Wait for training to finish before generating a preview.')
        if not owner_adapter(cfg):
            raise ValueError('An owner-only trained model is required.')
        if not self.preview_lock.acquire(blocking=False):
            raise ValueError('A preview is already generating. Please wait.')
        self.preview_job=dict(stage='queued',chat_id=chat_id)
        threading.Thread(target=self.generate_preview,args=(chat_id,cfg),daemon=True).start()
        return dict(started=True,chat_id=chat_id)

    def generate_preview(self,chat_id,cfg):
        from .messages import Messages,eligible,conversation
        from .model import Model,reply_config
        from .state import State
        state=None
        try:
            self.preview_job=dict(stage='generating',chat_id=chat_id)
            source=Messages(cfg['db_path'])
            row=source.latest_incoming(chat_id)
            if not row:
                raise ValueError('No received text is available to preview in this conversation.')
            approved=dict(cfg,chat_settings=dict(cfg.get('chat_settings',{})))
            approved['chat_settings'][str(chat_id)]=dict(enabled=True,instructions='')
            if not eligible(row,approved):
                raise ValueError('The latest received message cannot be previewed.')
            turns=conversation(source.history_through(chat_id,row['rowid']),label_senders=row['style']==43)
            key=(cfg['model'],cfg['adapter_path'])
            if self.preview_model is None or self.preview_model_key!=key:
                self.preview_model=Model(cfg)
                self.preview_model_key=key
            reply=self.preview_model.reply(turns,reply_config(cfg,chat_id,group=row['style']==43))
            if not reply:
                raise ValueError('The model returned no usable reply. Try generating again.')
            state=State()
            guid=state.save_manual_preview(row,reply)
            self.preview_job=dict(stage='ready',chat_id=chat_id,guid=guid)
        except Exception as exc:
            error=str(exc) if isinstance(exc,ValueError) else f'Preview failed ({type(exc).__name__}). Check app.log.'
            self.preview_job=dict(stage='failed',chat_id=chat_id,error=error)
            logging.error('Manual preview failed (%s)',type(exc).__name__)
        finally:
            if state:state.close()
            self.preview_lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = "LocalInbox/1"

    @property
    def app(self):
        return self.server.app

    def log_message(self,*args):
        pass  # URLs and message content never go into access logs.

    def respond(self,status,body,mime="application/json",headers=None):
        if mime == "application/json":
            body = json.dumps(body).encode()
        elif isinstance(body,str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Length",str(len(body)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Referrer-Policy","no-referrer")
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for key,value in (headers or {}).items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie",""))
            value = cookie.get("inbox_session")
            return bool(value and secrets.compare_digest(value.value,self.app.token))
        except Exception:
            return False

    def gate(self,mutation=False):
        if self.headers.get("Host") != f"127.0.0.1:{self.app.port}":
            self.respond(403,{"error":"Invalid host."})
            return False
        if mutation and (self.headers.get("Origin") != self.app.origin or self.headers.get("Content-Type") != "application/json"):
            self.respond(403,{"error":"Same-origin JSON requests required."})
            return False
        return True

    def do_GET(self):
        if not self.gate():
            return
        url = urlparse(self.path)
        params = parse_qs(url.query)
        if url.path == "/" and params.get("token"):
            if secrets.compare_digest(params["token"][0],self.app.token):
                self.respond(303,b"","text/plain",{"Location":"/","Set-Cookie":f"inbox_session={self.app.token}; HttpOnly; SameSite=Strict; Path=/"})
            else:
                self.respond(403,{"error":"Session link expired. Reopen the app using start.command."})
            return
        if not self.authorized():
            self.respond(401,"Open this inbox with start.command in your project folder.","text/plain")
            return
        try:
            if url.path in ("/","/app.js","/style.css"):
                name,mime = {"/":("index.html","text/html; charset=utf-8"),"/app.js":("app.js","text/javascript"),"/style.css":("style.css","text/css")}[url.path]
                self.respond(200,(STATIC/name).read_bytes(),mime)
            elif url.path == "/api/status":
                self.respond(200,self.app.status())
            elif url.path == "/api/chats":
                self.respond(200,{"chats":self.app.chats()})
            elif url.path.startswith("/api/chats/") and url.path.endswith("/messages"):
                chat_id = int(url.path.split("/")[3])
                def cursor(key):
                    return int(params[key][0]) if key in params else None
                self.respond(200,self.app.inbox().messages(chat_id,before=cursor("before"),after=cursor("after")))
            elif url.path.startswith('/api/chats/') and url.path.endswith('/preview'):
                self.respond(200,self.app.preview_status(int(url.path.split('/')[3])))
            elif url.path == "/api/activity":
                from .state import State
                state = State()
                try:
                    self.respond(200,{"activity":[dict(x) for x in state.previews()]})
                finally:
                    state.close()
            elif url.path.startswith("/api/attachments/"):
                path,mime,inline = self.app.inbox().attachment(int(url.path.rsplit("/",1)[1]))
                if path.stat().st_size > 40*1024*1024:
                    self.respond(413,{"error":"Attachment exceeds the 40 MB preview limit. Open it in Messages."})
                    return
                disposition = "inline" if inline else "attachment"
                self.respond(200,path.read_bytes(),mime,{"Content-Disposition":f'{disposition}; filename="attachment"'})
            else:
                self.respond(404,{"error":"Not found."})
        except (ValueError,FileNotFoundError):
            self.respond(404,{"error":"Item not found or no longer available on this Mac."})
        except sqlite3.Error:
            self.respond(503,{"error":"Messages unavailable. Check Full Disk Access in Setup."})
        except Exception:
            logging.exception("Inbox request failed")
            self.respond(500,{"error":"Local request failed. Check the app log."})

    def do_POST(self):
        if not self.gate(mutation=True):
            return
        if not self.authorized():
            self.respond(401,{"error":"Session expired. Reopen the app."})
            return
        try:
            length = int(self.headers.get("Content-Length","0"))
            if not 0 < length <= 20000:
                raise ValueError("Invalid request size.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):
                raise ValueError("Expected an object.")
            if self.path == "/api/control":
                self.respond(200,self.app.control(payload))
            elif self.path.startswith('/api/chats/') and self.path.endswith('/preview'):
                self.respond(202,self.app.start_preview(int(self.path.split('/')[3])))
            elif self.path.startswith('/api/chats/') and self.path.endswith('/send'):
                self.respond(200,self.app.manual_send(int(self.path.split('/')[3]),payload))
            elif self.path.startswith("/api/chats/"):
                self.respond(200,self.app.update_chat(int(self.path.rsplit("/",1)[1]),payload))
            elif self.path == "/api/train":
                with self.app.process_lock:
                    if jobs.status().get("stage") in jobs.BUSY or (self.app.training and self.app.training.poll() is None):
                        raise ValueError("Training is already running.")
                    self.app.training = self.app.spawn("imessage_bot.jobs",log="training.log")
                self.respond(202,{"started":True})
            elif self.path == "/api/contacts/sync":
                with self.app.process_lock:
                    if self.app.contacts_process and self.app.contacts_process.poll() is None:
                        raise ValueError('Contacts sync is already running.')
                    self.app.contacts_process=self.app.spawn('imessage_bot.contacts','--request',log='contacts.log')
                self.respond(202,{'started':True})
            elif self.path == '/api/permissions/messages':
                subprocess.Popen(['/usr/bin/open','x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles'])
                self.respond(200,{'opened':True})
            else:
                self.respond(404,{"error":"Not found."})
        except SendError as exc:
            self.respond(502,{'error':str(exc),'retry_safe':not exc.uncertain})
        except (ValueError,TypeError,KeyError) as exc:
            self.respond(400,{"error":str(exc)})
        except Exception:
            self.respond(500,{"error":"Could not save. Check the app log."})


def serve(port=8765,open_browser=True):
    config.private_dir()
    config.load()
    app = App(port)
    server = ThreadingHTTPServer(("127.0.0.1",port),Handler)
    app.port = server.server_port
    server.app = app
    url = app.origin+"/?token="+app.token
    session = config.HOME/"ui-url"
    session.write_text(url)
    session.chmod(0o600)
    print(f"Local inbox: {app.origin} — use start.command to open your private session.",flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.mutate(lambda cfg: cfg.update(enabled=False))
        if app.worker and app.worker.poll() is None:
            app.worker.terminate()
            app.worker.wait(timeout=10)
