-- =====================================================================
-- WonderPlay :: 00_bootstrap.sql
-- Creates the application role + database. Run ONCE as a superuser:
--
--   psql -U postgres -v app_password='your-strong-password' -f db/00_bootstrap.sql
--
-- The password is passed as a psql variable so it never lives in the repo.
-- =====================================================================
\set ON_ERROR_STOP on

CREATE ROLE sonicvault LOGIN PASSWORD :'app_password';

CREATE DATABASE sonicvault
    OWNER     sonicvault
    ENCODING  'UTF8'
    TEMPLATE  template0;

\connect sonicvault

-- pg_trgm powers fuzzy "search as you type" across artists/albums/tracks.
CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;

-- Every connection from the app resolves unqualified names in "music" first.
ALTER ROLE sonicvault IN DATABASE sonicvault SET search_path = music, public;
