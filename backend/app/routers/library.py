"""Library: folders + scanning, browse (tracks/albums/artists), search, lyrics."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncConnection

from ..db import execute, fetch_all, fetch_one, fetch_val, get_conn
from ..deps import get_http, not_found, paging
from ..schemas import FolderCreate, FolderUpdate, LyricsUpdate, ScanRequest, SourceUpdate, TrackUpdate
from ..services import catalog
from ..services.lyrics import fetch_lrclib, parse_lrc
from ..services.scanner import _norm, scanner

router = APIRouter(prefix="/api", tags=["library"])


# --- folders & scanning --------------------------------------------------------------

@router.get("/library/folders")
async def list_folders(conn: AsyncConnection = Depends(get_conn)) -> list[dict]:
    return await fetch_all(conn, "SELECT * FROM library_folders ORDER BY id")


@router.post("/library/folders", status_code=201)
async def add_folder(body: FolderCreate, conn: AsyncConnection = Depends(get_conn)) -> dict:
    p = Path(body.path).expanduser()
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"not a directory: {p}")
    row = await fetch_one(conn, """
        INSERT INTO library_folders (path, recursive) VALUES (:p, :r)
        ON CONFLICT (path) DO UPDATE SET is_enabled = true, recursive = EXCLUDED.recursive
        RETURNING *
    """, p=_norm(p), r=body.recursive)
    return row


@router.patch("/library/folders/{folder_id}")
async def update_folder(folder_id: int, body: FolderUpdate, conn: AsyncConnection = Depends(get_conn)) -> dict:
    row = await fetch_one(conn, """
        UPDATE library_folders SET is_enabled = COALESCE(:e, is_enabled), recursive = COALESCE(:r, recursive)
         WHERE id = :id RETURNING *
    """, id=folder_id, e=body.is_enabled, r=body.recursive)
    if not row:
        raise not_found("folder")
    return row


@router.delete("/library/folders/{folder_id}", status_code=204)
async def remove_folder(folder_id: int, conn: AsyncConnection = Depends(get_conn)) -> None:
    """Stop indexing a folder. Its files are flagged unavailable; tracks,
    playlists and history are kept (re-adding the folder restores them)."""
    path = await fetch_val(conn, "DELETE FROM library_folders WHERE id = :id RETURNING path", id=folder_id)
    if path is None:
        raise not_found("folder")
    prefix = path.rstrip("\\/") + ("\\" if "\\" in path else "/")
    await execute(conn, """
        UPDATE media_sources SET is_available = false, is_preferred = false
         WHERE origin = 'local' AND left(file_path, length(:p)) = :p
    """, p=prefix)


@router.post("/library/scan", status_code=202)
async def start_scan(body: ScanRequest | None = None) -> dict:
    body = body or ScanRequest()
    started = scanner.start(body.folder_ids, full=body.full)
    return {"started": started, "status": scanner.status()}


@router.get("/library/scan")
async def scan_status() -> dict:
    return scanner.status()


@router.get("/library/stats")
async def stats(conn: AsyncConnection = Depends(get_conn)) -> dict:
    return await fetch_one(conn, """
        SELECT (SELECT count(*) FROM tracks WHERE in_library)                    AS tracks,
               (SELECT count(*) FROM albums)                                     AS albums,
               (SELECT count(*) FROM artists)                                    AS artists,
               (SELECT count(*) FROM playlists)                                  AS playlists,
               (SELECT count(*) FROM tracks WHERE is_favorite)                   AS favorites,
               (SELECT count(DISTINCT ms.track_id) FROM media_sources ms JOIN tracks t ON t.id = ms.track_id
                 WHERE ms.kind = 'video' AND ms.is_available AND t.in_library)  AS tracks_with_video,
               (SELECT COALESCE(sum(duration_ms), 0) FROM tracks WHERE in_library)::bigint AS total_duration_ms
    """)


# --- tracks ------------------------------------------------------------------------------

_TRACK_SORTS = {
    "recent": "created_at DESC, id DESC",
    "title": "lower(title), id",
    "artist": "lower(artist_name) NULLS LAST, lower(album_title), disc_number, track_number NULLS LAST, id",
    "album": "lower(album_title) NULLS LAST, disc_number, track_number NULLS LAST, id",
    "plays": "play_count DESC, last_played_at DESC NULLS LAST, id",
    "last_played": "last_played_at DESC NULLS LAST, id",
    "liked": "favorited_at DESC NULLS LAST, id",
}


@router.get("/tracks")
async def list_tracks(
    sort: Literal["recent", "title", "artist", "album", "plays", "last_played", "liked"] = "recent",
    favorites: bool = False,
    has_video: bool | None = None,
    genre: str | None = Query(default=None, max_length=200),
    ids: str | None = Query(default=None, description="comma-separated ids; results keep this order"),
    page: dict = Depends(paging),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    if ids:
        # Used to restore the saved queue: keep the caller's order, skip unknown ids.
        try:
            id_list = [int(x) for x in ids.split(",") if x.strip()][:500]
        except ValueError:
            raise HTTPException(status_code=400, detail="ids must be integers") from None
        items = await fetch_all(conn, """
            SELECT v.* FROM unnest(CAST(:ids AS bigint[])) WITH ORDINALITY AS q(id, ord)
              JOIN v_tracks v ON v.id = q.id ORDER BY q.ord
        """, ids=id_list)
        return {"items": items, "total": len(items), "limit": len(items), "offset": 0}

    where = ["(has_audio OR has_video)", "in_library"]
    params: dict = dict(page)
    if favorites:
        where.append("is_favorite")
    if has_video is not None:
        where.append("has_video" if has_video else "NOT has_video")
    if genre:
        where.append("lower(genre) = lower(:genre)")
        params["genre"] = genre
    w = " AND ".join(where)
    items = await fetch_all(conn, f"""
        SELECT * FROM v_tracks WHERE {w} ORDER BY {_TRACK_SORTS[sort]} LIMIT :limit OFFSET :offset
    """, **params)
    total = await fetch_val(conn, f"SELECT count(*) FROM v_tracks WHERE {w}",
                            **{k: v for k, v in params.items() if k == "genre"})
    return {"items": items, "total": total, **page}


@router.get("/tracks/{track_id}")
async def get_track(track_id: int, conn: AsyncConnection = Depends(get_conn)) -> dict:
    track = await fetch_one(conn, "SELECT * FROM v_tracks WHERE id = :id", id=track_id)
    if not track:
        raise not_found("track")
    track["artists"] = await fetch_all(conn, """
        SELECT a.id, a.name, ta.role, ta.position FROM track_artists ta JOIN artists a ON a.id = ta.artist_id
         WHERE ta.track_id = :id ORDER BY ta.position
    """, id=track_id)
    track["sources"] = await fetch_all(conn, """
        SELECT id, kind, origin, extractor, remote_id, page_url, mime_type, container, audio_codec,
               video_codec, bitrate_kbps, sample_rate_hz, bit_depth, channels, width, height, fps,
               duration_ms, sync_offset_ms, is_preferred, is_available, file_size_bytes,
               CASE WHEN origin = 'local' THEN file_path END AS file_path
          FROM media_sources WHERE track_id = :id ORDER BY kind, is_preferred DESC, id
    """, id=track_id)
    return track


@router.patch("/tracks/{track_id}")
async def update_track(track_id: int, body: TrackUpdate, conn: AsyncConnection = Depends(get_conn)) -> dict:
    row = await fetch_one(conn, """
        UPDATE tracks SET
            is_favorite  = COALESCE(CAST(:fav AS boolean), is_favorite),
            in_library   = in_library OR COALESCE(CAST(:fav AS boolean), false),
            favorited_at = CASE WHEN CAST(:fav AS boolean) IS NULL THEN favorited_at
                                WHEN CAST(:fav AS boolean) THEN COALESCE(favorited_at, now())
                                ELSE NULL END,
            rating   = COALESCE(:rating, rating),
            title    = COALESCE(:title, title),
            genre    = COALESCE(:genre, genre),
            explicit = COALESCE(:explicit, explicit)
         WHERE id = :id RETURNING id
    """, id=track_id, fav=body.is_favorite, rating=body.rating, title=body.title,
        genre=body.genre, explicit=body.explicit)
    if not row:
        raise not_found("track")
    return await fetch_one(conn, "SELECT * FROM v_tracks WHERE id = :id", id=track_id)


@router.patch("/sources/{source_id}")
async def update_source(source_id: int, body: SourceUpdate, conn: AsyncConnection = Depends(get_conn)) -> dict:
    """Tune Song<->Video sync (`sync_offset_ms`: video_pos = audio_pos + offset)
    or pin a source as the preferred rendition."""
    src = await fetch_one(conn, "SELECT id, track_id, kind FROM media_sources WHERE id = :id", id=source_id)
    if not src:
        raise not_found("media source")
    if body.sync_offset_ms is not None:
        await execute(conn, "UPDATE media_sources SET sync_offset_ms = :o WHERE id = :id",
                      id=source_id, o=body.sync_offset_ms)
    if body.is_preferred:
        await execute(conn, """UPDATE media_sources SET is_preferred = false
                                WHERE track_id = :t AND kind = :k AND is_preferred""",
                      t=src["track_id"], k=src["kind"])
        await execute(conn, "UPDATE media_sources SET is_preferred = true WHERE id = :id", id=source_id)
    return await fetch_one(conn, """
        SELECT id, track_id, kind, origin, sync_offset_ms, is_preferred FROM media_sources WHERE id = :id
    """, id=source_id)


# --- albums & artists ----------------------------------------------------------------------

_ALBUM_SELECT = """
    SELECT al.id, al.title, al.album_type, al.release_year, al.release_date, al.genre,
           al.cover_path, al.dominant_color, al.palette, al.created_at,
           ar.id AS artist_id, ar.name AS artist_name,
           (SELECT count(*) FROM tracks t WHERE t.album_id = al.id)::int AS track_count
      FROM albums al LEFT JOIN artists ar ON ar.id = al.album_artist_id
