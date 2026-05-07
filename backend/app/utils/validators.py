def validate_coordinate(value, field_name):
    """
    Validate that a coordinate is numeric.
    """
    if value is None:
        raise ValueError(f"{field_name} is required.")

    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a valid number.")


def validate_route_mode(mode):
    """
    Validate routing mode.
    Allowed values:
    - distance
    - time
    """
    if mode is None:
        raise ValueError("mode is required.")

    mode = str(mode).strip().lower()

    if mode not in {"distance", "time"}:
        raise ValueError("mode must be either 'distance' or 'time'.")

    return mode


def validate_polygon_ring(ring, field_name):
    """
    Validate one polygon ring in GeoJSON coordinate format:
    [
      [lon, lat],
      [lon, lat],
      ...
    ]
    """
    if not isinstance(ring, list):
        raise ValueError(f"{field_name} must be a list of coordinate pairs.")

    if len(ring) < 4:
        raise ValueError(f"{field_name} must contain at least 4 coordinate pairs.")

    normalized_ring = []

    for i, pair in enumerate(ring):
        if not isinstance(pair, list) or len(pair) != 2:
            raise ValueError(f"{field_name}[{i}] must be a [lon, lat] pair.")

        lon = validate_coordinate(pair[0], f"{field_name}[{i}][0]")
        lat = validate_coordinate(pair[1], f"{field_name}[{i}][1]")

        normalized_ring.append([lon, lat])

    if normalized_ring[0] != normalized_ring[-1]:
        raise ValueError(f"{field_name} must be closed. The first and last coordinate pair must match.")

    return normalized_ring


def validate_blocked_polygons(blocked_polygons):
    """
    Validate blocked polygons in GeoJSON geometry format.
    """
    if blocked_polygons is None:
        return []

    if not isinstance(blocked_polygons, list):
        raise ValueError("blocked_polygons must be a list.")

    normalized_polygons = []

    for index, polygon in enumerate(blocked_polygons):
        if not isinstance(polygon, dict):
            raise ValueError(f"blocked_polygons[{index}] must be an object.")

        polygon_type = polygon.get("type")
        if polygon_type != "Polygon":
            raise ValueError(f"blocked_polygons[{index}].type must be 'Polygon'.")

        coordinates = polygon.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) == 0:
            raise ValueError(f"blocked_polygons[{index}].coordinates must be a non-empty list.")

        normalized_rings = []
        for ring_index, ring in enumerate(coordinates):
            normalized_rings.append(
                validate_polygon_ring(
                    ring,
                    f"blocked_polygons[{index}].coordinates[{ring_index}]"
                )
            )

        normalized_polygons.append({
            "type": "Polygon",
            "coordinates": normalized_rings
        })

    return normalized_polygons


def validate_traffic_points(traffic_points):
    """
    Validate traffic point list.

    Expected format:
    [
        {"lat": 33.68, "lon": 73.04},
        {"lat": 33.69, "lon": 73.05}
    ]

    Returns normalized list of:
    [
        {"lat": float, "lon": float},
        ...
    ]
    """
    if traffic_points is None:
        return []

    if not isinstance(traffic_points, list):
        raise ValueError("traffic_points must be a list.")

    normalized_points = []

    for index, point in enumerate(traffic_points):
        if not isinstance(point, dict):
            raise ValueError(f"traffic_points[{index}] must be an object.")

        lat = validate_coordinate(point.get("lat"), f"traffic_points[{index}].lat")
        lon = validate_coordinate(point.get("lon"), f"traffic_points[{index}].lon")

        normalized_points.append({
            "lat": lat,
            "lon": lon
        })

    return normalized_points