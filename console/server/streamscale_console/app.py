"""Loopback-only console API: static UI, authenticated operations and a live stream.

Security model: the server binds to 127.0.0.1 and accepts only loopback Host
headers (DNS-rebinding defence). The printed login link carries a random session
token that becomes an HttpOnly, SameSite=Strict cookie. Every API call needs the
cookie; every state-changing request and WebSocket also needs a loopback Origin.
Cluster credentials and the agent token never reach the browser.
"""

import asyncio
import hmac
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from streamscale_console.cluster import ARTIFACTS, ROOT
from streamscale_console.operations import Coordinator, Journal, Rejected
from streamscale_console.telemetry import TelemetryHub
from streamscale_console.workflow import WorkflowLock, holder

COOKIE = "streamscale_console_session"
WEB = ROOT / "console/web/dist"
LOGIN_HINT = """<!doctype html><meta charset="utf-8"><title>StreamScale console</title>
<style>body{font:16px system-ui;margin:4rem auto;max-width:36rem;padding:0 1rem;
color:#1d2330;background:#f7f7f4}@media(prefers-color-scheme:dark){body{color:#e8e8e3;
background:#16181d}}</style><h1>StreamScale console</h1><p>This local console needs the
login link printed by <code>./streamscale.sh console</code>. The link is also saved in
<code>artifacts/console/server.json</code>.</p>"""


