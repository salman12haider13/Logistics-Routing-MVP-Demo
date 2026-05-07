DROP TABLE IF EXISTS routing_roads CASCADE;
DROP TABLE IF EXISTS routing_roads_raw CASCADE;

CREATE TABLE routing_roads_raw AS
SELECT
    ROW_NUMBER() OVER () AS raw_id,
    osm_id,
    name,
    highway,
    oneway,
    bridge,
    tunnel,
    COALESCE(layer, '0') AS layer,
    CONCAT_WS(
        '|',
        COALESCE(layer, '0'),
        CASE
            WHEN COALESCE(bridge, 'no') IN ('yes', '1', 'true') THEN 'bridge'
            ELSE 'no_bridge'
        END,
        CASE
            WHEN COALESCE(tunnel, 'no') IN ('yes', '1', 'true') THEN 'tunnel'
            ELSE 'no_tunnel'
        END
    ) AS level_key,
    way
FROM planet_osm_line
WHERE highway IS NOT NULL
  AND highway IN (
      'motorway',
      'trunk',
      'primary',
      'secondary',
      'motorway_link',
      'trunk_link',
      'primary_link',
      'secondary_link'
  );

ALTER TABLE routing_roads_raw
ADD PRIMARY KEY (raw_id);

CREATE INDEX idx_routing_roads_raw_way
ON routing_roads_raw
USING GIST (way);

CREATE INDEX idx_routing_roads_raw_level_key
ON routing_roads_raw (level_key);

ANALYZE routing_roads_raw;

CREATE TABLE routing_roads AS
WITH noded AS (
    SELECT
        level_key,
        (ST_Dump(ST_Node(ST_UnaryUnion(ST_Collect(way))))).geom AS geom
    FROM routing_roads_raw
    GROUP BY level_key
),
segments AS (
    SELECT
        ROW_NUMBER() OVER () AS id,
        level_key,
        geom AS way
    FROM noded
    WHERE GeometryType(geom) = 'LINESTRING'
      AND ST_Length(geom) > 1.0
)
SELECT
    s.id,
    r.osm_id,
    r.name,
    r.highway,
    r.oneway,
    r.bridge,
    r.tunnel,
    r.layer,
    s.way
FROM segments s
CROSS JOIN LATERAL (
    SELECT
        osm_id,
        name,
        highway,
        oneway,
        bridge,
        tunnel,
        layer
    FROM routing_roads_raw r
    WHERE r.level_key = s.level_key
      AND ST_DWithin(r.way, s.way, 1.0)
    ORDER BY
        ST_Length(ST_Intersection(r.way, s.way)) DESC NULLS LAST,
        r.way <-> ST_LineInterpolatePoint(s.way, 0.5)
    LIMIT 1
) r;

ALTER TABLE routing_roads
ADD PRIMARY KEY (id);

CREATE INDEX idx_routing_roads_way
ON routing_roads
USING GIST (way);

ANALYZE routing_roads;