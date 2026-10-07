"""Host-side development connections. Never print database connection secrets."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    kafka_bootstrap_servers: str
    database_url: str

    @classmethod
    def from_env(cls) -> "Settings":
        kafka = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:19092").strip()
        database = os.environ.get("DATABASE_URL", "").strip()
        if not kafka or not database:
            raise ValueError("Set KAFKA_BOOTSTRAP_SERVERS and DATABASE_URL; see .env.example")
        return cls(kafka, database)
