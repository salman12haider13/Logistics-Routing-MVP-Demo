DROP TABLE IF EXISTS routing_roads_vertices_pgr CASCADE;

ALTER TABLE routing_roads
ADD COLUMN IF NOT EXISTS source BIGINT,
ADD COLUMN IF NOT EXISTS target BIGINT,
ADD COLUMN IF NOT EXISTS length_m DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS speed_kmh DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS travel_time_sec DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS cost_distance DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS cost_time DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS reverse_cost_distance DOUBLE PRECISION,
ADD COLUMN IF NOT EXISTS reverse_cost_time DOUBLE PRECISION;

UPDATE routing_roads
SET length_m = ST_Length(way);

UPDATE routing_roads
SET speed_kmh = CASE
    WHEN highway = 'motorway' THEN 90
    WHEN highway = 'trunk' THEN 80
    WHEN highway = 'primary' THEN 60
    WHEN highway = 'secondary' THEN 50
    WHEN highway = 'motorway_link' THEN 40
    WHEN highway = 'trunk_link' THEN 35
    WHEN highway = 'primary_link' THEN 30
    WHEN highway = 'secondary_link' THEN 25
    ELSE 30
END;

UPDATE routing_roads
SET travel_time_sec = CASE
    WHEN speed_kmh > 0 THEN length_m / (speed_kmh * 1000.0 / 3600.0)
    ELSE NULL
END;

UPDATE routing_roads
SET cost_distance = CASE
        WHEN oneway IN ('-1', 'reverse') THEN 1000000000
        ELSE length_m
    END,
    reverse_cost_distance = CASE
        WHEN oneway IN ('yes', '1', 'true') THEN 1000000000
        ELSE length_m
    END,
    cost_time = CASE
        WHEN oneway IN ('-1', 'reverse') THEN 1000000000
        ELSE travel_time_sec
    END,
    reverse_cost_time = CASE
        WHEN oneway IN ('yes', '1', 'true') THEN 1000000000
        ELSE travel_time_sec
    END;

SELECT pgr_createTopology(
    'routing_roads',
    0.001,
    'way',
    'id'
);

CREATE INDEX IF NOT EXISTS idx_routing_roads_source
ON routing_roads (source);

CREATE INDEX IF NOT EXISTS idx_routing_roads_target
ON routing_roads (target);

ANALYZE routing_roads;
ANALYZE routing_roads_vertices_pgr;