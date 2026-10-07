"""Locate the same contract assets in a checkout and an installed wheel."""

from pathlib import Path


def asset_directory(kind: str) -> Path:
    if kind not in {"schemas", "migrations"}:
        raise ValueError(f"Unknown asset kind: {kind}")
    package = Path(__file__).resolve().parent
    bundled = package / f"_{kind}"
    if bundled.is_dir():
        return bundled
    root = package.parents[1]
    source = root / ("schemas" if kind == "schemas" else "db/migrations")
    if not source.is_dir():
        raise FileNotFoundError(f"Missing StreamScale {kind} assets")
    return source
