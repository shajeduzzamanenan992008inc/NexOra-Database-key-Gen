"""PostgreSQL access layer.

The service talks to Supabase PostgreSQL directly over TLS. Supabase is
PostgreSQL, and license activation requires row-level locking inside a single
transaction; using the database connection (rather than PostgREST) is what
makes race-free activation possible. Credentials stay on the server.

All SQL executed by this application is parameterized. This module never
interpolates values into SQL text.
"""

from __future__ import annotations

import queue
import ssl
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import pg8000.dbapi as pgdb

from .config import DatabaseSettings


class DatabaseError(RuntimeError):
    """Base class for database failures safe to translate into HTTP 503."""


class DatabaseUnavailable(DatabaseError):
    """The pool or the database is temporarily unreachable."""


def sqlstate(exc: BaseException) -> str | None:
    """Return the PostgreSQL SQLSTATE carried by a pg8000 exception."""
    for arg in exc.args:
        if isinstance(arg, dict):
            code = arg.get("C")
            if isinstance(code, str):
                return code
    return None


def is_unique_violation(exc: BaseException) -> bool:
    return sqlstate(exc) == "23505"


def is_foreign_key_violation(exc: BaseException) -> bool:
    return sqlstate(exc) == "23503"


def _rows(cursor: Any) -> list[dict[str, Any]]:
    if not cursor.description:
        return []
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


class Database:
    """Small, dependency-light connection pool.

    pg8000 is used because it is pure Python, works on every supported host
    (including Windows without build tools), and supports TLS. Connections are
    never shared concurrently: a connection is checked out for one block and
    returned to the idle queue afterwards.
    """

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._idle: queue.LifoQueue[Any] = queue.LifoQueue(maxsize=settings.pool_max)
        self._slots = threading.BoundedSemaphore(settings.pool_max)
        self._closed = False
        self._lock = threading.Lock()

    # -- connection lifecycle -------------------------------------------------

    def _connect(self) -> Any:
        ssl_context = None
        if self._settings.uses_ssl:
            # `require` and stronger are treated as verified TLS here. This is
            # stricter than libpq's `require` and works with Supabase's CA.
            # Providers that use a private CA (Supabase's pooler does) need
            # DATABASE_SSLROOTCERT; the bundle supplements the system roots.
            ssl_context = ssl.create_default_context(
                cafile=self._settings.sslrootcert or None
            )
        try:
            connection = pgdb.connect(
                user=self._settings.user,
                password=self._settings.password,
                host=self._settings.host,
                port=self._settings.port,
                database=self._settings.database,
                timeout=self._settings.connect_timeout,
                ssl_context=ssl_context,
            )
            connection.autocommit = False
            return connection
        except Exception as exc:  # pragma: no cover - depends on network state
            raise DatabaseUnavailable(
                f"Could not connect to the database at {self._settings.dsn_summary}."
            ) from exc

    def _discard(self, connection: Any) -> None:
        try:
            connection.close()
        except Exception:
            pass

    @staticmethod
    def _ping(connection: Any) -> bool:
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchall()
            connection.rollback()
            return True
        except Exception:
            return False

    def _acquire(self) -> Any:
        with self._lock:
            if self._closed:
                raise DatabaseUnavailable("The database pool is closed.")
        if not self._slots.acquire(timeout=self._settings.pool_acquire_timeout):
            raise DatabaseUnavailable("Timed out waiting for a database connection.")
        try:
            try:
                connection = self._idle.get_nowait()
            except queue.Empty:
                return self._connect()

            if self._ping(connection):
                return connection
            self._discard(connection)
            return self._connect()
        except BaseException:
            self._slots.release()
            raise

    def _release(self, connection: Any, *, broken: bool) -> None:
        try:
            if broken or self._closed:
                self._discard(connection)
            else:
                try:
                    self._idle.put_nowait(connection)
                except queue.Full:
                    self._discard(connection)
        finally:
            self._slots.release()

    @contextmanager
    def cursor(self) -> Iterator[Any]:
        """Run one transaction on a pooled connection.

        Commits on success, rolls back on any exception, and removes broken
        connections from the pool instead of reusing them.
        """
        connection = self._acquire()
        broken = False
        try:
            cursor = connection.cursor()
            try:
                yield cursor
                connection.commit()
            except BaseException as exc:
                if isinstance(
                    exc,
                    (pgdb.InterfaceError, pgdb.OperationalError, pgdb.InternalError),
                ):
                    broken = True
                try:
                    connection.rollback()
                except Exception:
                    broken = True
                raise
        finally:
            self._release(connection, broken=broken)

    # -- query helpers --------------------------------------------------------

    def fetch_all(
        self, sql: str, params: Sequence[Any] | None = ()
    ) -> list[dict[str, Any]]:
        with self.cursor() as cursor:
            cursor.execute(sql, tuple(() if params is None else params))
            return _rows(cursor)

    def fetch_one(
        self, sql: str, params: Sequence[Any] | None = ()
    ) -> dict[str, Any] | None:
        rows = self.fetch_all(sql, () if params is None else params)
        return rows[0] if rows else None

    def fetch_value(self, sql: str, params: Sequence[Any] | None = ()) -> Any:
        with self.cursor() as cursor:
            cursor.execute(sql, tuple(() if params is None else params))
            row = cursor.fetchone()
            return row[0] if row else None

    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return cursor.rowcount

    def ping(self) -> bool:
        try:
            self.fetch_value("SELECT 1")
            return True
        except Exception:
            return False

    def close(self) -> None:
        with self._lock:
            self._closed = True
        while True:
            try:
                connection = self._idle.get_nowait()
            except queue.Empty:
                return
            self._discard(connection)
