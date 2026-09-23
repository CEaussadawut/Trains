"""Turn engine objects into JSON-safe payloads for the web client."""
from __future__ import annotations
import math
from typing import Any

from metro_router import MetroGraph, Node, Route

from server.colors import dash_for, hex_for


def json_safe(value: Any) -> Any:
    """Replace non-finite floats with null.

    json.dumps happily emits bare `Infinity` and `NaN`, which are not valid
    JSON and which JSON.parse rejects outright.
    """
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    return value


def edge_cost(graph: MetroGraph, node: Node, following: Node) -> float:
    costs = [cost for neighbor, cost, _kind in graph.neighbors(node) if neighbor == following]
    return min(costs) if costs else 0.0


def network_payload(graph: MetroGraph, full: MetroGraph | None = None) -> dict:
    """Every station and edge, each flagged active, so the map can grey out the
    future network instead of hiding it.

    Geometry comes from `full` (the `planned` graph, a superset of every
    scenario) while `active` is judged against `graph`. Reading edges from
    `graph.adjacency` alone would omit the unbuilt lines entirely, and the
    scenario switch is much easier to read when the future network fades in
    rather than appearing from nothing.
    """
    full = full if full is not None else graph
    stations = []
    for station in graph.stations.values():
        active_lines = graph.active_station_lines(station.id)
        stations.append({
            "id": station.id,
            "name_en": station.name_en,
            "name_th": station.name_th,
            "lat": station.lat,
            "lon": station.lon,
            "map_x": station.map_x,
            "map_y": station.map_y,
            "label_side": station.label_side,
            "lines": station.lines,
            "active_lines": active_lines,
            "active": bool(active_lines),
            "interchange": len(station.lines) > 1,
        })

    lines = []
    for line_id, row in graph.lines.items():
        lines.append({
            "id": line_id,
            "name_en": row["name_en"],
            "name_th": row["name_th"],
            "color": row["color"],
            "color_hex": hex_for(row["color"]),
            "dash": dash_for(row["status"]),
            "operator": row["operator"],
            "status": row["status"],
            "mode": row["mode"],
            "active": line_id in graph.active_lines,
        })

    # Edges come straight out of the adjacency, deduplicated -- the graph stores
    # both directions.
    seen: set[frozenset[Node]] = set()
    edges = []
    transfers = []
    for node, neighbors in full.adjacency.items():
        for neighbor, cost, kind in neighbors:
            key = frozenset((node, neighbor))
            if key in seen:
                continue
            seen.add(key)
            active = node[1] in graph.active_lines and neighbor[1] in graph.active_lines
            record = {
                "from": node[0], "to": neighbor[0],
                "from_line": node[1], "to_line": neighbor[1],
                "seconds": cost,
                "active": active,
            }
            if kind == "rail":
                record["line"] = node[1]
                edges.append(record)
            else:
                transfers.append(record)

    return {
        "scenario": graph.scenario,
        "stations": stations,
        "lines": lines,
        "edges": edges,
        "transfers": transfers,
        "fares": graph.fares,
        "bounds": _bounds(stations),
        "stats": {
            "stations": len(graph.stations),
            "active_stations": sum(1 for s in stations if s["active"]),
            "nodes": graph.num_nodes,
            "rail_edges": graph.num_rail_edges,
            "transfer_edges": sum(1 for edge in transfers if edge["active"]),
        },
    }


def _bounds(stations: list[dict]) -> dict:
    def extent(key: str) -> tuple[float, float]:
        values = [station[key] for station in stations]
        return (min(values), max(values))

    min_x, max_x = extent("map_x")
    min_y, max_y = extent("map_y")
    min_lon, max_lon = extent("lon")
    min_lat, max_lat = extent("lat")
    return {
        "schematic": {"min_x": min_x, "max_x": max_x, "min_y": min_y, "max_y": max_y},
        "geographic": {"min_lon": min_lon, "max_lon": max_lon, "min_lat": min_lat, "max_lat": max_lat},
    }


def itinerary(graph: MetroGraph, path: list[Node]) -> list[dict]:
    """Group a node path into human-readable ride and transfer legs.

    A transfer is any step that changes line -- which in this network usually
    also changes station, because most Bangkok interchanges are two separate
    stations joined by a walk rather than one station serving two lines.
    """
    if len(path) < 2:
        return []

    legs: list[dict] = []
    index = 0
    while index < len(path):
        line = path[index][1]
        end = index
        while end + 1 < len(path) and path[end + 1][1] == line:
            end += 1

        if end > index:
            stations = [station_id for station_id, _ in path[index:end + 1]]
            seconds = sum(
                edge_cost(graph, path[step], path[step + 1]) for step in range(index, end)
            )
            legs.append({
                "kind": "ride",
                "line": line,
                "line_name": graph.lines[line]["name_en"],
                "color_hex": hex_for(graph.lines[line]["color"]),
                "operator": graph.lines[line]["operator"],
                "from": stations[0],
                "to": stations[-1],
                "from_name": graph.stations[stations[0]].name_en,
                "to_name": graph.stations[stations[-1]].name_en,
                "stations": stations,
                "stops": len(stations) - 1,
                "seconds": seconds,
            })

        if end + 1 < len(path):
            here, there = path[end], path[end + 1]
            legs.append({
                "kind": "transfer",
                "from": here[0],
                "to": there[0],
                "from_name": graph.stations[here[0]].name_en,
                "to_name": graph.stations[there[0]].name_en,
                "from_line": here[1],
                "to_line": there[1],
                "same_station": here[0] == there[0],
                "seconds": edge_cost(graph, here, there),
            })
        index = end + 1
    return legs


def route_payload(graph: MetroGraph, route: Route) -> dict:
    return {
        "path": [list(node) for node in route.path],
        "stations": [
            {
                "id": station_id,
                "line": line_id,
                "name_en": graph.stations[station_id].name_en,
                "name_th": graph.stations[station_id].name_th,
            }
            for station_id, line_id in route.path
        ],
        "duration_sec": route.duration_sec,
        "fare_thb": route.fare_thb,
        "hops": route.hops,
        "transfers": route.transfers,
        "nodes_expanded": route.nodes_expanded,
        "nodes_generated": route.nodes_generated,
        "runtime_sec": route.runtime_sec,
        "peak_memory_bytes": route.peak_memory_bytes,
        "itinerary": itinerary(graph, route.path),
    }
