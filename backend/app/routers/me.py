"""Single-user state: preferences, play history, home feed."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection

from ..db import fetch_all, fetch_one, fetch_val, get_conn
from ..deps import paging
from ..schemas import HistoryEntry, PreferencesUpdate

router = APIRouter(prefix="/api", tags=["me"])


@router.get("/preferences")
async def get_preferences(conn: AsyncConnection = Depends(get_conn)) -> dict:
    return await fetch_one(conn, "SELECT * FROM user_preferences WHERE id = 1")


@router.patch("/preferences")
async def update_preferences(body: PreferencesUpdate, conn: AsyncConnection = Depends(get_conn)) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        return await get_preferences(conn)
    casts = {"repeat": "repeat_mode", "preferred_mode": "media_kind",
             "queue_track_ids": "bigint[]", "extra": "jsonb"}
    if "extra" in fields:
        fields["extra"] = json.dumps(fields["extra"] or {})
    sets = ", ".join(f"{k} = CAST(:{k} AS {casts[k]})" if k in casts else f"{k} = :{k}" for k in fields)
    return await fetch_one(conn, f"UPDATE user_preferences SET {sets} WHERE id = 1 RETURNING *", **fields)


_HISTORY_INSERT = """
    INSERT INTO play_history (track_id, source_id, mode, context_type, context_id, started_at, ended_at,
                              played_ms, completed, skipped, was_offline, device)
    VALUES (:track_id, :source_id, CAST(:mode AS media_kind), CAST(:context_type AS play_context), :context_id,
            COALESCE(CAST(:started_at AS timestamptz), now()), :ended_at, :played_ms, :completed, :skipped,
            :was_offline, :device)
    RETURNING id
"""


async def _record(conn: AsyncConnection, e: HistoryEntry) -> int:
    if e.completed and e.skipped:
        raise HTTPException(status_code=400, detail="a play cannot be both completed and skipped")
    if not await fetch_val(conn, "SELECT 1 FROM tracks WHERE id = :id", id=e.track_id):
        raise HTTPException(status_code=400, detail=f"unknown track {e.track_id}")
    return await fetch_val(conn, _HISTORY_INSERT, **e.model_dump())


@router.post("/history", status_code=201)
async def record_play(body: HistoryEntry, conn: AsyncConnection = Depends(get_conn)) -> dict:
    """Log a finished listening session (sent when a track ends, is skipped,
    or replaced). Play counts update via a DB trigger (>= 30 s or completed)."""
    return {"id": await _record(conn, body)}


@router.post("/history/batch", status_code=201)
async def record_plays(body: list[HistoryEntry], conn: AsyncConnection = Depends(get_conn)) -> dict:
    """Bulk upload, used by the PWA to sync plays logged while offline."""
    ids = [await _record(conn, e) for e in body[:1000]]
    return {"ids": ids}


@router.get("/history")
async def history(page: dict = Depends(paging), conn: AsyncConnection = Depends(get_conn)) -> dict:
    items = await fetch_all(conn, """
        SELECT h.id AS play_id, h.started_at, h.ended_at, h.played_ms, h.completed, h.skipped,
               h.mode, h.context_type, h.context_id, v.*
          FROM play_history h JOIN v_tracks v ON v.id = h.track_id
         ORDER BY h.started_at DESC LIMIT :limit OFFSET :offset
    """, **page)
    return {"items": items, **page}


@router.get("/home")
async def home(conn: AsyncConnection = Depends(get_conn)) -> dict:
    """Shelves for the home screen."""
    recently_played = await fetch_all(conn, """
        SELECT v.* FROM (
            SELECT track_id, max(started_at) AS last FROM play_history GROUP BY track_id
             ORDER BY last DESC LIMIT 12) r
          JOIN v_tracks v ON v.id = r.track_id ORDER BY r.last DESC
    """)
    recently_added = await fetch_all(conn, """
        SELECT * FROM v_tracks WHERE in_library AND (has_audio OR has_video) ORDER BY created_at DESC LIMIT 12
    """)
    most_played = await fetch_all(conn, """
        SELECT * FROM v_tracks WHERE play_count > 0 ORDER BY play_count DESC LIMIT 12
    """)
    new_albums = await fetch_all(conn, """
        SELECT al.id, al.title, al.cover_path, al.dominant_color, al.release_year, ar.name AS artist_name
          FROM albums al LEFT JOIN artists ar ON ar.id = al.album_artist_id
         WHERE EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.in_library)
         ORDER BY al.created_at DESC LIMIT 12
    """)
    music_videos = await fetch_all(conn, """
        SELECT * FROM v_tracks WHERE has_video AND in_library ORDER BY last_played_at DESC NULLS LAST, created_at DESC LIMIT 12
    """)
    top_artists = await fetch_all(conn, """
        SELECT a.id, a.name, a.image_path,
               sum(t.play_count)::int AS plays, count(*)::int AS track_count,
               (SELECT al.cover_path FROM albums al JOIN tracks t2 ON t2.album_id = al.id
                 WHERE t2.primary_artist_id = a.id AND al.cover_path IS NOT NULL
                 ORDER BY t2.play_count DESC LIMIT 1) AS cover_path
          FROM artists a JOIN tracks t ON t.primary_artist_id = a.id
         GROUP BY a.id
         ORDER BY sum(t.play_count) DESC, count(*) DESC, lower(a.name)
         LIMIT 12
    """)
    genres = await fetch_all(conn, """
        SELECT genre AS name, count(*)::int AS track_count,
               (array_agg(cover_path ORDER BY play_count DESC) FILTER (WHERE cover_path IS NOT NULL))[1] AS cover_path
          FROM v_tracks WHERE in_library AND genre IS NOT NULL AND btrim(genre) <> ''
         GROUP BY genre ORDER BY count(*) DESC, genre LIMIT 8
    """)
    return {
        "top_artists": top_artists,
        "genres": genres,
        "recently_played": recently_played,
        "recently_added": recently_added,
        "most_played": most_played,
        "new_albums": new_albums,
        "music_videos": music_videos,
    }
