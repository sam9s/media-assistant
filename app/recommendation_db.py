from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
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

    create table if not exists reco.media_item (
        id bigserial primary key,
        media_type text not null,
        source_system text not null,
        source_item_id text not null,
        title text not null,
        normalized_title text not null,
        primary_creator text,
        contributors jsonb not null default '[]'::jsonb,
        album_or_series text,
        year integer,
        genres jsonb not null default '[]'::jsonb,
        tags jsonb not null default '[]'::jsonb,
        subjects jsonb not null default '[]'::jsonb,
        language text,
        library_path text,
        in_library boolean not null default true,
        canonical_external_ids jsonb not null default '{}'::jsonb,
        metadata jsonb not null default '{}'::jsonb,
        created_at timestamptz not null default now(),
        updated_at timestamptz not null default now(),
        unique (source_system, source_item_id)
    );

    create table if not exists reco.activity_event (
        id bigserial primary key,
        source_system text not null,
        source_event_id text not null,
        media_item_id bigint not null references reco.media_item(id) on delete cascade,
        event_type text not null,
        event_at timestamptz not null,
        completion_ratio double precision,
        play_count_delta integer,
        progress_percent double precision,
        raw_payload_json jsonb not null default '{}'::jsonb,
        created_at timestamptz not null default now(),
        unique (source_system, source_event_id)
    );

    create table if not exists reco.external_enrichment_cache (
        cache_key text primary key,
        source text not null,
        payload jsonb not null default '{}'::jsonb,
        expires_at timestamptz,
        updated_at timestamptz not null default now()
    );

    create table if not exists reco.preference_signal (
        user_key text not null,
        facet_type text not null,
        facet_value text not null,
        score_7d double precision not null default 0,
        score_30d double precision not null default 0,
        last_seen_at timestamptz,
        primary key (user_key, facet_type, facet_value)
    );

    create table if not exists reco.link_edge (
        id bigserial primary key,
        from_media_item_id bigint not null references reco.media_item(id) on delete cascade,
        to_media_item_id bigint not null references reco.media_item(id) on delete cascade,
        edge_type text not null,
        weight double precision not null default 0,
        source text not null,
        explanation_json jsonb not null default '{}'::jsonb,
        unique (from_media_item_id, to_media_item_id, edge_type, source)
    );

    create table if not exists reco.recommendation_run (
        id bigserial primary key,
        run_type text not null,
        status text not null default 'running',
        request_json jsonb not null default '{}'::jsonb,
        result_json jsonb not null default '{}'::jsonb,
        started_at timestamptz not null default now(),
        finished_at timestamptz,
        error_text text
    );

    create table if not exists reco.recommendation_candidate (
        id bigserial primary key,
        run_id bigint not null references reco.recommendation_run(id) on delete cascade,
        recommendation_type text not null,
        title text not null,
        media_type text not null,
        in_library boolean not null default true,
        source_system text,
        candidate_source text not null,
        shared_theme text,
        why_text text not null default '',
        acquisition_path text not null default 'none',
        confidence_score double precision not null default 0,
        payload jsonb not null default '{}'::jsonb,
        created_at timestamptz not null default now()
    );

    create table if not exists reco.weekly_digest (
        id bigserial primary key,
        run_id bigint references reco.recommendation_run(id) on delete set null,
        generated_at timestamptz not null default now(),
        theme_title text not null,
        theme_summary text not null default '',
        music_item jsonb not null default '{}'::jsonb,
        book_item jsonb not null default '{}'::jsonb,
        movie_item jsonb not null default '{}'::jsonb,
        supporting_activity jsonb not null default '[]'::jsonb,
        llm_summary text,
        llm_message_text text,
        status text not null default 'ready'
    );
    """
    with get_conn() as conn:
        conn.execute(ddl)


def create_run(run_type: str, request_payload: dict[str, Any]) -> int:
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute(
            """
            insert into reco.recommendation_run (run_type, request_json)
            values (%s, %s::jsonb)
            returning id
            """,
            (run_type, json.dumps(request_payload)),
        ).fetchone()
        return int(row["id"])


def finish_run(run_id: int, status: str, result_payload: dict[str, Any], error_text: Optional[str] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            update reco.recommendation_run
            set status = %s,
                result_json = %s::jsonb,
                finished_at = now(),
                error_text = %s
            where id = %s
            """,
            (status, json.dumps(result_payload), error_text, run_id),
        )


