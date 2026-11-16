-- =====================================================================
-- WonderPlay :: 02_catalog.sql   (migration, run after 01_schema.sql)
--
--   psql -U sonicvault -d sonicvault -f db/02_catalog.sql
--
-- Spotify-style library semantics: songs played straight from the
-- YouTube Music catalogue are stored (so they can stream, appear in
-- history and be cached offline) but are NOT part of "Your Library"
-- until you like them or add them to a playlist.
-- Safe to run more than once.
-- =====================================================================
\set ON_ERROR_STOP on

BEGIN;
SET LOCAL search_path = music, public;

ALTER TABLE tracks ADD COLUMN IF NOT EXISTS in_library boolean NOT NULL DEFAULT true;
CREATE INDEX IF NOT EXISTS tracks_library_recent_idx ON tracks (created_at DESC) WHERE in_library;
CREATE INDEX IF NOT EXISTS media_sources_remote_lookup_idx ON media_sources (remote_id) WHERE origin = 'remote';

-- Same columns as before, plus in_library and youtube_id (appended: a view
-- may only gain columns at the end).
CREATE OR REPLACE VIEW v_tracks AS
SELECT t.id,
       t.title,
       t.duration_ms,
       t.disc_number,
       t.track_number,
       t.genre,
       t.release_year,
       t.explicit,
       t.is_favorite,
       t.favorited_at,
       t.rating,
       t.play_count,
       t.last_played_at,
       t.created_at,
       ar.id              AS artist_id,
       ar.name            AS artist_name,
       al.id              AS album_id,
       al.title           AS album_title,
       al.cover_path,
       al.dominant_color,
       EXISTS (SELECT 1 FROM media_sources ms
                WHERE ms.track_id = t.id AND ms.is_available AND ms.kind = 'audio') AS has_audio,
       EXISTS (SELECT 1 FROM media_sources ms
                WHERE ms.track_id = t.id AND ms.is_available AND ms.kind = 'video') AS has_video,
       EXISTS (SELECT 1 FROM lyrics ly
                WHERE ly.track_id = t.id AND ly.synced_lines IS NOT NULL)          AS has_synced_lyrics,
       t.in_library,
       (SELECT ms.remote_id FROM media_sources ms
         WHERE ms.track_id = t.id AND ms.origin = 'remote' AND ms.extractor = 'youtube'
         ORDER BY (ms.kind = 'audio') DESC, ms.id LIMIT 1)                       AS youtube_id
  FROM tracks t
  LEFT JOIN artists ar ON ar.id = t.primary_artist_id
  LEFT JOIN albums  al ON al.id = t.album_id;

COMMIT;
