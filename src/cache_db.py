"""SQLite cache module."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class CacheDBError(RuntimeError):
    """Raised when cache DB operation fails."""


class CacheDB:
    """
    SQLite cache for account mapping results.
    """

    def __init__(self, db_path: str = "data/cache.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self._ensure_table()

    def _ensure_table(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_mapping (
                cache_key TEXT PRIMARY KEY,
                corp_code TEXT,
                note_type TEXT,
                mapping_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_account_mapping_meta ON account_mapping(corp_code, note_type)"
        )
        self.conn.commit()

    def get(self, cache_key: str) -> dict[str, str] | None:
        row = self.conn.execute(
            "SELECT mapping_json FROM account_mapping WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None
        raw = row[0]
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CacheDBError(f"캐시 JSON 파싱 실패: key={cache_key}") from exc
        if not isinstance(payload, dict):
            return None
        return {str(k): str(v) for k, v in payload.items()}

    def set(
        self,
        cache_key: str,
        mapping: dict[str, str],
        corp_code: str | None = None,
        note_type: str | None = None,
    ) -> None:
        if not isinstance(mapping, dict):
            raise CacheDBError("mapping은 dict 형식이어야 합니다.")
        payload = json.dumps(mapping, ensure_ascii=False)
        self.conn.execute(
            """
            INSERT OR REPLACE INTO account_mapping
            (cache_key, corp_code, note_type, mapping_json)
            VALUES (?, ?, ?, ?)
            """,
            (cache_key, corp_code, note_type, payload),
        )
        self.conn.commit()

    def invalidate(self, cache_key: str) -> int:
        cur = self.conn.execute(
            "DELETE FROM account_mapping WHERE cache_key = ?",
            (cache_key,),
        )
        self.conn.commit()
        return int(cur.rowcount or 0)

    def clear(self) -> int:
        cur = self.conn.execute("DELETE FROM account_mapping")
        self.conn.commit()
        return int(cur.rowcount or 0)

    def stats(self) -> dict[str, Any]:
        total = self.conn.execute("SELECT COUNT(*) FROM account_mapping").fetchone()
        total_rows = int(total[0]) if total else 0
        by_note_rows = self.conn.execute(
            """
            SELECT COALESCE(note_type, ''), COUNT(*)
            FROM account_mapping
            GROUP BY note_type
            ORDER BY COUNT(*) DESC
            """
        ).fetchall()
        by_note = {str(row[0]): int(row[1]) for row in by_note_rows}
        return {
            "path": str(self.db_path),
            "total_rows": total_rows,
            "by_note_type": by_note,
        }

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "CacheDB":
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        self.close()
