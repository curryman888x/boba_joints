-- Runs after the image's own 10_postgis.sh (dir is processed in sorted order).
-- The TIGER geocoder + topology extensions create ~40 tables in the `tiger` /
-- `topology` schemas and push those schemas onto search_path, which makes
-- Alembic autogenerate try to drop them. We don't use either -- drop them so the
-- schema is just `public` + PostGIS.
DROP EXTENSION IF EXISTS postgis_tiger_geocoder CASCADE;
DROP EXTENSION IF EXISTS postgis_topology CASCADE;
DROP SCHEMA IF EXISTS tiger CASCADE;
DROP SCHEMA IF EXISTS tiger_data CASCADE;
DROP SCHEMA IF EXISTS topology CASCADE;
ALTER DATABASE boba SET search_path TO "$user", public;
