import json
import sqlite3
from pathlib import Path

from .schemas import Selection


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS results (
                cache_key TEXT PRIMARY KEY,
                selection TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS attempts (
                run_id TEXT NOT NULL,
                product_key TEXT NOT NULL,
                details TEXT NOT NULL
            );
            """
        )

    def get(self, cache_key: str) -> Selection | None:
        row = self.connection.execute(
            "SELECT selection FROM results WHERE cache_key = ?", (cache_key,)
        ).fetchone()
        return Selection.model_validate_json(row[0]) if row else None

    def save(self, cache_key: str, selection: Selection) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT OR REPLACE INTO results VALUES (?, ?)",
                (cache_key, selection.model_dump_json()),
            )

    def save_attempt(self, run_id: str, product_key: str, details: dict) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO attempts VALUES (?, ?, ?)",
                (run_id, product_key, json.dumps(details)),
            )

    def close(self) -> None:
        self.connection.close()
