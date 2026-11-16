"""Catalog write helpers shared by the local scanner and the yt-dlp importer."""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy.ext.asyncio import AsyncConnection

from ..db import execute, fetch_one, fetch_val
from .covers import CoverInfo

VIDEO_CONTAINERS = ("mp4", "m4v", "webm", "mkv")


async def get_or_create_artist(conn: AsyncConnection, name: str) -> int:
    name = name.strip()[:500]
    sql_get = "SELECT id FROM artists WHERE lower(name) = lower(:name)"
    aid = await fetch_val(conn, sql_get, name=name)
    if aid:
        return aid
    aid = await fetch_val(
        conn,
        "INSERT INTO artists (name) VALUES (:name) ON CONFLICT DO NOTHING RETURNING id",
        name=name,
    )
    return aid or await fetch_val(conn, sql_get, name=name)


async def get_or_create_album(
    conn: AsyncConnection,
    *,
    title: str,
    album_artist_id: int | None,
    album_type: str = "album",
    release_year: int | None = None,
    release_date: str | None = None,
    genre: str | None = None,
    total_tracks: int | None = None,
    total_discs: int | None = None,
) -> int:
    title = title.strip()[:500]
    rd = None
    if release_date:
        try:
            rd = date.fromisoformat(release_date)
        except ValueError:
            rd = None
    params = dict(title=title, aid=album_artist_id, atype=album_type, year=release_year,
                  rdate=rd, genre=genre, tt=total_tracks, td=total_discs)
    sql_get = """SELECT id FROM albums
                  WHERE album_artist_id IS NOT DISTINCT FROM :aid AND lower(title) = lower(:title)"""
    album_id = await fetch_val(conn, sql_get, **params)
    if album_id:
        # Fill gaps only; never clobber what is already known.
        await execute(conn, """
            UPDATE albums SET
                release_year = COALESCE(release_year, :year),
                release_date = COALESCE(release_date, :rdate),
                genre        = COALESCE(genre, :genre),
                total_tracks = COALESCE(total_tracks, :tt),
                total_discs  = COALESCE(total_discs, :td)
             WHERE id = :id
               AND (   (release_year IS NULL AND CAST(:year AS smallint) IS NOT NULL)
                    OR (release_date IS NULL AND CAST(:rdate AS date) IS NOT NULL)
                    OR (genre        IS NULL AND CAST(:genre AS text) IS NOT NULL)
                    OR (total_tracks IS NULL AND CAST(:tt AS smallint) IS NOT NULL)
                    OR (total_discs  IS NULL AND CAST(:td AS smallint) IS NOT NULL))
        """, id=album_id, **{k: v for k, v in params.items() if k not in ("title", "aid", "atype")})
        return album_id
    album_id = await fetch_val(conn, """
        INSERT INTO albums (title, album_artist_id, album_type, release_year, release_date,
                            genre, total_tracks, total_discs)
        VALUES (:title, :aid, :atype, :year, :rdate, :genre, :tt, :td)
        ON CONFLICT DO NOTHING
        RETURNING id
    """, **params)
    return album_id or await fetch_val(conn, sql_get, **params)


async def set_album_cover(conn: AsyncConnection, album_id: int, cover: CoverInfo, *, replace: bool = False) -> None:
    await execute(conn, f"""
        UPDATE albums
           SET cover_path = :path, cover_hash = :hash,
               dominant_color = :color, palette = CAST(:palette AS jsonb)
         WHERE id = :id {"" if replace else "AND cover_path IS NULL"}
    """, id=album_id, path=cover.cover_path, hash=cover.cover_hash,
        color=cover.dominant_color, palette=json.dumps(cover.palette))


async def find_pairable_track(
    conn: AsyncConnection, *, title: str, artist_id: int | None, kind: str,
    album_id: int | None = None, exclude_origin: str | None = None,
) -> int | None:
    """Find an existing track this new rendition belongs to (Song <-> Video pairing).

    * video: same title + artist, and the track has no video yet.
    * audio: same title + artist, compatible album, and no *dedicated* audio
      file yet (audio extracted from a music-video file doesn't count).
    """
    if artist_id is None:
        return None
    if kind == "video":
        return await fetch_val(conn, """
            SELECT t.id FROM tracks t
             WHERE lower(t.title) = lower(:title) AND t.primary_artist_id = :aid
               AND NOT EXISTS (SELECT 1 FROM media_sources s
                                WHERE s.track_id = t.id AND s.kind = 'video' AND s.is_available)
             ORDER BY (t.album_id IS NOT NULL) DESC, t.id
             LIMIT 1
        """, title=title, aid=artist_id)
    return await fetch_val(conn, """
        SELECT t.id FROM tracks t
         WHERE lower(t.title) = lower(:title) AND t.primary_artist_id = :aid
           AND (t.album_id IS NOT DISTINCT FROM :album OR t.album_id IS NULL
                OR NOT EXISTS (SELECT 1 FROM media_sources s
                                WHERE s.track_id = t.id AND s.kind = 'audio' AND s.is_available
                                  AND s.origin = 'local'))
           AND NOT EXISTS (SELECT 1 FROM media_sources s
                            WHERE s.track_id = t.id AND s.kind = 'audio' AND s.is_available
                              AND COALESCE(s.container, '') <> ALL (CAST(:vc AS text[]))
                              AND (CAST(:excl AS source_origin) IS NULL OR s.origin = CAST(:excl AS source_origin)))
         ORDER BY t.id
         LIMIT 1
    """, title=title, aid=artist_id, album=album_id, vc=list(VIDEO_CONTAINERS), excl=exclude_origin)