def upsert_media_item(
    *,
    media_type: str,
    source_system: str,
    source_item_id: str,
    title: str,
    normalized_title: str,
    primary_creator: Optional[str] = None,
    contributors: Optional[list[str]] = None,
    album_or_series: Optional[str] = None,
    year: Optional[int] = None,
    genres: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    subjects: Optional[list[str]] = None,
    language: Optional[str] = None,
    library_path: Optional[str] = None,
    in_library: bool = True,
    canonical_external_ids: Optional[dict[str, Any]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            insert into reco.media_item (
                media_type, source_system, source_item_id, title, normalized_title,
                primary_creator, contributors, album_or_series, year, genres, tags,
                subjects, language, library_path, in_library, canonical_external_ids, metadata,
                updated_at
            )
            values (
                %s, %s, %s, %s, %s,
                %s, %s::jsonb, %s, %s, %s::jsonb, %s::jsonb,
                %s::jsonb, %s, %s, %s, %s::jsonb, %s::jsonb,
                now()
            )
            on conflict (source_system, source_item_id) do update set
                media_type = excluded.media_type,
                title = excluded.title,
                normalized_title = excluded.normalized_title,
                primary_creator = excluded.primary_creator,
                contributors = excluded.contributors,
                album_or_series = excluded.album_or_series,
                year = excluded.year,
                genres = excluded.genres,
                tags = excluded.tags,
                subjects = excluded.subjects,
                language = excluded.language,
                library_path = excluded.library_path,
                in_library = excluded.in_library,
                canonical_external_ids = excluded.canonical_external_ids,
                metadata = excluded.metadata,
                updated_at = now()
            returning id
            """,
            (
                media_type,
                source_system,
                source_item_id,
                title,
                normalized_title,
                primary_creator,
                json.dumps(contributors or []),
                album_or_series,
                year,
                json.dumps(genres or []),
                json.dumps(tags or []),
                json.dumps(subjects or []),
                language,
                library_path,
                in_library,
                json.dumps(canonical_external_ids or {}),
                json.dumps(metadata or {}),
            ),
        ).fetchone()
        return int(row["id"])


def insert_activity_event(
    *,
    source_system: str,
    source_event_id: str,
    media_item_id: int,
    event_type: str,
    event_at: datetime,
    completion_ratio: Optional[float] = None,
    play_count_delta: Optional[int] = None,
    progress_percent: Optional[float] = None,
    raw_payload_json: Optional[dict[str, Any]] = None,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            insert into reco.activity_event (
                source_system, source_event_id, media_item_id, event_type, event_at,
                completion_ratio, play_count_delta, progress_percent, raw_payload_json
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            on conflict (source_system, source_event_id) do update set
                media_item_id = excluded.media_item_id,
                event_type = excluded.event_type,
                event_at = excluded.event_at,
                completion_ratio = excluded.completion_ratio,
                play_count_delta = excluded.play_count_delta,
                progress_percent = excluded.progress_percent,
                raw_payload_json = excluded.raw_payload_json
            """,
            (
                source_system,
                source_event_id,
                media_item_id,
                event_type,
                event_at,
                completion_ratio,
                play_count_delta,
                progress_percent,
                json.dumps(raw_payload_json or {}),
            ),
        )


def replace_signals(user_key: str, rows: list[dict[str, Any]]) -> None:
    with get_conn() as conn:
        conn.execute("delete from reco.preference_signal where user_key = %s", (user_key,))
        for row in rows:
            conn.execute(
                """
                insert into reco.preference_signal (
                    user_key, facet_type, facet_value, score_7d, score_30d, last_seen_at
                )
                values (%s, %s, %s, %s, %s, %s)
                """,
                (
                    user_key,
                    row["facet_type"],
                    row["facet_value"],
                    row.get("score_7d", 0),
                    row.get("score_30d", 0),
                    row.get("last_seen_at"),
                ),
            )


def top_signals(user_key: str, limit: int = 12) -> list[dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            """
            select *
            from reco.preference_signal
            where user_key = %s
            order by score_7d desc, score_30d desc, last_seen_at desc nulls last
            limit %s
            """,
            (user_key, limit),
        ).fetchall()
        return list(rows)


def recent_activity(lookback_days: int, limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema()
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    with get_conn() as conn:
        rows = conn.execute(
            """
            select
                e.id,
                e.source_system,
                e.source_event_id,
                e.event_type,
                e.event_at,
                e.completion_ratio,
                e.play_count_delta,
                e.progress_percent,
                m.id as media_item_id,
                m.media_type,
                m.title,
                m.normalized_title,
                m.primary_creator,
                m.album_or_series,
                m.year,
                m.genres,
                m.tags,
                m.subjects,
                m.language,
                m.library_path,
                m.in_library,
                m.canonical_external_ids
            from reco.activity_event e
            join reco.media_item m on m.id = e.media_item_id
            where e.event_at >= %s
            order by e.event_at desc
            limit %s
            """,
            (cutoff, limit),
        ).fetchall()
        return list(rows)


def library_candidates(media_type: str, exclude_normalized_titles: list[str], limit: int = 100) -> list[dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            """
            select *
            from reco.media_item
            where media_type = %s
              and in_library = true
              and not (normalized_title = any(%s))
            order by updated_at desc
            limit %s
            """,
            (media_type, exclude_normalized_titles or [""], limit),
        ).fetchall()
        return list(rows)


def search_media(seed_value: str, limit: int = 25) -> list[dict[str, Any]]:
    ensure_schema()
    term = f"%{seed_value.strip().lower()}%"
    with get_conn() as conn:
        rows = conn.execute(
            """
            select *
            from reco.media_item
            where lower(title) like %s
               or lower(coalesce(primary_creator, '')) like %s
               or lower(coalesce(album_or_series, '')) like %s
            order by updated_at desc
            limit %s
            """,
            (term, term, term, limit),
        ).fetchall()
        return list(rows)


def insert_candidate(
    run_id: int,
    recommendation_type: str,
    candidate: dict[str, Any],
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            insert into reco.recommendation_candidate (
                run_id, recommendation_type, title, media_type, in_library,
                source_system, candidate_source, shared_theme, why_text,
                acquisition_path, confidence_score, payload
            )
            values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                run_id,
                recommendation_type,
                candidate["title"],
                candidate["media_type"],
                candidate.get("in_library", True),
                candidate.get("source_system"),
                candidate.get("candidate_source", candidate.get("source_system") or "library"),
                candidate.get("shared_theme"),
                candidate.get("why", ""),
                candidate.get("acquisition_path", "none"),
                float(candidate.get("confidence_score", 0)),
                json.dumps(candidate),
            ),
        )


def insert_weekly_digest(
    *,
    run_id: int,
    theme_title: str,
    theme_summary: str,
    music_item: dict[str, Any],
    book_item: dict[str, Any],
    movie_item: dict[str, Any],
    supporting_activity: list[dict[str, Any]],
    llm_summary: Optional[str],
    llm_message_text: Optional[str],
    status: str = "ready",
) -> int:
    with get_conn() as conn:
        row = conn.execute(
            """
            insert into reco.weekly_digest (
                run_id, theme_title, theme_summary, music_item, book_item, movie_item,
                supporting_activity, llm_summary, llm_message_text, status
            )
            values (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s)
            returning id
            """,
            (
                run_id,
                theme_title,
                theme_summary,
                json.dumps(music_item),
                json.dumps(book_item),
                json.dumps(movie_item),
                json.dumps(supporting_activity),
                llm_summary,
                llm_message_text,
                status,
            ),
        ).fetchone()
        return int(row["id"])


def latest_digest() -> Optional[dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute(
            """
            select *
            from reco.weekly_digest
            order by generated_at desc, id desc
            limit 1
            """
        ).fetchone()
        return dict(row) if row else None


def digest_history(limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        rows = conn.execute(
            """
            select *
            from reco.weekly_digest
            order by generated_at desc, id desc
            limit %s
            """,
            (limit,),
        ).fetchall()
        return list(rows)


def latest_run(run_type: Optional[str] = None) -> Optional[dict[str, Any]]:
    ensure_schema()
    sql = """
        select *
        from reco.recommendation_run
        {where_clause}
        order by started_at desc, id desc
        limit 1
    """
    params: tuple[Any, ...] = ()
    where_clause = ""
    if run_type:
        where_clause = "where run_type = %s"
        params = (run_type,)
    with get_conn() as conn:
        row = conn.execute(sql.format(where_clause=where_clause), params).fetchone()
        return dict(row) if row else None


def cache_get(cache_key: str) -> Optional[dict[str, Any]]:
    ensure_schema()
    with get_conn() as conn:
        row = conn.execute(
            """
            select payload
            from reco.external_enrichment_cache
            where cache_key = %s
              and (expires_at is null or expires_at > now())
            """,
            (cache_key,),
        ).fetchone()
        return row["payload"] if row else None


def cache_put(cache_key: str, source: str, payload: dict[str, Any], ttl_hours: Optional[int] = 168) -> None:
    expires_at = None
    if ttl_hours:
        expires_at = datetime.now(UTC) + timedelta(hours=ttl_hours)
    with get_conn() as conn:
        conn.execute(
            """
            insert into reco.external_enrichment_cache (cache_key, source, payload, expires_at, updated_at)
            values (%s, %s, %s::jsonb, %s, now())
            on conflict (cache_key) do update set
                source = excluded.source,
                payload = excluded.payload,
                expires_at = excluded.expires_at,
                updated_at = now()
            """,
            (cache_key, source, json.dumps(payload), expires_at),
        )
