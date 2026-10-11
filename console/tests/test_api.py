"""Loopback API security, operation serialization and duplicate-click protection."""

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient
from streamscale_console.app import COOKIE, Console, create_app, register_core_operations
from streamscale_console.operations import Coordinator, Journal, Rejected

TOKEN = "t" * 43
PORT = 18555
ORIGIN = f"http://127.0.0.1:{PORT}"


class FakeForward:
    restarts = 0

    def stop(self):
        pass

    def restart(self):
        self.restarts += 1


class FakeHub:
    def __init__(self):
        self.listeners = []
        self.sample_listeners = []
        self.forward = FakeForward()
        self.status = {"agent": "connected"}
        self.points = []
        self.latest = None
        self.agent = None

    def public_status(self):
        return {
            "agent": "connected",
            "since": 0,
            "error": None,
            "last_ok": 0,
            "forward_restarts": 0,
        }


@pytest.fixture
def client(tmp_path):
    console = Console(TOKEN, PORT, hub=FakeHub(), journal=Journal(tmp_path))
    register_core_operations(console)
    app = create_app(console, background=False)
    with TestClient(app, base_url=f"http://127.0.0.1:{PORT}") as test_client:
        test_client.console = console
        yield test_client


def login(client):
    response = client.get(f"/?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "samesite=strict" in response.headers["set-cookie"].lower()
    client.cookies.set(COOKIE, TOKEN)


def test_api_requires_the_session_cookie(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/?token=wrong").status_code == 401
    login(client)
    assert client.get("/api/state").status_code == 200


def test_foreign_host_headers_are_refused(client):
    login(client)
    response = client.get("/api/state", headers={"Host": "attacker.example"})
    assert response.status_code == 421


@pytest.mark.parametrize("origin", [None, "http://attacker.example", "http://127.0.0.1:1"])
def test_state_changes_require_a_loopback_origin(client, origin):
    login(client)
    headers = {} if origin is None else {"Origin": origin}
    response = client.post(
        "/api/operations",
        json={"kind": "timeline.note", "params": {"text": "x"}, "key": "key-00000001"},
        headers=headers,
    )
    assert response.status_code == 403


def test_repeated_idempotency_key_returns_the_same_operation(client):
    login(client)
    body = {"kind": "timeline.note", "params": {"text": "hello"}, "key": "key-00000002"}
    first = client.post("/api/operations", json=body, headers={"Origin": ORIGIN}).json()
    second = client.post("/api/operations", json=body, headers={"Origin": ORIGIN}).json()
    assert first["created"] is True and second["created"] is False
    assert first["operation"]["id"] == second["operation"]["id"]
    assert client.console.coordinator.queue.qsize() == 1


def test_unknown_operations_are_rejected(client):
    login(client)
    response = client.post(
        "/api/operations",
        json={"kind": "shell.exec", "params": {"command": "rm -rf /"}, "key": "key-00000003"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 409
    assert "Unknown operation" in response.json()["error"]


def test_websocket_requires_cookie_and_origin(client):
    from starlette.websockets import WebSocketDisconnect

    # The test client always sends Host: testserver for WebSockets; set the real one.
    host = {"Host": f"127.0.0.1:{PORT}"}

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/stream", headers=host | {"Origin": ORIGIN}) as socket:
            socket.receive_json()
    login(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/stream", headers=host | {"Origin": "http://attacker.example"}
        ) as socket:
            socket.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/stream", headers={"Origin": ORIGIN}) as socket:
            socket.receive_json()
    with client.websocket_connect("/api/stream", headers=host | {"Origin": ORIGIN}) as socket:
        hello = socket.receive_json()
    assert hello["type"] == "hello"
    assert hello["state"]["status"]["agent"] == "connected"


def test_root_files_cannot_escape_the_build_directory(client):
    login(client)
    assert client.get("/..%2Fpyproject.toml").status_code == 404
    assert client.get("/index.html").status_code == 404


def run(coroutine):
    return asyncio.run(coroutine)


def test_operations_run_one_at_a_time_in_order(tmp_path):
    async def scenario():
        coordinator = Coordinator(Journal(tmp_path))
        coordinator.queue = asyncio.Queue()
        events = []

        async def slow(operation):
            events.append(("start", operation.params["n"]))
            await asyncio.sleep(0.01)
            events.append(("end", operation.params["n"]))
            return {"n": operation.params["n"]}

        coordinator.register("work", slow, "Work")
        worker = asyncio.create_task(coordinator.run())
        await asyncio.sleep(0)
        for index in range(3):
            coordinator.submit("work", {"n": index}, f"key-work-{index:04d}")
        while any(op.state != "succeeded" for op in coordinator.operations.values()):
            await asyncio.sleep(0.01)
        worker.cancel()
        return events

    events = run(scenario())
    assert events == [("start", 0), ("end", 0), ("start", 1), ("end", 1), ("start", 2), ("end", 2)]


def test_identical_in_flight_requests_are_not_repeated(tmp_path):
    coordinator = Coordinator(Journal(tmp_path))
    coordinator.queue = asyncio.Queue()

    async def handler(operation):
        return {}

    coordinator.register("sale.pause", handler, "Pause")
    first, created = coordinator.submit("sale.pause", {}, "key-click-0001")
    second, again = coordinator.submit("sale.pause", {}, "key-click-0002")
    assert created and not again and first.id == second.id


def test_checks_reject_before_queueing_and_are_journaled(tmp_path):
    coordinator = Coordinator(Journal(tmp_path))
    coordinator.queue = asyncio.Queue()

    def refuse(params):
        raise Rejected("A sale is already running")

    async def handler(operation):
        return {}

    coordinator.register("sale.start", handler, "Start sale", check=refuse)
    with pytest.raises(Rejected):
        coordinator.submit("sale.start", {}, "key-start-0001")
    assert coordinator.queue.qsize() == 0


def test_failures_are_recorded_with_their_message(tmp_path):
    async def scenario():
        journal = Journal(tmp_path)
        coordinator = Coordinator(journal)
        coordinator.queue = asyncio.Queue()

        async def broken(operation):
            raise RuntimeError("Agent refused the command")

        coordinator.register("broken", broken, "Broken")
        worker = asyncio.create_task(coordinator.run())
        await asyncio.sleep(0)
        operation, _ = coordinator.submit("broken", {}, "key-broken-01")
        deadline = time.time() + 2
        while operation.state in {"queued", "running"} and time.time() < deadline:
            await asyncio.sleep(0.01)
        worker.cancel()
        return operation

    operation = run(scenario())
    assert operation.state == "failed"
    assert operation.error == "Agent refused the command"
    lines = (tmp_path / "journal.jsonl").read_text().splitlines()
    assert json.loads(lines[-1])["state"] == "failed"
