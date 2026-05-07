#!/usr/bin/env bash
set -euo pipefail

echo "=== Data setup started ==="

DB_HOST="${DB_HOST:-db}"
DB_PORT="${DB_PORT:-5432}"
DB_NAME="${DB_NAME:-routing_db}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-postgres}"

export PGPASSWORD="$DB_PASSWORD"

PBF_PATH="/data/raw/pakistan-latest.osm.pbf"

echo "Waiting for PostgreSQL..."
until pg_isready -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME"; do
  sleep 2
done

echo "PostgreSQL is ready."

echo "Checking whether OSM import already exists..."
PLANET_LINE_EXISTS=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "
SELECT EXISTS (
  SELECT 1
  FROM information_schema.tables
  WHERE table_schema = 'public'
    AND table_name = 'planet_osm_line'
);
")

if [ "$PLANET_LINE_EXISTS" = "t" ]; then
  echo "OSM import tables already exist. Skipping osm2pgsql import."
else
  echo "OSM import tables not found. Running osm2pgsql import..."

  if [ ! -f "$PBF_PATH" ]; then
    echo "ERROR: PBF file not found at $PBF_PATH"
    exit 1
  fi

  osm2pgsql \
    --create \
    --slim \
    --database="$DB_NAME" \
    --username="$DB_USER" \
    --host="$DB_HOST" \
    --port="$DB_PORT" \
    --hstore \
    "$PBF_PATH"

  echo "OSM import completed."
fi

echo "Checking whether routing network already exists..."
ROADS_EXISTS=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "
SELECT EXISTS (
  SELECT 1
  FROM information_schema.tables
  WHERE table_schema = 'public'
    AND table_name = 'routing_roads'
);
")

if [ "$ROADS_EXISTS" = "t" ]; then
  ROADS_COUNT=$(psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tAc "
    SELECT COUNT(*) FROM routing_roads;
  " | xargs)

  if [ "$ROADS_COUNT" != "0" ]; then
    echo "Routing network already exists with $ROADS_COUNT rows. Skipping rebuild."
    echo "=== Data setup finished successfully ==="
    exit 0
  fi
fi

echo "Building routing network..."
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f /database/build/02_build_routing_network.sql
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -f /database/build/03_build_costs_and_topology.sql

echo "Validating routing network..."
psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "
SELECT COUNT(*) AS roads_count FROM routing_roads;
SELECT COUNT(*) AS vertices_count FROM routing_roads_vertices_pgr;
SELECT COUNT(*) AS missing_nodes
FROM routing_roads
WHERE source IS NULL OR target IS NULL;
"

echo "=== Data setup finished successfully ==="