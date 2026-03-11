from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, Iterator, Optional

import psycopg
from psycopg.rows import dict_row

from app.config import settings


@contextmanager
def get_conn() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(settings.RECOMMENDATIONS_DATABASE_URL, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure_schema() -> None:
    ddl = """
    create schema if not exists reco;

    create table if not exists reco.media_job (
        job_id text primary key,
        pipeline text not null,
        title text not null,
        status text not null,
        progress_percent double precision,
        bytes_done bigint,
        bytes_total bigint,
        speed_bytes_per_second double precision,
        eta_seconds integer,
        files_done integer,
        files_total integer,
        message text,
        started_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        payload jsonb not null default '{}'::jsonb
    );
    """
    with get_conn() as conn:
        conn.execute(ddl)


def upsert_job(
    *,
    job_id: str,
    pipeline: str,
    title: str,
    status: str,
    progress_percent: Optional[float] = None,
    bytes_done: Optional[int] = None,
    bytes_total: Optional[int] = None,
    speed_bytes_per_second: Optional[float] = None,
    eta_seconds: Optional[int] = None,
    files_done: Optional[int] = None,
    files_total: Optional[int] = None,
    message: Optional[str] = None,
    started_at: Optional[float] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    ensure_schema()
    started_dt = datetime.fromtimestamp(started_at, tz=UTC) if started_at else datetime.now(UTC)
    with get_conn() as conn:
        conn.execute(
            """
            insert into reco.media_job (
                job_id, pipeline, title, status, progress_percent, bytes_done, bytes_total,
                speed_bytes_per_second, eta_seconds, files_done, files_total, message,
                started_at, updated_at, payload
            )
            values (
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, now(), %s::jsonb
            )
            on conflict (job_id) do update set
                pipeline = excluded.pipeline,
                title = excluded.title,
                status = excluded.status,
                progress_percent = excluded.progress_percent,
                bytes_done = excluded.bytes_done,
                bytes_total = excluded.bytes_total,
                speed_bytes_per_second = excluded.speed_bytes_per_second,
                eta_seconds = excluded.eta_seconds,
                files_done = excluded.files_done,
                files_total = excluded.files_total,
                message = excluded.message,
                updated_at = now(),
                payload = excluded.payload
            """,
            (
                job_id,
                pipeline,
                title,
                status,
                progress_percent,
                bytes_done,
                bytes_total,
                speed_bytes_per_second,
                eta_seconds,
                files_done,
                files_total,
                message,
                started_dt,
                json.dumps(payload or {}),
            ),
        )


def get_job(job_id: str) -> Optional[dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute(
            """
            select job_id, pipeline, title, status, progress_percent, bytes_done, bytes_total,
                   speed_bytes_per_second, eta_seconds, files_done, files_total, message,
                   extract(epoch from started_at) as started_at,
                   extract(epoch from updated_at) as updated_at
            from reco.media_job
            where job_id = %s
            """,
            (job_id,),
        ).fetchone()
        return dict(row) if row else None


def list_jobs(pipeline: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        if pipeline:
            rows = conn.execute(
                """
                select job_id, pipeline, title, status, progress_percent, bytes_done, bytes_total,
                       speed_bytes_per_second, eta_seconds, files_done, files_total, message,
                       extract(epoch from started_at) as started_at,
                       extract(epoch from updated_at) as updated_at
                from reco.media_job
                where pipeline = %s
                order by updated_at desc
                limit %s
                """,
                (pipeline, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                select job_id, pipeline, title, status, progress_percent, bytes_done, bytes_total,
                       speed_bytes_per_second, eta_seconds, files_done, files_total, message,
                       extract(epoch from started_at) as started_at,
                       extract(epoch from updated_at) as updated_at
                from reco.media_job
                order by updated_at desc
                limit %s
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
