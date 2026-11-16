"""Playlists: CRUD, add/remove/reorder items."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncConnection

from ..db import execute, fetch_all, fetch_one, fetch_val, get_conn
from ..deps import not_found
from ..schemas import PlaylistAdd, PlaylistCreate, PlaylistMove, PlaylistUpdate

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


async def _compact(conn: AsyncConnection, playlist_id: int) -> None:
    """Renumber positions to 0..n-1 (the unique constraint is deferred)."""
    await execute(conn, """
        WITH r AS (SELECT id, (row_number() OVER (ORDER BY position, id) - 1)::int AS pos
                     FROM playlist_tracks WHERE playlist_id = :p)
        UPDATE playlist_tracks pt SET position = r.pos FROM r
         WHERE pt.id = r.id AND pt.position <> r.pos
    """, p=playlist_id)


async def _append(conn: AsyncConnection, playlist_id: int, track_ids: list[int]) -> list[int]:
    missing = await fetch_val(conn, """
        SELECT array_agg(x) FROM unnest(CAST(:ids AS bigint[])) x
         WHERE NOT EXISTS (SELECT 1 FROM tracks t WHERE t.id = x)
    """, ids=track_ids)
    if missing:
        raise HTTPException(status_code=400, detail=f"unknown track ids: {missing}")
    rows = await fetch_all(conn, """
        INSERT INTO playlist_tracks (playlist_id, track_id, position)
        SELECT :p, t.id, (SELECT COALESCE(max(position) + 1, 0) FROM playlist_tracks WHERE playlist_id = :p)
                         + t.ord - 1
          FROM unnest(CAST(:ids AS bigint[])) WITH ORDINALITY AS t(id, ord)
        RETURNING id
    """, p=playlist_id, ids=track_ids)
    # Adding a catalogue song to a playlist saves it to Your Library (Spotify semantics).
    await execute(conn, "UPDATE tracks SET in_library = true WHERE id = ANY(CAST(:ids AS bigint[])) AND NOT in_library",
                  ids=track_ids)
    return [r["id"] for r in rows]


async def _lock(conn: AsyncConnection, playlist_id: int) -> None:
    if not await fetch_val(conn, "SELECT 1 FROM playlists WHERE id = :id FOR UPDATE", id=playlist_id):
        raise not_found("playlist")


@router.get("")
async def list_playlists(conn: AsyncConnection = Depends(get_conn)) -> list[dict]:
    rows = await fetch_all(conn, """
        SELECT v.*,
               ARRAY(SELECT DISTINCT al.cover_path
                       FROM playlist_tracks pt
                       JOIN tracks t  ON t.id = pt.track_id
                       JOIN albums al ON al.id = t.album_id
                      WHERE pt.playlist_id = v.id AND al.cover_path IS NOT NULL
                      LIMIT 4) AS mosaic
          FROM v_playlists v
         ORDER BY v.is_pinned DESC, v.updated_at DESC
    """)
    return rows


@router.post("", status_code=201)
async def create_playlist(body: PlaylistCreate, conn: AsyncConnection = Depends(get_conn)) -> dict:
    pid = await fetch_val(conn, "INSERT INTO playlists (name, description) VALUES (:n, :d) RETURNING id",
                          n=body.name.strip(), d=body.description)
    if body.track_ids:
        await _append(conn, pid, body.track_ids)
    return await fetch_one(conn, "SELECT * FROM v_playlists WHERE id = :id", id=pid)


@router.get("/{playlist_id}")
async def get_playlist(playlist_id: int, conn: AsyncConnection = Depends(get_conn)) -> dict:
    pl = await fetch_one(conn, "SELECT * FROM v_playlists WHERE id = :id", id=playlist_id)
    if not pl:
        raise not_found("playlist")
    pl["items"] = await fetch_all(conn, """
        SELECT pt.id AS item_id, pt.position, pt.added_at, v.*
          FROM playlist_tracks pt JOIN v_tracks v ON v.id = pt.track_id
         WHERE pt.playlist_id = :id
         ORDER BY pt.position
    """, id=playlist_id)
    return pl


@router.patch("/{playlist_id}")
async def update_playlist(playlist_id: int, body: PlaylistUpdate, conn: AsyncConnection = Depends(get_conn)) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="nothing to update")
    sets = ", ".join(f"{k} = :{k}" for k in fields)  # keys are whitelisted by the schema
    n = await execute(conn, f"UPDATE playlists SET {sets} WHERE id = :id", id=playlist_id, **fields)
    if not n:
        raise not_found("playlist")
    return await fetch_one(conn, "SELECT * FROM v_playlists WHERE id = :id", id=playlist_id)


@router.delete("/{playlist_id}", status_code=204)
async def delete_playlist(playlist_id: int, conn: AsyncConnection = Depends(get_conn)) -> None:
    if not await execute(conn, "DELETE FROM playlists WHERE id = :id", id=playlist_id):
        raise not_found("playlist")


@router.post("/{playlist_id}/tracks", status_code=201)
async def add_tracks(playlist_id: int, body: PlaylistAdd, conn: AsyncConnection = Depends(get_conn)) -> dict:
    await _lock(conn, playlist_id)
    item_ids = await _append(conn, playlist_id, body.track_ids)
    if body.position is not None:
        # Move the new block into place, last first so the order is preserved.
        for item_id in reversed(item_ids):
            await execute(conn, "SELECT playlist_move_item(:i, :p)", i=item_id, p=body.position)
    return {"added_item_ids": item_ids}


@router.delete("/{playlist_id}/items/{item_id}", status_code=204)
async def remove_item(playlist_id: int, item_id: int, conn: AsyncConnection = Depends(get_conn)) -> None:
    await _lock(conn, playlist_id)
    n = await execute(conn, "DELETE FROM playlist_tracks WHERE id = :i AND playlist_id = :p",
                      i=item_id, p=playlist_id)
    if not n:
        raise not_found("playlist item")
    await _compact(conn, playlist_id)


@router.post("/{playlist_id}/items/{item_id}/move")
async def move_item(playlist_id: int, item_id: int, body: PlaylistMove,
                    conn: AsyncConnection = Depends(get_conn)) -> dict:
    owner = await fetch_val(conn, "SELECT playlist_id FROM playlist_tracks WHERE id = :i", i=item_id)
    if owner != playlist_id:
        raise not_found("playlist item")
    await execute(conn, "SELECT playlist_move_item(:i, :p)", i=item_id, p=body.position)
    return {"item_id": item_id, "position": await fetch_val(
        conn, "SELECT position FROM playlist_tracks WHERE id = :i", i=item_id)}
