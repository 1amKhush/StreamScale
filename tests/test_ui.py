"""Busy dashboard ports must not fail acceptance or interrupt existing listeners."""

from contextlib import contextmanager

import pytest

from deploy import ui


@pytest.fixture
def forwards(monkeypatch):
    calls, closed = [], []
    busy = set()

    @contextmanager
    def forward(role, port=None):
        calls.append((role, port))
        if port in busy:
            raise RuntimeError(f"Loopback port {port} is already in use")
        selected = port or (44000 + len(calls))
        try:
            yield f"http://127.0.0.1:{selected}"
        finally:
            closed.append((role, selected))

    class StopImmediately:
        def wait(self, _):
            return True

    monkeypatch.setattr(ui, "forward", forward)
    monkeypatch.setattr(ui, "Event", StopImmediately)
    monkeypatch.setattr(ui.signal, "signal", lambda *_: None)
    return calls, closed, busy


def test_free_defaults_keep_documented_addresses(forwards, capsys):
    calls, closed, _ = forwards
    ui.main([])
    assert calls == [("grafana", 13000), ("prometheus", 19090)]
    assert closed == [("prometheus", 19090), ("grafana", 13000)]
    assert "Grafana: http://127.0.0.1:13000/d/streamscale" in capsys.readouterr().out


def test_both_busy_defaults_fall_back_and_only_created_forwards_are_closed(forwards, capsys):
    calls, closed, busy = forwards
    busy.update({13000, 19090})
    ui.main([])
    assert calls == [
        ("grafana", 13000),
        ("grafana", None),
        ("prometheus", 19090),
        ("prometheus", None),
    ]
    assert closed == [("prometheus", 44004), ("grafana", 44002)]
    output = capsys.readouterr().out
    assert "Grafana: http://127.0.0.1:44002/d/streamscale" in output
    assert "Prometheus: http://127.0.0.1:44004" in output


@pytest.mark.parametrize("ports", [(13001, 19091), (0, 0)])
def test_explicit_ports_and_automatic_port_zero_are_forwarded(forwards, ports):
    calls, _, _ = forwards
    ui.main(["--grafana-port", str(ports[0]), "--prometheus-port", str(ports[1])])
    assert calls == [("grafana", ports[0]), ("prometheus", ports[1])]


def test_busy_explicit_second_port_has_actionable_error_and_closes_first(forwards, capsys):
    calls, closed, busy = forwards
    busy.add(19091)
    with pytest.raises(SystemExit, match="1"):
        ui.main(["--grafana-port", "13001", "--prometheus-port", "19091"])
    assert calls == [("grafana", 13001), ("prometheus", 19091)]
    assert closed == [("grafana", 13001)]
    error = capsys.readouterr().err
    assert "--prometheus-port 0" in error
    assert "Traceback" not in error


def test_forward_startup_failure_is_reported_without_retrying_another_port(monkeypatch, capsys):
    calls = []

    @contextmanager
    def failure(role, port):
        calls.append((role, port))
        raise RuntimeError("Port forward for grafana failed; see the log")
        yield  # pragma: no cover

    monkeypatch.setattr(ui, "forward", failure)
    monkeypatch.setattr(ui.signal, "signal", lambda *_: None)
    with pytest.raises(SystemExit, match="1"):
        ui.main([])
    assert calls == [("grafana", 13000)]
    assert "see the log" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["-1", "65536", "not-a-port"])
def test_invalid_ports_fail_before_starting_forwards(forwards, value):
    calls, _, _ = forwards
    with pytest.raises(SystemExit, match="2"):
        ui.main(["--grafana-port", value])
    assert calls == []
