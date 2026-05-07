import json

from app.db import get_db_connection

BLOCKED = 1000000000.0
TRAFFIC_DELAY_PER_POINT_SEC = 90.0

START_NODE_ID = 9000000001
END_NODE_ID = 9000000002

DYNAMIC_NODE_BASE = 9200000000
DYNAMIC_EDGE_BASE = 9300000000

ROUTING_EDGE_TABLE = "routing_roads"


class RoutingService:
    @staticmethod
    def normalize_oneway(oneway_value):
        value = (oneway_value or "").strip().lower()

        if value in ("yes", "1", "true"):
            return "forward"

        if value in ("-1", "reverse"):
            return "reverse"

        return "both"

    @staticmethod
    def get_nearest_edge(lon, lat):
        sql = f"""
            WITH input_point AS (
                SELECT ST_Transform(
                    ST_SetSRID(ST_MakePoint(%s, %s), 4326),
                    3857
                ) AS geom
            ),
            edge_pick AS (
                SELECT
                    id,
                    source,
                    target,
                    oneway,
                    length_m,
                    travel_time_sec,
                    ST_LineLocatePoint(way, (SELECT geom FROM input_point)) AS raw_fraction,
                    ST_Distance(way, (SELECT geom FROM input_point)) AS snap_distance_m,
                    ST_LineInterpolatePoint(
                        way,
                        ST_LineLocatePoint(way, (SELECT geom FROM input_point))
                    ) AS snapped_geom,
                    way
                FROM {ROUTING_EDGE_TABLE}
                ORDER BY way <-> (SELECT geom FROM input_point)
                LIMIT 1
            )
            SELECT
                id,
                source,
                target,
                oneway,
                length_m,
                travel_time_sec,
                LEAST(GREATEST(raw_fraction, 0.000001), 0.999999) AS fraction,
                snap_distance_m,
                ST_AsText(way) AS way_wkt,
                ST_X(snapped_geom) AS snapped_x,
                ST_Y(snapped_geom) AS snapped_y
            FROM edge_pick;
        """

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (lon, lat))
                return cur.fetchone()
        finally:
            conn.close()

    @staticmethod
    def get_blocked_intervals_for_polygon(polygon_geometry):
        polygon_geojson = json.dumps(polygon_geometry)

        sql = f"""
            WITH polygon_geom AS (
                SELECT ST_Transform(
                    ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),
                    3857
                ) AS geom
            ),
            raw_intersections AS (
                SELECT
                    r.id,
                    r.source,
                    r.target,
                    r.oneway,
                    r.length_m,
                    r.travel_time_sec,
                    ST_AsText(r.way) AS way_wkt,
                    (ST_Dump(ST_Intersection(r.way, (SELECT geom FROM polygon_geom)))).geom AS inter_geom,
                    r.way
                FROM {ROUTING_EDGE_TABLE} r
                WHERE ST_Intersects(r.way, (SELECT geom FROM polygon_geom))
            ),
            line_parts AS (
                SELECT
                    id,
                    source,
                    target,
                    oneway,
                    length_m,
                    travel_time_sec,
                    way_wkt,
                    way,
                    inter_geom
                FROM raw_intersections
                WHERE GeometryType(inter_geom) = 'LINESTRING'
                  AND ST_Length(inter_geom) > 0
            )
            SELECT
                id,
                source,
                target,
                oneway,
                length_m,
                travel_time_sec,
                way_wkt,
                LEAST(
                    ST_LineLocatePoint(way, ST_StartPoint(inter_geom)),
                    ST_LineLocatePoint(way, ST_EndPoint(inter_geom))
                ) AS start_fraction,
                GREATEST(
                    ST_LineLocatePoint(way, ST_StartPoint(inter_geom)),
                    ST_LineLocatePoint(way, ST_EndPoint(inter_geom))
                ) AS end_fraction
            FROM line_parts
            ORDER BY id, start_fraction, end_fraction;
        """

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, (polygon_geojson,))
                return cur.fetchall()
        finally:
            conn.close()

    @staticmethod
    def merge_fraction_intervals(intervals):
        if not intervals:
            return []

        cleaned = []
        for interval in intervals:
            start_fraction = max(0.0, min(1.0, float(interval["start_fraction"])))
            end_fraction = max(0.0, min(1.0, float(interval["end_fraction"])))

            if end_fraction < start_fraction:
                start_fraction, end_fraction = end_fraction, start_fraction

            if end_fraction - start_fraction <= 0.0000001:
                continue

            cleaned.append({
                "start_fraction": start_fraction,
                "end_fraction": end_fraction,
            })

        if not cleaned:
            return []

        cleaned.sort(key=lambda item: (item["start_fraction"], item["end_fraction"]))
        merged = [cleaned[0]]

        for current in cleaned[1:]:
            last = merged[-1]
            if current["start_fraction"] <= last["end_fraction"] + 0.000001:
                last["end_fraction"] = max(last["end_fraction"], current["end_fraction"])
            else:
                merged.append(current)

        return merged

    @staticmethod
    def make_geom_sql(way_wkt, start_fraction, end_fraction):
        start_fraction = max(0.0, min(1.0, float(start_fraction)))
        end_fraction = max(0.0, min(1.0, float(end_fraction)))

        if end_fraction < start_fraction:
            start_fraction, end_fraction = end_fraction, start_fraction

        return (
            f"ST_LineSubstring("
            f"ST_GeomFromText('{way_wkt}', 3857), "
            f"{start_fraction}, "
            f"{end_fraction}"
            f")"
        )

    @staticmethod
    def build_segment_costs(oneway_mode, distance_m, time_sec, traffic_penalty_sec=0.0):
        distance_m = float(distance_m)
        time_sec = float(time_sec)
        traffic_penalty_sec = float(traffic_penalty_sec)

        adjusted_time = time_sec + traffic_penalty_sec

        if oneway_mode == "forward":
            return {
                "cost_distance": distance_m,
                "reverse_cost_distance": BLOCKED,
                "cost_time": adjusted_time,
                "reverse_cost_time": BLOCKED,
            }

        if oneway_mode == "reverse":
            return {
                "cost_distance": BLOCKED,
                "reverse_cost_distance": distance_m,
                "cost_time": BLOCKED,
                "reverse_cost_time": adjusted_time,
            }

        return {
            "cost_distance": distance_m,
            "reverse_cost_distance": distance_m,
            "cost_time": adjusted_time,
            "reverse_cost_time": adjusted_time,
        }

    @staticmethod
    def build_split_info_for_edge(
        edge,
        blocked_intervals,
        next_node_id,
        next_edge_id,
        traffic_count=0,
        force_dynamic=False,
    ):
        oneway_mode = RoutingService.normalize_oneway(edge["oneway"])
        length_m = float(edge["length_m"])
        travel_time_sec = float(edge["travel_time_sec"])
        way_wkt = edge["way_wkt"]

        merged_intervals = RoutingService.merge_fraction_intervals(blocked_intervals)
        total_traffic_penalty_sec = float(traffic_count) * TRAFFIC_DELAY_PER_POINT_SEC

        barriers = []
        segments = []

        if not merged_intervals:
            if force_dynamic:
                segment_costs = RoutingService.build_segment_costs(
                    oneway_mode=oneway_mode,
                    distance_m=length_m,
                    time_sec=travel_time_sec,
                    traffic_penalty_sec=total_traffic_penalty_sec,
                )

                segments.append({
                    "id": next_edge_id,
                    "source": int(edge["source"]),
                    "target": int(edge["target"]),
                    "geom_sql": RoutingService.make_geom_sql(
                        way_wkt=way_wkt,
                        start_fraction=0.0,
                        end_fraction=1.0,
                    ),
                    **segment_costs,
                })
                next_edge_id += 1

            return {
                "edge_id": int(edge["id"]),
                "source": int(edge["source"]),
                "target": int(edge["target"]),
                "oneway_mode": oneway_mode,
                "length_m": length_m,
                "travel_time_sec": travel_time_sec,
                "way_wkt": way_wkt,
                "barriers": [],
                "segments": segments,
                "total_traffic_penalty_sec": total_traffic_penalty_sec,
                "next_node_id": next_node_id,
                "next_edge_id": next_edge_id,
            }

        prev_fraction = 0.0
        prev_node = int(edge["source"])

        for blocked_interval in merged_intervals:
            blocked_start = blocked_interval["start_fraction"]
            blocked_end = blocked_interval["end_fraction"]

            left_node_id = next_node_id
            next_node_id += 1

            right_node_id = next_node_id
            next_node_id += 1

            barriers.append({
                "start_fraction": blocked_start,
                "end_fraction": blocked_end,
                "left_node_id": left_node_id,
                "right_node_id": right_node_id,
            })

            safe_fraction = blocked_start - prev_fraction
            safe_segment_distance = length_m * safe_fraction
            safe_segment_time = travel_time_sec * safe_fraction
            safe_segment_penalty = total_traffic_penalty_sec * safe_fraction

            if safe_segment_distance > 0 and safe_segment_time >= 0:
                segment_costs = RoutingService.build_segment_costs(
                    oneway_mode=oneway_mode,
                    distance_m=safe_segment_distance,
                    time_sec=safe_segment_time,
                    traffic_penalty_sec=safe_segment_penalty,
                )

                segments.append({
                    "id": next_edge_id,
                    "source": prev_node,
                    "target": left_node_id,
                    "geom_sql": RoutingService.make_geom_sql(
                        way_wkt=way_wkt,
                        start_fraction=prev_fraction,
                        end_fraction=blocked_start,
                    ),
                    **segment_costs,
                })
                next_edge_id += 1

            prev_fraction = blocked_end
            prev_node = right_node_id

        final_fraction = 1.0 - prev_fraction
        final_segment_distance = length_m * final_fraction
        final_segment_time = travel_time_sec * final_fraction
        final_segment_penalty = total_traffic_penalty_sec * final_fraction

        if final_segment_distance > 0 and final_segment_time >= 0:
            final_segment_costs = RoutingService.build_segment_costs(
                oneway_mode=oneway_mode,
                distance_m=final_segment_distance,
                time_sec=final_segment_time,
                traffic_penalty_sec=final_segment_penalty,
            )

            segments.append({
                "id": next_edge_id,
                "source": prev_node,
                "target": int(edge["target"]),
                "geom_sql": RoutingService.make_geom_sql(
                    way_wkt=way_wkt,
                    start_fraction=prev_fraction,
                    end_fraction=1.0,
                ),
                **final_segment_costs,
            })
            next_edge_id += 1

        return {
            "edge_id": int(edge["id"]),
            "source": int(edge["source"]),
            "target": int(edge["target"]),
            "oneway_mode": oneway_mode,
            "length_m": length_m,
            "travel_time_sec": travel_time_sec,
            "way_wkt": way_wkt,
            "barriers": barriers,
            "segments": segments,
            "total_traffic_penalty_sec": total_traffic_penalty_sec,
            "next_node_id": next_node_id,
            "next_edge_id": next_edge_id,
        }

    @staticmethod
    def find_safe_interval_for_fraction(split_info, fraction):
        fraction = max(0.000001, min(0.999999, float(fraction)))
        barriers = split_info["barriers"]

        if not barriers:
            return {
                "left_node_id": int(split_info["source"]),
                "right_node_id": int(split_info["target"]),
                "left_fraction": 0.0,
                "right_fraction": 1.0,
            }

        first_barrier = barriers[0]

        if fraction < first_barrier["start_fraction"]:
            return {
                "left_node_id": int(split_info["source"]),
                "right_node_id": int(first_barrier["left_node_id"]),
                "left_fraction": 0.0,
                "right_fraction": float(first_barrier["start_fraction"]),
            }

        for barrier in barriers:
            if barrier["start_fraction"] <= fraction <= barrier["end_fraction"]:
                return None

        for index in range(len(barriers) - 1):
            current_barrier = barriers[index]
            next_barrier = barriers[index + 1]

            if current_barrier["end_fraction"] < fraction < next_barrier["start_fraction"]:
                return {
                    "left_node_id": int(current_barrier["right_node_id"]),
                    "right_node_id": int(next_barrier["left_node_id"]),
                    "left_fraction": float(current_barrier["end_fraction"]),
                    "right_fraction": float(next_barrier["start_fraction"]),
                }

        last_barrier = barriers[-1]

        if fraction > last_barrier["end_fraction"]:
            return {
                "left_node_id": int(last_barrier["right_node_id"]),
                "right_node_id": int(split_info["target"]),
                "left_fraction": float(last_barrier["end_fraction"]),
                "right_fraction": 1.0,
            }

        return None

    @staticmethod
    def build_start_connectors(edge, split_info, start_fraction, next_edge_id):
        interval = RoutingService.find_safe_interval_for_fraction(split_info, start_fraction)

        if interval is None:
            raise ValueError("Start point lies inside a blocked zone on the selected road.")

        oneway_mode = split_info["oneway_mode"]
        way_wkt = split_info["way_wkt"]
        length_m = split_info["length_m"]
        travel_time_sec = split_info["travel_time_sec"]
        total_traffic_penalty_sec = split_info.get("total_traffic_penalty_sec", 0.0)

        left_fraction = interval["left_fraction"]
        right_fraction = interval["right_fraction"]
        left_node_id = interval["left_node_id"]
        right_node_id = interval["right_node_id"]

        left_fraction_size = start_fraction - left_fraction
        right_fraction_size = right_fraction - start_fraction

        dist_to_left = length_m * left_fraction_size
        dist_to_right = length_m * right_fraction_size

        time_to_left = travel_time_sec * left_fraction_size
        time_to_right = travel_time_sec * right_fraction_size

        penalty_to_left = total_traffic_penalty_sec * left_fraction_size
        penalty_to_right = total_traffic_penalty_sec * right_fraction_size

        connectors = []
        connector_edge_ids = []

        if oneway_mode == "forward":
            edge_data = {
                "id": next_edge_id,
                "source": START_NODE_ID,
                "target": right_node_id,
                "geom_sql": RoutingService.make_geom_sql(
                    way_wkt=way_wkt,
                    start_fraction=start_fraction,
                    end_fraction=right_fraction,
                ),
                "cost_distance": dist_to_right,
                "reverse_cost_distance": BLOCKED,
                "cost_time": time_to_right + penalty_to_right,
                "reverse_cost_time": BLOCKED,
            }
            connectors.append(edge_data)
            connector_edge_ids.append(next_edge_id)
            next_edge_id += 1

        elif oneway_mode == "reverse":
            edge_data = {
                "id": next_edge_id,
                "source": START_NODE_ID,
                "target": left_node_id,
                "geom_sql": RoutingService.make_geom_sql(
                    way_wkt=way_wkt,
                    start_fraction=left_fraction,
                    end_fraction=start_fraction,
                ),
                "cost_distance": dist_to_left,
                "reverse_cost_distance": BLOCKED,
                "cost_time": time_to_left + penalty_to_left,
                "reverse_cost_time": BLOCKED,
            }
            connectors.append(edge_data)
            connector_edge_ids.append(next_edge_id)
            next_edge_id += 1

        else:
            left_edge = {
                "id": next_edge_id,
                "source": START_NODE_ID,
                "target": left_node_id,
                "geom_sql": RoutingService.make_geom_sql(
                    way_wkt=way_wkt,
                    start_fraction=left_fraction,
                    end_fraction=start_fraction,
                ),
                "cost_distance": dist_to_left,
                "reverse_cost_distance": BLOCKED,
                "cost_time": time_to_left + penalty_to_left,
                "reverse_cost_time": BLOCKED,
            }
            connectors.append(left_edge)
            connector_edge_ids.append(next_edge_id)
            next_edge_id += 1

            right_edge = {
                "id": next_edge_id,
                "source": START_NODE_ID,
                "target": right_node_id,
                "geom_sql": RoutingService.make_geom_sql(
                    way_wkt=way_wkt,
                    start_fraction=start_fraction,
                    end_fraction=right_fraction,
                ),
                "cost_distance": dist_to_right,
                "reverse_cost_distance": BLOCKED,
                "cost_time": time_to_right + penalty_to_right,
                "reverse_cost_time": BLOCKED,
            }
            connectors.append(right_edge)
            connector_edge_ids.append(next_edge_id)
            next_edge_id += 1

        return connectors, connector_edge_ids, next_edge_id

    @staticmethod
    def build_end_connectors(edge, split_info, end_fraction, next_edge_id):
        interval = RoutingService.find_safe_interval_for_fraction(split_info, end_fraction)

        if interval is None:
            raise ValueError("End point lies inside a blocked zone on the selected road.")

        oneway_mode = split_info["oneway_mode"]
        way_wkt = split_info["way_wkt"]
        length_m = split_info["length_m"]
        travel_time_sec = split_info["travel_time_sec"]
        total_traffic_penalty_sec = split_info.get("total_traffic_penalty_sec", 0.0)

        left_fraction = interval["left_fraction"]
        right_fraction = interval["right_fraction"]
        left_node_id = interval["left_node_id"]
        right_node_id = interval["right_node_id"]

        left_fraction_size = end_fraction - left_fraction
        right_fraction_size = right_fraction - end_fraction

        dist_from_left = length_m * left_fraction_size
        dist_from_right = length_m * right_fraction_size

        time_from_left = travel_time_sec * left_fraction_size
        time_from_right = travel_time_sec * right_fraction_size

        penalty_from_left = total_traffic_penalty_sec * left_fraction_size
        penalty_from_right = total_traffic_penalty_sec * right_fraction_size

        connectors = []
        connector_edge_ids = []

        if oneway_mode == "forward":
            edge_data = {
                "id": next_edge_id,
                "source": left_node_id,
                "target": END_NODE_ID,
                "geom_sql": RoutingService.make_geom_sql(
                    way_wkt=way_wkt,
                    start_fraction=left_fraction,
                    end_fraction=end_fraction,
                ),
                "cost_distance": dist_from_left,
                "reverse_cost_distance": BLOCKED,
                "cost_time": time_from_left + penalty_from_left,
                "reverse_cost_time": BLOCKED,
            }
            connectors.append(edge_data)
            connector_edge_ids.append(next_edge_id)
            next_edge_id += 1

        elif oneway_mode == "reverse":
            edge_data = {
                "id": next_edge_id,
                "source": right_node_id,
                "target": END_NODE_ID,
                "geom_sql": RoutingService.make_geom_sql(
                    way_wkt=way_wkt,
                    start_fraction=end_fraction,
                    end_fraction=right_fraction,
                ),
                "cost_distance": dist_from_right,
                "reverse_cost_distance": BLOCKED,
                "cost_time": time_from_right + penalty_from_right,
                "reverse_cost_time": BLOCKED,
            }
            connectors.append(edge_data)
            connector_edge_ids.append(next_edge_id)
            next_edge_id += 1

        else:
            left_edge = {
                "id": next_edge_id,
                "source": left_node_id,
                "target": END_NODE_ID,
                "geom_sql": RoutingService.make_geom_sql(
                    way_wkt=way_wkt,
                    start_fraction=left_fraction,
                    end_fraction=end_fraction,
                ),
                "cost_distance": dist_from_left,
                "reverse_cost_distance": BLOCKED,
                "cost_time": time_from_left + penalty_from_left,
                "reverse_cost_time": BLOCKED,
            }
            connectors.append(left_edge)
            connector_edge_ids.append(next_edge_id)
            next_edge_id += 1

            right_edge = {
                "id": next_edge_id,
                "source": right_node_id,
                "target": END_NODE_ID,
                "geom_sql": RoutingService.make_geom_sql(
                    way_wkt=way_wkt,
                    start_fraction=end_fraction,
                    end_fraction=right_fraction,
                ),
                "cost_distance": dist_from_right,
                "reverse_cost_distance": BLOCKED,
                "cost_time": time_from_right + penalty_from_right,
                "reverse_cost_time": BLOCKED,
            }
            connectors.append(right_edge)
            connector_edge_ids.append(next_edge_id)
            next_edge_id += 1

        return connectors, connector_edge_ids, next_edge_id

    @staticmethod
    def edge_dict_to_sql_with_geom(edge):
        return f"""
            SELECT
                {int(edge['id'])}::bigint AS id,
                {int(edge['source'])}::bigint AS source,
                {int(edge['target'])}::bigint AS target,
                {float(edge['cost_distance'])}::double precision AS cost_distance,
                {float(edge['cost_time'])}::double precision AS cost_time,
                {float(edge['reverse_cost_distance'])}::double precision AS reverse_cost_distance,
                {float(edge['reverse_cost_time'])}::double precision AS reverse_cost_time,
                {edge['geom_sql']} AS geom
        """

    @staticmethod
    def edge_dict_to_sql_for_pgr(edge):
        return f"""
            SELECT
                {int(edge['id'])}::bigint AS id,
                {int(edge['source'])}::bigint AS source,
                {int(edge['target'])}::bigint AS target,
                {float(edge['cost_distance'])}::double precision AS cost_distance,
                {float(edge['cost_time'])}::double precision AS cost_time,
                {float(edge['reverse_cost_distance'])}::double precision AS reverse_cost_distance,
                {float(edge['reverse_cost_time'])}::double precision AS reverse_cost_time
        """

    @staticmethod
    def build_base_edges_sql_with_geom(excluded_edge_ids):
        if excluded_edge_ids:
            excluded_ids_sql = ", ".join(str(int(edge_id)) for edge_id in sorted(excluded_edge_ids))
            where_clause = f"WHERE id NOT IN ({excluded_ids_sql})"
        else:
            where_clause = ""

        return f"""
            SELECT
                id::bigint AS id,
                source::bigint AS source,
                target::bigint AS target,
                cost_distance::double precision AS cost_distance,
                cost_time::double precision AS cost_time,
                reverse_cost_distance::double precision AS reverse_cost_distance,
                reverse_cost_time::double precision AS reverse_cost_time,
                way AS geom
            FROM {ROUTING_EDGE_TABLE}
            {where_clause}
        """

    @staticmethod
    def build_base_edges_sql_for_pgr(excluded_edge_ids):
        if excluded_edge_ids:
            excluded_ids_sql = ", ".join(str(int(edge_id)) for edge_id in sorted(excluded_edge_ids))
            where_clause = f"WHERE id NOT IN ({excluded_ids_sql})"
        else:
            where_clause = ""

        return f"""
            SELECT
                id::bigint AS id,
                source::bigint AS source,
                target::bigint AS target,
                cost_distance::double precision AS cost_distance,
                cost_time::double precision AS cost_time,
                reverse_cost_distance::double precision AS reverse_cost_distance,
                reverse_cost_time::double precision AS reverse_cost_time
            FROM {ROUTING_EDGE_TABLE}
            {where_clause}
        """

    @staticmethod
    def build_graph_context(
        start_lon,
        start_lat,
        end_lon,
        end_lat,
        blocked_polygons=None,
        traffic_points=None,
    ):
        blocked_polygons = blocked_polygons or []
        traffic_points = traffic_points or []

        start_edge = RoutingService.get_nearest_edge(start_lon, start_lat)
        end_edge = RoutingService.get_nearest_edge(end_lon, end_lat)

        if not start_edge or not end_edge:
            raise ValueError("Could not find nearby routing roads.")

        edge_groups = {}
        snapped_traffic_points = []
        traffic_counts_by_edge_id = {}

        for polygon in blocked_polygons:
            polygon_intervals = RoutingService.get_blocked_intervals_for_polygon(polygon)

            for row in polygon_intervals:
                edge_id = int(row["id"])

                edge_groups.setdefault(edge_id, {
                    "edge": {
                        "id": int(row["id"]),
                        "source": int(row["source"]),
                        "target": int(row["target"]),
                        "oneway": row["oneway"],
                        "length_m": float(row["length_m"]),
                        "travel_time_sec": float(row["travel_time_sec"]),
                        "way_wkt": row["way_wkt"],
                    },
                    "blocked_intervals": [],
                })

                edge_groups[edge_id]["blocked_intervals"].append({
                    "start_fraction": float(row["start_fraction"]),
                    "end_fraction": float(row["end_fraction"]),
                })

        for point in traffic_points:
            snapped_edge = RoutingService.get_nearest_edge(point["lon"], point["lat"])

            if not snapped_edge:
                continue

            edge_id = int(snapped_edge["id"])
            traffic_counts_by_edge_id[edge_id] = traffic_counts_by_edge_id.get(edge_id, 0) + 1

            edge_groups.setdefault(edge_id, {
                "edge": {
                    "id": int(snapped_edge["id"]),
                    "source": int(snapped_edge["source"]),
                    "target": int(snapped_edge["target"]),
                    "oneway": snapped_edge["oneway"],
                    "length_m": float(snapped_edge["length_m"]),
                    "travel_time_sec": float(snapped_edge["travel_time_sec"]),
                    "way_wkt": snapped_edge["way_wkt"],
                },
                "blocked_intervals": [],
            })

            snapped_traffic_points.append({
                "input_lat": float(point["lat"]),
                "input_lon": float(point["lon"]),
                "nearest_edge_id": edge_id,
                "fraction": float(snapped_edge["fraction"]),
                "snap_distance_m": float(snapped_edge["snap_distance_m"]),
                "snapped_x": float(snapped_edge["snapped_x"]) if snapped_edge["snapped_x"] is not None else None,
                "snapped_y": float(snapped_edge["snapped_y"]) if snapped_edge["snapped_y"] is not None else None,
            })

        next_node_id = DYNAMIC_NODE_BASE
        next_edge_id = DYNAMIC_EDGE_BASE

        split_info_by_edge_id = {}
        dynamic_edges = []
        excluded_edge_ids = set()

        for edge_id, group in edge_groups.items():
            merged_intervals = RoutingService.merge_fraction_intervals(group["blocked_intervals"])
            traffic_count = traffic_counts_by_edge_id.get(edge_id, 0)

            force_dynamic = bool(merged_intervals) or traffic_count > 0

            split_info = RoutingService.build_split_info_for_edge(
                edge=group["edge"],
                blocked_intervals=merged_intervals,
                next_node_id=next_node_id,
                next_edge_id=next_edge_id,
                traffic_count=traffic_count,
                force_dynamic=force_dynamic,
            )

            next_node_id = split_info["next_node_id"]
            next_edge_id = split_info["next_edge_id"]

            split_info_by_edge_id[edge_id] = split_info

            if force_dynamic:
                excluded_edge_ids.add(edge_id)
                dynamic_edges.extend(split_info["segments"])

        start_edge_id = int(start_edge["id"])
        if start_edge_id not in split_info_by_edge_id:
            split_info_by_edge_id[start_edge_id] = RoutingService.build_split_info_for_edge(
                edge=start_edge,
                blocked_intervals=[],
                next_node_id=next_node_id,
                next_edge_id=next_edge_id,
                traffic_count=traffic_counts_by_edge_id.get(start_edge_id, 0),
                force_dynamic=False,
            )

        end_edge_id = int(end_edge["id"])
        if end_edge_id not in split_info_by_edge_id:
            split_info_by_edge_id[end_edge_id] = RoutingService.build_split_info_for_edge(
                edge=end_edge,
                blocked_intervals=[],
                next_node_id=next_node_id,
                next_edge_id=next_edge_id,
                traffic_count=traffic_counts_by_edge_id.get(end_edge_id, 0),
                force_dynamic=False,
            )

        start_split_info = split_info_by_edge_id[start_edge_id]
        end_split_info = split_info_by_edge_id[end_edge_id]

        start_connectors, start_connector_edge_ids, next_edge_id = RoutingService.build_start_connectors(
            edge=start_edge,
            split_info=start_split_info,
            start_fraction=float(start_edge["fraction"]),
            next_edge_id=next_edge_id,
        )

        end_connectors, end_connector_edge_ids, next_edge_id = RoutingService.build_end_connectors(
            edge=end_edge,
            split_info=end_split_info,
            end_fraction=float(end_edge["fraction"]),
            next_edge_id=next_edge_id,
        )

        dynamic_edges.extend(start_connectors)
        dynamic_edges.extend(end_connectors)

        all_edges_with_geom_parts = [
            RoutingService.build_base_edges_sql_with_geom(excluded_edge_ids)
        ] + [
            RoutingService.edge_dict_to_sql_with_geom(edge)
            for edge in dynamic_edges
        ]

        all_edges_for_pgr_parts = [
            RoutingService.build_base_edges_sql_for_pgr(excluded_edge_ids)
        ] + [
            RoutingService.edge_dict_to_sql_for_pgr(edge)
            for edge in dynamic_edges
        ]

        return {
            "start_edge": start_edge,
            "end_edge": end_edge,
            "snapped_traffic_points": snapped_traffic_points,
            "traffic_counts_by_edge_id": traffic_counts_by_edge_id,
            "all_edges_with_geom_sql": " UNION ALL ".join(all_edges_with_geom_parts),
            "all_edges_for_pgr_sql": " UNION ALL ".join(all_edges_for_pgr_parts),
            "start_connector_edge_ids": start_connector_edge_ids,
            "end_connector_edge_ids": end_connector_edge_ids,
        }

    @staticmethod
    def classify_effective_speed_kmh(effective_speed_kmh):
        if effective_speed_kmh is None:
            return "fast"
        if effective_speed_kmh >= 70:
            return "fast"
        if effective_speed_kmh >= 40:
            return "medium"
        return "slow"

    @staticmethod
    def build_route_feature_collection(path_rows):
        features = []

        for row in path_rows:
            geometry = json.loads(row["segment_geojson"])

            distance_m = float(row["cost_distance"])
            time_sec = float(row["cost_time"])

            if time_sec > 0:
                effective_speed_kmh = (distance_m / time_sec) * 3.6
            else:
                effective_speed_kmh = None

            speed_class = RoutingService.classify_effective_speed_kmh(effective_speed_kmh)

            features.append({
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "path_seq": int(row["path_seq"]),
                    "edge_id": int(row["edge"]),
                    "distance_m": distance_m,
                    "time_sec": time_sec,
                    "effective_speed_kmh": effective_speed_kmh,
                    "speed_class": speed_class,
                }
            })

        return json.dumps({
            "type": "FeatureCollection",
            "features": features
        })

    @staticmethod
    def get_route_set(
        start_lon,
        start_lat,
        end_lon,
        end_lat,
        mode,
        blocked_polygons=None,
        traffic_points=None,
        k=3,
    ):
        blocked_polygons = blocked_polygons or []
        traffic_points = traffic_points or []

        graph_context = RoutingService.build_graph_context(
            start_lon=start_lon,
            start_lat=start_lat,
            end_lon=end_lon,
            end_lat=end_lat,
            blocked_polygons=blocked_polygons,
            traffic_points=traffic_points,
        )

        start_edge = graph_context["start_edge"]
        end_edge = graph_context["end_edge"]
        snapped_traffic_points = graph_context["snapped_traffic_points"]
        traffic_counts_by_edge_id = graph_context["traffic_counts_by_edge_id"]
        all_edges_with_geom_sql = graph_context["all_edges_with_geom_sql"]
        all_edges_for_pgr_sql = graph_context["all_edges_for_pgr_sql"]
        start_connector_edge_ids = graph_context["start_connector_edge_ids"]
        end_connector_edge_ids = graph_context["end_connector_edge_ids"]

        if mode == "distance":
            cost_column = "cost_distance"
            reverse_cost_column = "reverse_cost_distance"
        else:
            cost_column = "cost_time"
            reverse_cost_column = "reverse_cost_time"

        sql = f"""
            WITH all_edges AS (
                {all_edges_with_geom_sql}
            ),
            ksp AS (
                SELECT *
                FROM pgr_ksp(
                    $$SELECT
                        id,
                        source,
                        target,
                        {cost_column} AS cost,
                        {reverse_cost_column} AS reverse_cost
                      FROM ({all_edges_for_pgr_sql}) AS q$$,
                    {START_NODE_ID},
                    {END_NODE_ID},
                    {int(k)},
                    directed := true
                )
            ),
            path_edges AS (
                SELECT
                    k.path_id,
                    k.path_seq,
                    k.edge,
                    e.source,
                    e.target,
                    e.cost_distance,
                    e.cost_time,
                    ST_AsGeoJSON(ST_Transform(e.geom, 4326)) AS segment_geojson
                FROM ksp k
                JOIN all_edges e
                  ON k.edge = e.id
                WHERE k.edge <> -1
            )
            SELECT
                path_id,
                path_seq,
                edge,
                source,
                target,
                cost_distance,
                cost_time,
                segment_geojson
            FROM path_edges
            ORDER BY path_id, path_seq;
        """

        conn = get_db_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()

                if not rows:
                    raise ValueError(
                        f"No route found between the selected points. "
                        f"start_edge_id={start_edge['id']}, end_edge_id={end_edge['id']}, "
                        f"blocked_polygon_count={len(blocked_polygons)}, "
                        f"traffic_point_count={len(traffic_points)}"
                    )

                start_connector_edge_ids_set = set(start_connector_edge_ids)
                end_connector_edge_ids_set = set(end_connector_edge_ids)

                rows_by_path_id = {}
                for row in rows:
                    path_id = int(row["path_id"])
                    rows_by_path_id.setdefault(path_id, []).append(row)

                routes = []

                for index, path_id in enumerate(sorted(rows_by_path_id.keys())):
                    path_rows = rows_by_path_id[path_id]

                    total_distance_m = sum(float(r["cost_distance"]) for r in path_rows)
                    total_time_sec = sum(float(r["cost_time"]) for r in path_rows)
                    step_count = len(path_rows)

                    start_vertex_id = None
                    for row in path_rows:
                        if int(row["edge"]) in start_connector_edge_ids_set:
                            start_vertex_id = int(row["target"])
                            break

                    end_vertex_id = None
                    for row in reversed(path_rows):
                        if int(row["edge"]) in end_connector_edge_ids_set:
                            end_vertex_id = int(row["source"])
                            break

                    route_geojson = RoutingService.build_route_feature_collection(path_rows)

                    routes.append({
                        "route_id": path_id,
                        "label": f"Route {index + 1}",
                        "is_primary": index == 0,
                        "selected_vertices": {
                            "start_vertex_id": start_vertex_id,
                            "end_vertex_id": end_vertex_id,
                        },
                        "summary": {
                            "step_count": step_count,
                            "total_distance_m": float(total_distance_m),
                            "total_time_sec": float(total_time_sec),
                        },
                        "route_geojson": route_geojson,
                    })

                return {
                    "mode": mode,
                    "request": {
                        "start_lon": start_lon,
                        "start_lat": start_lat,
                        "end_lon": end_lon,
                        "end_lat": end_lat,
                    },
                    "start": {
                        "input_lon": start_lon,
                        "input_lat": start_lat,
                        "nearest_edge_id": int(start_edge["id"]),
                        "nearest_source": int(start_edge["source"]),
                        "nearest_target": int(start_edge["target"]),
                        "snap_distance_m": float(start_edge["snap_distance_m"]),
                    },
                    "end": {
                        "input_lon": end_lon,
                        "input_lat": end_lat,
                        "nearest_edge_id": int(end_edge["id"]),
                        "nearest_source": int(end_edge["source"]),
                        "nearest_target": int(end_edge["target"]),
                        "snap_distance_m": float(end_edge["snap_distance_m"]),
                    },
                    "blocked_polygon_count": len(blocked_polygons),
                    "traffic_point_count": len(traffic_points),
                    "traffic_edge_count": len(traffic_counts_by_edge_id),
                    "snapped_traffic_points": snapped_traffic_points,
                    "selected_route_index": 0,
                    "routes": routes,
                }
        finally:
            conn.close()