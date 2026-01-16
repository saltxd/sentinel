"""SQLite-based metrics storage with retention management."""

import asyncio
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import structlog

logger = structlog.get_logger()


class MetricsDB:
    """SQLite storage for container metrics and events."""

    def __init__(self, db_path: str, retention_hours: int = 48):
        self.db_path = Path(db_path)
        self.retention_hours = retention_hours

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._init_db()

    @contextmanager
    def _get_conn(self) -> Iterator[sqlite3.Connection]:
        """Get a database connection with row factory."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._get_conn() as conn:
            conn.executescript("""
                -- Container metrics (CPU, memory, etc.)
                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    container_id TEXT NOT NULL,
                    container_name TEXT NOT NULL,
                    metric_name TEXT NOT NULL,
                    metric_value REAL NOT NULL,
                    labels TEXT  -- JSON for additional labels
                );

                CREATE INDEX IF NOT EXISTS idx_metrics_container_time
                ON metrics(container_name, timestamp);

                CREATE INDEX IF NOT EXISTS idx_metrics_time
                ON metrics(timestamp);

                -- Container events (restarts, OOM, etc.)
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    container_id TEXT NOT NULL,
                    container_name TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    details TEXT  -- JSON
                );

                CREATE INDEX IF NOT EXISTS idx_events_time
                ON events(timestamp);

                -- Escalation tracking (cooldown management)
                CREATE TABLE IF NOT EXISTS escalations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    issue_key TEXT NOT NULL,
                    container_name TEXT,
                    details TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_escalations_key_time
                ON escalations(issue_key, timestamp);
            """)

        logger.info("database_initialized", path=str(self.db_path))

    def store_metric(
        self,
        container_id: str,
        container_name: str,
        metric_name: str,
        value: float,
        labels: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Store a single metric value."""
        ts = timestamp or datetime.now(timezone.utc)

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO metrics (timestamp, container_id, container_name, metric_name, metric_value, labels)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    ts.isoformat(),
                    container_id,
                    container_name,
                    metric_name,
                    value,
                    json.dumps(labels) if labels else None,
                ),
            )

    def store_metrics_batch(
        self,
        metrics: list[tuple[str, str, str, float, dict[str, Any] | None]],
        timestamp: datetime | None = None,
    ) -> None:
        """Store multiple metrics in a single transaction.

        Args:
            metrics: List of (container_id, container_name, metric_name, value, labels)
        """
        ts = (timestamp or datetime.now(timezone.utc)).isoformat()

        with self._get_conn() as conn:
            conn.executemany(
                """
                INSERT INTO metrics (timestamp, container_id, container_name, metric_name, metric_value, labels)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (ts, cid, cname, mname, val, json.dumps(labels) if labels else None)
                    for cid, cname, mname, val, labels in metrics
                ],
            )

    def store_event(
        self,
        container_id: str,
        container_name: str,
        event_type: str,
        details: dict[str, Any] | None = None,
        timestamp: datetime | None = None,
    ) -> None:
        """Store a container event."""
        ts = timestamp or datetime.now(timezone.utc)

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO events (timestamp, container_id, container_name, event_type, details)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ts.isoformat(),
                    container_id,
                    container_name,
                    event_type,
                    json.dumps(details) if details else None,
                ),
            )

    def get_metrics_for_baseline(
        self,
        container_name: str,
        metric_name: str,
        hours: int = 24,
    ) -> list[float]:
        """Get metric values for baseline calculation."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                SELECT metric_value FROM metrics
                WHERE container_name = ? AND metric_name = ? AND timestamp > ?
                ORDER BY timestamp ASC
                """,
                (container_name, metric_name, cutoff.isoformat()),
            )
            return [row["metric_value"] for row in cursor.fetchall()]

    def get_recent_events(
        self,
        container_name: str | None = None,
        hours: int = 1,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get recent events, optionally filtered."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

        query = "SELECT * FROM events WHERE timestamp > ?"
        params: list[Any] = [cutoff.isoformat()]

        if container_name:
            query += " AND container_name = ?"
            params.append(container_name)

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        query += " ORDER BY timestamp DESC"

        with self._get_conn() as conn:
            cursor = conn.execute(query, params)
            return [
                {
                    "timestamp": row["timestamp"],
                    "container_id": row["container_id"],
                    "container_name": row["container_name"],
                    "event_type": row["event_type"],
                    "details": json.loads(row["details"]) if row["details"] else None,
                }
                for row in cursor.fetchall()
            ]

    def record_escalation(
        self,
        issue_key: str,
        container_name: str | None = None,
        details: str | None = None,
    ) -> None:
        """Record an escalation for cooldown tracking."""
        ts = datetime.now(timezone.utc)

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO escalations (timestamp, issue_key, container_name, details)
                VALUES (?, ?, ?, ?)
                """,
                (ts.isoformat(), issue_key, container_name, details),
            )

    def get_last_escalation(self, issue_key: str) -> datetime | None:
        """Get the timestamp of the last escalation for an issue."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                SELECT timestamp FROM escalations
                WHERE issue_key = ?
                ORDER BY timestamp DESC
                LIMIT 1
                """,
                (issue_key,),
            )
            row = cursor.fetchone()
            if row:
                return datetime.fromisoformat(row["timestamp"])
        return None

    def cleanup_old_data(self) -> int:
        """Remove data older than retention period. Returns rows deleted."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.retention_hours)
        cutoff_str = cutoff.isoformat()

        total_deleted = 0

        with self._get_conn() as conn:
            for table in ["metrics", "events", "escalations"]:
                cursor = conn.execute(
                    f"DELETE FROM {table} WHERE timestamp < ?",  # noqa: S608
                    (cutoff_str,),
                )
                total_deleted += cursor.rowcount

        if total_deleted > 0:
            logger.info("cleanup_completed", rows_deleted=total_deleted)

        return total_deleted

    def get_stats(self) -> dict[str, int]:
        """Get database statistics."""
        with self._get_conn() as conn:
            stats = {}
            for table in ["metrics", "events", "escalations"]:
                cursor = conn.execute(f"SELECT COUNT(*) as count FROM {table}")  # noqa: S608
                stats[table] = cursor.fetchone()["count"]
            return stats

    # Async wrappers to avoid blocking the event loop

    async def store_metrics_batch_async(
        self,
        metrics: list[tuple[str, str, str, float, dict[str, Any] | None]],
        timestamp: datetime | None = None,
    ) -> None:
        """Async wrapper for store_metrics_batch."""
        await asyncio.to_thread(self.store_metrics_batch, metrics, timestamp)

    async def get_metrics_for_baseline_async(
        self,
        container_name: str,
        metric_name: str,
        hours: int = 24,
    ) -> list[float]:
        """Async wrapper for get_metrics_for_baseline."""
        return await asyncio.to_thread(
            self.get_metrics_for_baseline, container_name, metric_name, hours
        )

    async def record_escalation_async(
        self,
        issue_key: str,
        container_name: str | None = None,
        details: str | None = None,
    ) -> None:
        """Async wrapper for record_escalation."""
        await asyncio.to_thread(
            self.record_escalation, issue_key, container_name, details
        )

    async def get_last_escalation_async(self, issue_key: str) -> datetime | None:
        """Async wrapper for get_last_escalation."""
        return await asyncio.to_thread(self.get_last_escalation, issue_key)

    async def cleanup_old_data_async(self) -> int:
        """Async wrapper for cleanup_old_data."""
        return await asyncio.to_thread(self.cleanup_old_data)