async def create_track(conn: AsyncConnection, **f) -> int:
    return await fetch_val(conn, """
        INSERT INTO tracks (title, album_id, primary_artist_id, disc_number, track_number,
                            duration_ms, genre, release_year, isrc, bpm, explicit,
                            replaygain_track_db, replaygain_album_db)
        VALUES (:title, :album_id, :artist_id, :disc_number, :track_number,
                :duration_ms, :genre, :release_year, :isrc, :bpm, :explicit,
                :rg_track, :rg_album)
        RETURNING id
    """, **_track_params(f))


async def update_track(conn: AsyncConnection, track_id: int, **f) -> None:
    await execute(conn, """
        UPDATE tracks SET
            title = :title, album_id = :album_id, primary_artist_id = :artist_id,
            disc_number = :disc_number, track_number = :track_number,
            duration_ms = COALESCE(:duration_ms, duration_ms), genre = :genre,
            release_year = :release_year, isrc = :isrc, bpm = :bpm, explicit = :explicit,
            replaygain_track_db = :rg_track, replaygain_album_db = :rg_album
         WHERE id = :id
    """, id=track_id, **_track_params(f))


async def attach_album_if_missing(conn: AsyncConnection, track_id: int, album_id: int | None,
                                  duration_ms: int | None) -> None:
    await execute(conn, """
        UPDATE tracks SET album_id = COALESCE(album_id, :album),
                          duration_ms = COALESCE(duration_ms, :dur)
         WHERE id = :id AND (album_id IS NULL OR duration_ms IS NULL)
    """, id=track_id, album=album_id, dur=duration_ms)


def _track_params(f: dict) -> dict:
    return dict(
        title=f["title"][:1000], album_id=f.get("album_id"), artist_id=f.get("artist_id"),
        disc_number=f.get("disc_number") or 1, track_number=f.get("track_number"),
        duration_ms=f.get("duration_ms"), genre=f.get("genre"), release_year=f.get("release_year"),
        isrc=f.get("isrc"), bpm=f.get("bpm"), explicit=bool(f.get("explicit")),
        rg_track=f.get("replaygain_track_db"), rg_album=f.get("replaygain_album_db"),
    )


async def set_track_artists(conn: AsyncConnection, track_id: int, primary_id: int | None,
                            featured_ids: list[int]) -> None:
    await execute(conn, "DELETE FROM track_artists WHERE track_id = :t AND role IN ('primary','featured')",
                  t=track_id)
    rows = ([(primary_id, "primary", 0)] if primary_id else []) + \
           [(fid, "featured", i + 1) for i, fid in enumerate(featured_ids) if fid != primary_id]
    for aid, role, pos in rows:
        await execute(conn, """
            INSERT INTO track_artists (track_id, artist_id, role, position)
            VALUES (:t, :a, CAST(:r AS artist_role), :p) ON CONFLICT DO NOTHING
        """, t=track_id, a=aid, r=role, p=pos)


async def upsert_lyrics(conn: AsyncConnection, track_id: int, *, synced: list[dict] | None,
                        plain: str | None, source: str, overwrite_manual: bool = False) -> None:
    if not synced and not plain:
        return
    await execute(conn, f"""
        INSERT INTO lyrics (track_id, plain_text, synced_lines, source)
        VALUES (:t, :plain, CAST(:synced AS jsonb), :src)
        ON CONFLICT (track_id) DO UPDATE
           SET plain_text = EXCLUDED.plain_text,
               synced_lines = EXCLUDED.synced_lines,
               source = EXCLUDED.source
         WHERE {"true" if overwrite_manual else "lyrics.source <> 'manual'"}
    """, t=track_id, plain=plain, synced=json.dumps(synced) if synced else None, src=source)


async def refresh_preferred(conn: AsyncConnection, track_id: int) -> None:
    """Pick the default source per (track, kind):
       audio: local audio file > remote > audio inside a local video file, then bitrate.
       video: local > remote, then resolution."""
    await execute(conn, """
        UPDATE media_sources SET is_preferred = false
         WHERE track_id = :t AND is_preferred
    """, t=track_id)
    await execute(conn, """
        WITH ranked AS (
            SELECT id, row_number() OVER (
                       PARTITION BY kind
                       ORDER BY
                         CASE
                           WHEN kind = 'audio' AND origin = 'local'
                                AND COALESCE(container,'') <> ALL (CAST(:vc AS text[])) THEN 0
                           WHEN origin = 'local' AND kind = 'video'                     THEN 0
                           WHEN origin = 'remote'                                       THEN 1
                           ELSE 2
                         END,
                         CASE WHEN kind = 'video' THEN height ELSE bitrate_kbps END DESC NULLS LAST,
                         id) AS rn
              FROM media_sources
             WHERE track_id = :t AND is_available
        )
        UPDATE media_sources m SET is_preferred = true
          FROM ranked r WHERE m.id = r.id AND r.rn = 1
    """, t=track_id, vc=list(VIDEO_CONTAINERS))


async def get_track_brief(conn: AsyncConnection, track_id: int) -> dict | None:
    return await fetch_one(conn, "SELECT * FROM v_tracks WHERE id = :id", id=track_id)
