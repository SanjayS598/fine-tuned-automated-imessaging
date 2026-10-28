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
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import config, jobs
from .inbox import Inbox, supports_replies

STATIC = Path(__file__).parent / "static"


class App:
    def __init__(self, port):
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self.worker = None
        self.training = None
        self.process_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.cached_chats, self.cached_at = [], 0

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
            return [dict(row, reply_supported=supports_replies(row,cfg),
                         auto_reply=self.chat_settings(row,cfg)["enabled"],
                         instructions=self.chat_settings(row,cfg)["instructions"]) for row in self.cached_chats]

    @staticmethod
    def chat_settings(chat,cfg):
        fallback = any(config.normalize(x) in cfg["allowed_contacts"] for x in chat["participants"] if "@" in x or any(c.isdigit() for c in x)) if supports_replies(chat,cfg) else False
        return cfg.get("chat_settings",{}).get(str(chat["id"]),dict(enabled=fallback,instructions=""))

    def chat(self,chat_id):
        return next((c for c in self.chats() if c["id"] == chat_id),None)

    def mutate(self,edit):
        def revisioned(cfg):
            edit(cfg)
            cfg["revision"] = cfg.get("revision",0)+1
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
        trained = (Path(cfg["adapter_path"])/"personal-model.json").is_file()
        return dict(enabled=cfg["enabled"],mode="preview" if cfg["dry_run"] else "live",
                    number=cfg["own_identities"][0],sender_verified=cfg["sender_verified"],
                    connected=connected,error=error,trained=trained,worker_running=self.worker_running(),
                    training=jobs.status(),model=cfg["model"])

    def control(self,payload):
        allowed = {"enabled","mode","verified_number"}
        if not payload or set(payload)-allowed:
            raise ValueError("Unknown control.")
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
                    if not (Path(cfg["adapter_path"])/"personal-model.json").is_file():
                        raise ValueError("Train your model before starting replies.")
                    if not cfg["dry_run"] and not cfg["sender_verified"]:
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
        if not payload or set(payload)-{"enabled","instructions"}:
            raise ValueError("Unknown conversation setting.")
        if "enabled" in payload and type(payload["enabled"]) is not bool:
            raise ValueError("Enabled must be true or false.")
        if payload.get("enabled") and not chat["reply_supported"]:
            raise ValueError("Auto replies support direct iMessage and SMS conversations.")
        if "instructions" in payload and (not isinstance(payload["instructions"],str) or len(payload["instructions"])>4000):
            raise ValueError("Instructions must be text, up to 4000 characters.")
        def edit(cfg):
            current = dict(self.chat_settings(chat,cfg))
            current.update(payload)
            cfg["chat_settings"][str(chat_id)] = current
        self.mutate(edit)
        return self.chat(chat_id)


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
            elif self.path.startswith("/api/chats/"):
                self.respond(200,self.app.update_chat(int(self.path.rsplit("/",1)[1]),payload))
            elif self.path == "/api/train":
                with self.app.process_lock:
                    if jobs.status().get("stage") in ("exporting","training") or (self.app.training and self.app.training.poll() is None):
                        raise ValueError("Training is already running.")
                    if self.app.status()["trained"]:
                        raise ValueError("A trained model already exists. Choose a new adapter path to train a replacement.")
                    self.app.training = self.app.spawn("imessage_bot.jobs",log="training.log")
                self.respond(202,{"started":True})
            else:
                self.respond(404,{"error":"Not found."})
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
