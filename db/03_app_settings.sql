-- =====================================================================
-- WonderPlay :: 03_app_settings.sql   (migration)
-- Server-side settings edited from the app, e.g. the YouTube cookies that
-- let a cloud server pass YouTube's "confirm you're not a bot" check.
-- Safe to run more than once.
-- =====================================================================
\set ON_ERROR_STOP on

BEGIN;
SET LOCAL search_path = music, public;

CREATE TABLE IF NOT EXISTS app_settings (
    key         text        PRIMARY KEY,
    value       text        NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

COMMIT;
