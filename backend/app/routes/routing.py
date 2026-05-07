import json

from flask import Blueprint, jsonify, request

from app.services.routing_service import RoutingService
from app.utils.validators import (
    validate_blocked_polygons,
    validate_coordinate,
    validate_route_mode,
    validate_traffic_points,
)

routing_bp = Blueprint("routing", __name__, url_prefix="/api")


def parse_k_value(raw_k):
    try:
        k = int(raw_k)
    except (TypeError, ValueError):
        raise ValueError("k must be a valid integer.")

    if k < 1 or k > 5:
        raise ValueError("k must be between 1 and 5.")

    return k


def normalize_route_set_for_json(route_set):
    routes = route_set.get("routes", [])

    normalized_routes = []
    for route in routes:
        normalized_route = dict(route)
        normalized_route["route_geojson"] = json.loads(route["route_geojson"])
        normalized_routes.append(normalized_route)

    route_set["routes"] = normalized_routes
    return route_set


@routing_bp.get("/health")
def route_health():
    return jsonify({
        "status": "ok",
        "service": "routing-api"
    })


@routing_bp.get("/route/simple")
def get_route_simple():
    """
    Simple GET endpoint for JSON route responses.

    Example:
    /api/route/simple?start_lat=33.6844&start_lon=73.0479&end_lat=31.5204&end_lon=74.3587&mode=time&k=3
    """
    try:
        start_lat = validate_coordinate(request.args.get("start_lat"), "start_lat")
        start_lon = validate_coordinate(request.args.get("start_lon"), "start_lon")
        end_lat = validate_coordinate(request.args.get("end_lat"), "end_lat")
        end_lon = validate_coordinate(request.args.get("end_lon"), "end_lon")
        mode = validate_route_mode(request.args.get("mode", "time"))
        k = parse_k_value(request.args.get("k", "3"))

        result = RoutingService.get_route_set(
            start_lon=start_lon,
            start_lat=start_lat,
            end_lon=end_lon,
            end_lat=end_lat,
            mode=mode,
            blocked_polygons=[],
            traffic_points=[],
            k=k,
        )

        result = normalize_route_set_for_json(result)
        return jsonify(result), 200

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({
            "error": "Internal server error.",
            "details": str(exc)
        }), 500


@routing_bp.post("/route")
def get_route():
    """
    Main POST endpoint used by the frontend.

    Request body:
    {
      "start": {"lat": 33.70, "lon": 73.10},
      "end": {"lat": 33.65, "lon": 73.05},
      "mode": "time",
      "blocked_polygons": [...],
      "traffic_points": [...],
      "k": 3
    }
    """
    try:
        payload = request.get_json(silent=True)

        if not payload:
            return jsonify({"error": "Request body must be valid JSON."}), 400

        start = payload.get("start", {})
        end = payload.get("end", {})
        mode = validate_route_mode(payload.get("mode"))
        blocked_polygons = validate_blocked_polygons(payload.get("blocked_polygons"))
        traffic_points = validate_traffic_points(payload.get("traffic_points"))
        k = parse_k_value(payload.get("k", 3))

        start_lat = validate_coordinate(start.get("lat"), "start.lat")
        start_lon = validate_coordinate(start.get("lon"), "start.lon")
        end_lat = validate_coordinate(end.get("lat"), "end.lat")
        end_lon = validate_coordinate(end.get("lon"), "end.lon")

        result = RoutingService.get_route_set(
            start_lon=start_lon,
            start_lat=start_lat,
            end_lon=end_lon,
            end_lat=end_lat,
            mode=mode,
            blocked_polygons=blocked_polygons,
            traffic_points=traffic_points,
            k=k,
        )

        result = normalize_route_set_for_json(result)
        return jsonify(result), 200

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        return jsonify({
            "error": "Internal server error.",
            "details": str(exc)
        }), 500