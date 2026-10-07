from unittest.mock import Mock

import psycopg

from streamscale import cli


def test_missing_environment_is_actionable(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr("sys.argv", ["streamscale", "migrate"])
    assert cli.main() == 1
    assert ".env.example" in capsys.readouterr().err


def test_driver_errors_do_not_leak_credentials(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost/db")
    monkeypatch.setattr("sys.argv", ["streamscale", "migrate"])
    monkeypatch.setattr(cli, "migrate", Mock(side_effect=psycopg.OperationalError("secret")))
    assert cli.main() == 1
    output = capsys.readouterr().err
    assert "OperationalError" in output
    assert "secret" not in output