"""


@router.get("/albums")
async def list_albums(
    sort: Literal["recent", "title", "artist", "year"] = "recent",
    page: dict = Depends(paging),
    conn: AsyncConnection = Depends(get_conn),
) -> dict:
    order = {
        "recent": "al.created_at DESC, al.id DESC",
        "title": "lower(al.title), al.id",
        "artist": "lower(ar.name) NULLS LAST, al.release_year NULLS LAST, al.id",
        "year": "al.release_year DESC NULLS LAST, al.id",
    }[sort]
    where = "WHERE EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.in_library)"
    items = await fetch_all(conn, f"{_ALBUM_SELECT} {where} ORDER BY {order} LIMIT :limit OFFSET :offset", **page)
    total = await fetch_val(conn, f"SELECT count(*) FROM albums al {where}")
    return {"items": items, "total": total, **page}


@router.get("/albums/{album_id}")
async def get_album(album_id: int, conn: AsyncConnection = Depends(get_conn)) -> dict:
    album = await fetch_one(conn, f"{_ALBUM_SELECT} WHERE al.id = :id", id=album_id)
    if not album:
        raise not_found("album")
    album["tracks"] = await fetch_all(conn, """
        SELECT * FROM v_tracks WHERE album_id = :id
         ORDER BY disc_number, track_number NULLS LAST, lower(title)
    """, id=album_id)
    return album


@router.get("/artists")
async def list_artists(page: dict = Depends(paging), conn: AsyncConnection = Depends(get_conn)) -> dict:
    where = """WHERE EXISTS (SELECT 1 FROM tracks t WHERE t.primary_artist_id = a.id AND t.in_library)"""
    items = await fetch_all(conn, f"""
        SELECT a.id, a.name, a.image_path,
               (SELECT al.cover_path FROM albums al
                 WHERE al.album_artist_id = a.id AND al.cover_path IS NOT NULL
                 ORDER BY al.release_year DESC NULLS LAST LIMIT 1) AS cover_path,
               (SELECT count(*) FROM tracks t WHERE t.primary_artist_id = a.id)::int AS track_count
          FROM artists a {where}
         ORDER BY lower(coalesce(a.sort_name, a.name)) LIMIT :limit OFFSET :offset
    """, **page)
    total = await fetch_val(conn, f"SELECT count(*) FROM artists a {where}")
    return {"items": items, "total": total, **page}


@router.get("/artists/{artist_id}")
async def get_artist(artist_id: int, conn: AsyncConnection = Depends(get_conn)) -> dict:
    artist = await fetch_one(conn, "SELECT id, name, sort_name, image_path, bio FROM artists WHERE id = :id",
                             id=artist_id)
    if not artist:
        raise not_found("artist")
    artist["top_tracks"] = await fetch_all(conn, """
        SELECT * FROM v_tracks
         WHERE id IN (SELECT track_id FROM track_artists WHERE artist_id = :id)
         ORDER BY play_count DESC, last_played_at DESC NULLS LAST, id LIMIT 10
    """, id=artist_id)
    artist["albums"] = await fetch_all(conn, f"""
        {_ALBUM_SELECT} WHERE al.album_artist_id = :id
         ORDER BY al.release_year DESC NULLS LAST, lower(al.title)
    """, id=artist_id)
    artist["appears_on"] = await fetch_all(conn, f"""
        {_ALBUM_SELECT}
         WHERE al.id IN (SELECT t.album_id FROM tracks t JOIN track_artists ta ON ta.track_id = t.id
                          WHERE ta.artist_id = :id AND ta.role = 'featured')
           AND al.album_artist_id IS DISTINCT FROM :id
         ORDER BY al.release_year DESC NULLS LAST
    """, id=artist_id)
    return artist


# --- search ------------------------------------------------------------------------------------

@router.get("/search")
async def search(q: str = Query(min_length=1, max_length=200), limit: int = Query(default=20, le=100),
                 conn: AsyncConnection = Depends(get_conn)) -> dict:
    """Fuzzy, typo-tolerant search (pg_trgm) across tracks, albums and artists."""
    term = q.strip().lower()
    like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    params = dict(q=term, like=like, limit=limit)
    tracks = await fetch_all(conn, """
        SELECT v.*, greatest(similarity(lower(v.title), :q),
                             similarity(lower(coalesce(v.artist_name, '')), :q) * 0.8) AS score
          FROM v_tracks v
         WHERE v.in_library AND (lower(v.title) LIKE :like OR lower(v.title) % :q
            OR lower(coalesce(v.artist_name, '')) LIKE :like OR lower(coalesce(v.artist_name, '')) % :q)
         ORDER BY (lower(v.title) LIKE :q || '%') DESC, score DESC, v.play_count DESC
         LIMIT :limit
    """, **params)
    albums = await fetch_all(conn, f"""
        {_ALBUM_SELECT}
         WHERE (lower(al.title) LIKE :like OR lower(al.title) % :q)
           AND EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.in_library)
         ORDER BY (lower(al.title) LIKE :q || '%') DESC, similarity(lower(al.title), :q) DESC
         LIMIT :limit
    """, **params)
    artists = await fetch_all(conn, """
        SELECT a.id, a.name, a.image_path,
               (SELECT al.cover_path FROM albums al WHERE al.album_artist_id = a.id
                  AND al.cover_path IS NOT NULL LIMIT 1) AS cover_path
          FROM artists a
         WHERE (lower(a.name) LIKE :like OR lower(a.name) % :q)
           AND EXISTS (SELECT 1 FROM tracks t WHERE t.primary_artist_id = a.id AND t.in_library)
         ORDER BY (lower(a.name) LIKE :q || '%') DESC, similarity(lower(a.name), :q) DESC
         LIMIT :limit
    """, **params)
    top = None
    if tracks or artists:
        best_track = tracks[0]["score"] if tracks else 0
        best_artist = await fetch_val(conn, "SELECT similarity(lower(:n), :q)", n=artists[0]["name"], q=term) \
            if artists else 0
        top = ({"type": "artist", **artists[0]} if best_artist >= best_track
               else {"type": "track", **tracks[0]})
    return {"query": q, "top_result": top, "tracks": tracks, "albums": albums, "artists": artists}


# --- lyrics --------------------------------------------------------------------------------------

@router.get("/tracks/{track_id}/lyrics")
async def get_lyrics(track_id: int, conn: AsyncConnection = Depends(get_conn)) -> dict:
    row = await fetch_one(conn, "SELECT * FROM lyrics WHERE track_id = :id", id=track_id)
    if not row:
        raise not_found("lyrics")
    return row


@router.put("/tracks/{track_id}/lyrics")
async def put_lyrics(track_id: int, body: LyricsUpdate, conn: AsyncConnection = Depends(get_conn)) -> dict:
    """Save lyrics by hand: paste LRC (synced) and/or plain text, or just
    nudge `offset_ms` when existing synced lyrics drift."""
    if not await fetch_val(conn, "SELECT 1 FROM tracks WHERE id = :id", id=track_id):
        raise not_found("track")
    synced, plain = (parse_lrc(body.lrc) if body.lrc else (None, None))
    plain = body.plain_text or plain
    if synced or plain:
        await catalog.upsert_lyrics(conn, track_id, synced=synced or None, plain=plain,
                                    source="manual", overwrite_manual=True)
    exists = await fetch_val(conn, "SELECT 1 FROM lyrics WHERE track_id = :id", id=track_id)
    if not exists:
        raise HTTPException(status_code=400, detail="provide lrc or plain_text")
    if body.offset_ms is not None or body.language is not None:
        await execute(conn, """UPDATE lyrics SET offset_ms = COALESCE(:o, offset_ms),
                                                 language = COALESCE(:l, language)
                                WHERE track_id = :id""", id=track_id, o=body.offset_ms, l=body.language)
    return await fetch_one(conn, "SELECT * FROM lyrics WHERE track_id = :id", id=track_id)


@router.post("/tracks/{track_id}/lyrics/fetch")
async def fetch_lyrics(track_id: int, overwrite: bool = False, conn: AsyncConnection = Depends(get_conn),
                       http: httpx.AsyncClient = Depends(get_http)) -> dict:
    """Look up synced lyrics on LRCLIB (free public lyrics database)."""
    t = await fetch_one(conn, "SELECT title, artist_name, album_title, duration_ms FROM v_tracks WHERE id = :id",
                        id=track_id)
    if not t:
        raise not_found("track")
    if not t["artist_name"]:
        raise HTTPException(status_code=400, detail="track has no artist; cannot look up lyrics")
    found = await fetch_lrclib(http, title=t["title"], artist=t["artist_name"],
                               album=t["album_title"], duration_ms=t["duration_ms"])
    if not found:
        # Album/duration mismatches are common for YouTube imports: retry loosely.
        found = await fetch_lrclib(http, title=t["title"], artist=t["artist_name"])
    if not found:
        raise not_found("lyrics on LRCLIB")
    await catalog.upsert_lyrics(conn, track_id, synced=found["synced_lines"], plain=found["plain_text"],
                                source="remote", overwrite_manual=overwrite)
    row = await fetch_one(conn, "SELECT * FROM lyrics WHERE track_id = :id", id=track_id)
    if row and row["synced_lines"] is not None and not isinstance(row["synced_lines"], list):
        row["synced_lines"] = json.loads(row["synced_lines"])
    return row
