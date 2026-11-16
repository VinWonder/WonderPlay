-- =====================================================================
-- WonderPlay :: 99_reset.sql  —  DEVELOPMENT ONLY. Destroys all library data.
--   psql -U sonicvault -d sonicvault -f db/99_reset.sql
--   psql -U sonicvault -d sonicvault -f db/01_schema.sql
-- =====================================================================
\set ON_ERROR_STOP on
DROP SCHEMA IF EXISTS music CASCADE;
