"""Versioned, transactional PostgreSQL bootstrap with applied-file checksums."""

import hashlib
from pathlib import Path

import psycopg

from streamscale.assets import asset_directory


def migrate(database_url: str, directory: Path | None = None) -> list[str]:
    files = sorted((directory or asset_directory("migrations")).glob("[0-9]*_*.sql"))
    if not files:
        raise RuntimeError("No SQL migrations found")
    applied_now = []
    with psycopg.connect(database_url, connect_timeout=10) as connection:
        with connection.transaction():
            connection.execute("SELECT pg_advisory_xact_lock(1937006962, 1)")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS _streamscale_migration (
                    name TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
                )
            """)
            applied = dict(
                connection.execute("SELECT name, sha256 FROM _streamscale_migration").fetchall()
            )
            names = {file.name for file in files}
            if applied.keys() - names:
                raise RuntimeError("Database contains migrations absent from this revision")
            for file in files:
                body = file.read_bytes()
                checksum = hashlib.sha256(body).hexdigest()
                if file.name in applied:
                    if applied[file.name] != checksum:
                        raise RuntimeError(f"Applied migration changed: {file.name}")
                    continue
                if applied and file.name < max(applied):
                    raise RuntimeError(
                        f"Migration added behind current database version: {file.name}"
                    )
                connection.execute(body.decode("utf-8"), prepare=False)
                connection.execute(
                    "INSERT INTO _streamscale_migration (name, sha256) VALUES (%s, %s)",
                    (file.name, checksum),
                )
                applied_now.append(file.name)
    return applied_now