class Console:
    """Process-wide state shared by routes and background tasks."""

    def __init__(self, token, port, hub=None, journal=None, extras=None):
        self.token = token
        self.port = port
        self.started_at = time.time()
        self.hub = hub or TelemetryHub()
        self.journal = journal or Journal()
        self.lock = WorkflowLock()
        self.session = None
        self.coordinator = Coordinator(self.journal, lambda: self.session)
        self.clients: set[asyncio.Queue] = set()
        self.loop = None
        self.extras = extras or {}
        self.dashboards = {}
        self.components = []
        for source in (self.hub, self.coordinator, self.journal):
            source.listeners.append(self.publish)

    @property
    def origins(self):
        return {f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"}

    @property
    def hosts(self):
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def authorized(self, cookie):
        return bool(cookie) and hmac.compare_digest(cookie.encode(), self.token.encode())

    def publish(self, message):
        if self.loop is None:
            return
        for queue in list(self.clients):
            self.loop.call_soon_threadsafe(_offer, queue, message)

    def server_info(self):
        deployment = ARTIFACTS / "deployment.json"
        return {
            "started_at": self.started_at,
            "port": self.port,
            "agent": self.hub.agent,
            "deployment": json.loads(deployment.read_text()) if deployment.exists() else None,
            "dashboards": self.dashboards,
        }

    def workflow(self):
        other = holder()
        return {
            "held_by_console": self.lock.held,
            "console_purpose": self.lock.purpose,
            "holder": None if other is None or other["self"] else other,
        }

    def snapshot(self):
        state = {
            "server": self.server_info(),
            "status": self.hub.public_status(),
            "points": list(self.hub.points)[-900:],
            "sample": None,
            "journal": self.journal.tail[-200:],
            "operations": self.coordinator.recent(),
            "workflow": self.workflow(),
            "now": time.time(),
        }
        if self.hub.latest is not None:
            from streamscale_console.telemetry import compact

            state["sample"] = compact(self.hub.latest)
        for component in self.components:
            state.update(component.snapshot())
        return state


def _offer(queue, message):
    try:
        queue.put_nowait(message)
    except asyncio.QueueFull:
        # A browser that cannot keep up discards its backlog and reloads current state.
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait({"type": "resync"})


def create_app(console: Console, background=True):
    @asynccontextmanager
    async def lifespan(_app):
        console.loop = asyncio.get_running_loop()
        tasks = []
        if background:
            tasks.append(asyncio.create_task(console.coordinator.run()))
            tasks.append(asyncio.create_task(console.hub.run()))
            tasks.append(asyncio.create_task(heartbeat(console)))
            for component in console.components:
                tasks.extend(asyncio.create_task(task) for task in component.tasks())
        else:
            console.coordinator.queue = asyncio.Queue()
        console.journal.load_tail()
        console.journal.write({"type": "system", "text": "Console server started"})
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            # Never drop an accepted request silently: record what did not finish.
            for operation in console.coordinator.operations.values():
                if operation.state in {"queued", "running"}:
                    console.journal.write(
                        {
                            "type": "operation",
                            "kind": operation.kind,
                            "op_id": operation.id,
                            "state": "abandoned",
                            "params": operation.params,
                            "error": "The console server stopped before this finished",
                        },
                        operation.session,
                    )
            console.journal.write({"type": "system", "text": "Console server stopped"})
            console.hub.forward.stop()
            console.lock.release()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.headers.get("host") not in console.hosts:
            return JSONResponse({"error": "Loopback host required"}, status_code=421)
        open_paths = {"/", "/health"}
        if request.url.path not in open_paths and not console.authorized(
            request.cookies.get(COOKIE)
        ):
            return JSONResponse({"error": "Console session required"}, status_code=401)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") not in console.origins:
                return JSONResponse({"error": "Same-origin request required"}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/")
    async def index(request: Request, token: str | None = None):
        if token is not None:
            if not console.authorized(token):
                return HTMLResponse(LOGIN_HINT, status_code=401)
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
            return response
        if not console.authorized(request.cookies.get(COOKIE)):
            return HTMLResponse(LOGIN_HINT, status_code=401)
        page = WEB / "index.html"
        if not page.exists():
            return HTMLResponse(
                "<p>Console web assets are not built. Run <code>make console-web</code>.</p>",
                status_code=503,
            )
        return FileResponse(page)

    if (WEB / "assets").exists():
        app.mount("/assets", StaticFiles(directory=WEB / "assets"), name="assets")

    @app.get("/api/state")
    async def state():
        return console.snapshot()

    @app.post("/api/operations")
    async def submit(request: Request):
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "Request body must be JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Request body must be an object"}, status_code=400)
        try:
            operation, created = console.coordinator.submit(
                body.get("kind"), body.get("params") or {}, body.get("key")
            )
        except Rejected as error:
            return JSONResponse({"error": str(error)}, status_code=409)
        return {"operation": operation.public(), "created": created}

    @app.get("/api/operations/{identifier}")
    async def operation(identifier: str):
        found = console.coordinator.operations.get(identifier)
        if found is None:
            return JSONResponse({"error": "Unknown operation"}, status_code=404)
        return found.public()

    @app.websocket("/api/stream")
    async def stream(socket: WebSocket):
        if (
            socket.headers.get("host") not in console.hosts
            or socket.headers.get("origin") not in console.origins
            or not console.authorized(socket.cookies.get(COOKIE))
        ):
            await socket.close(code=4401)
            return
        await socket.accept()
        queue = asyncio.Queue(maxsize=600)
        console.clients.add(queue)
        try:
            await socket.send_json({"type": "hello", "state": console.snapshot()})
            while True:
                message = await queue.get()
                await socket.send_text(json.dumps(message, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            console.clients.discard(queue)

    for component in console.components:
        component.routes(app)

    @app.get("/{name}")
    async def root_file(name: str):
        # Only files emitted at the build root (icons); never paths outside it.
        path = (WEB / name).resolve()
        if path.parent != WEB.resolve() or not path.is_file() or name == "index.html":
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(path)

    return app


async def heartbeat(console: Console):
    """Server pulse with workflow-lock status; lets browsers detect a silent stream."""
    while True:
        console.publish({"type": "server", "now": time.time(), "workflow": console.workflow()})
        await asyncio.sleep(2)


def register_core_operations(console: Console):
    async def reconnect(operation):
        await asyncio.to_thread(console.hub.forward.restart)
        return {"forward_restarts": console.hub.forward.restarts}

    async def note(operation):
        text = str(operation.params.get("text", "")).strip()
        if not text:
            raise Rejected("A timeline note needs text")
        console.journal.write({"type": "note", "text": text[:500]}, console.session)
        return {"text": text[:500]}

    console.coordinator.register(
        "agent.reconnect", reconnect, "Reconnect to the cluster agent", mutating=False
    )
    console.coordinator.register("timeline.note", note, "Add a timeline note", mutating=False)


def web_root() -> Path:
    return WEB
