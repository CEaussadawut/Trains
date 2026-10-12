"""ALT landmark preprocessing — the differential heuristic behind `astar_landmark`.

A* is only as good as its heuristic. The geographic one in `metro_router` is
great-circle distance divided by the fastest train, which is admissible but
loose: it believes you can fly to the goal at 45 km/h through buildings, so on
a network whose track bends around the river it leaves a lot on the table.

ALT (Goldberg & Harrelson, 2005) buys a tighter one with preprocessing. Pick a
handful of **landmarks**, compute *exact* shortest-path distances to and from
every one of them, and bound the unknown distance by the triangle inequality:

    d(n, goal) >= d(n, L) - d(goal, L)          and
    d(n, goal) >= d(L, goal) - d(L, n)

Both hold for every landmark L, so the largest of them is a valid lower bound,
and because each term is a difference of exact shortest-path distances the
bound is **consistent** as well as admissible.

Why this module is not three lines
----------------------------------
Two properties of this particular network make the textbook one-liner
(``max_L |d(L, goal) - d(L, n)|``) **wrong** here:

1. **The weighted graph is directed.** Rail edges are symmetric, but a transfer
   between operators is charged the *destination* operator's base fare, and
   those differ (BTSC 17 THB, SRTET 12). So as soon as ``Weights.cost`` is
   non-zero, ``d(u, v) != d(v, u)`` and a single distance table per landmark
   cannot answer both triangle inequalities. We keep two — distances *from* the
   landmark and distances *to* it — which is the directed ALT of the original
   paper. With ``cost = 0`` the two tables coincide and the bound collapses to
   the familiar absolute difference.

2. **The goal is a station, not a node.** Search runs over ``(station, line)``
   pairs and stops at whichever node of the goal station comes up first, so the
   quantity to bound is ``min`` over the goal's nodes. Taking the bound against
   one arbitrary goal node would overestimate and destroy optimality. See
   `GoalBound` for the two reductions that fix it.

Nothing here knows about trains: it takes an adjacency mapping and an edge-cost
function, which keeps it testable on hand-built graphs and keeps the metric
whatever the caller is actually minimising.
"""
from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Callable, Hashable, Iterable, Sequence

Node = Hashable
# node -> iterable of (neighbour, raw_cost, kind), i.e. MetroGraph.adjacency
Adjacency = dict
# (from_node, to_node, raw_cost, kind) -> weighted cost of traversing that arc
CostFn = Callable[[Node, Node, float, str], float]

DEFAULT_LANDMARKS = 6


def dijkstra(adjacency: Adjacency, sources: Iterable[Node], cost_fn: CostFn,
             reverse: bool = False) -> dict[Node, float]:
    """Shortest weighted distance from `sources` to every reachable node.

    `reverse=True` walks the arcs backwards, giving distance *to* `sources`
    instead — note the cost is still looked up in the arc's original direction
    (`cost_fn(u, v, ...)` for the arc u -> v), which is the whole point: on a
    directed graph the reverse search must pay the forward price.
    """
    dist: dict[Node, float] = {}
    heap: list[tuple[float, int, Node]] = []
    order = 0
    for source in sources:
        if source not in dist:
            dist[source] = 0.0
            heap.append((0.0, order, source))
            order += 1
    heapq.heapify(heap)

    while heap:
        d, _, u = heapq.heappop(heap)
        if d > dist.get(u, math.inf):
            continue                                  # stale heap entry
        for v, raw, kind in adjacency.get(u, ()):
            # forward: we are at u and move to v, paying w(u -> v).
            # reverse: we are at u in the reversed graph, which is the head of
            # the real arc v -> u, so the price is w(v -> u).
            step = cost_fn(v, u, raw, kind) if reverse else cost_fn(u, v, raw, kind)
            nd = d + step
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                order += 1
                heapq.heappush(heap, (nd, order, v))
    return dist


@dataclass(frozen=True)
class LandmarkTable:
    """Exact distances to and from each landmark, for one metric.

    Valid only for the (graph, cost function) it was built from — the distances
    *are* the metric. Build a new one when the weights change; `metro_router`
    caches them per weighting so that happens at most once.
    """
    landmarks: tuple[Node, ...]
    dist_from: tuple[dict[Node, float], ...]   # dist_from[i][v] = d(L_i, v)
    dist_to: tuple[dict[Node, float], ...]     # dist_to[i][v]   = d(v, L_i)
    symmetric: bool = True                     # the two tables agree everywhere

    def __len__(self) -> int:
        return len(self.landmarks)


@dataclass
class GoalBound:
    """`LandmarkTable` collapsed against one goal *set*, ready to evaluate.

    The search may finish at any node of the goal station, so the heuristic has
    to lower-bound `min(d(n, g) for g in goal_nodes)`. Per landmark that reduces
    to two constants, both computed once here:

        min_g d(n, g) >= d(n, L) - max_g d(g, L)        -> `to_goal_max`
        min_g d(n, g) >= min_g d(L, g) - d(L, n)        -> `from_goal_min`

    Taking `max_g` in the first and `min_g` in the second is what keeps the
    bound valid for *whichever* goal node the search reaches: pick the other way
    round and the heuristic overestimates on some pairs, A* stops being optimal,
    and the Dijkstra oracle in the tests catches it.

    Evaluation is then O(landmarks) with two dict lookups each.
    """
    table: LandmarkTable
    to_goal_max: tuple[float, ...]
    from_goal_min: tuple[float, ...]
    usable: tuple[int, ...] = field(default=())

    @classmethod
    def build(cls, table: LandmarkTable, goal_nodes: Sequence[Node]) -> GoalBound:
        if not goal_nodes:
            # No goal node means nothing to bound against, and both reductions
            # below would take an extremum of an empty set. Callers reach this
            # by asking for a station with no open platforms, which is their
            # bug, so say so rather than returning a silently useless bound.
            raise ValueError("goal_nodes must not be empty")
        to_goal_max, from_goal_min, usable = [], [], []
        for i in range(len(table)):
            d_to, d_from = table.dist_to[i], table.dist_from[i]
            # A goal node the landmark cannot reach (or be reached from) makes
            # that landmark's bound unusable for this query rather than wrong:
            # an unreachable goal node could be the cheap one.
            reach_to = [d_to[g] for g in goal_nodes if g in d_to]
            reach_from = [d_from[g] for g in goal_nodes if g in d_from]
            ok_to = len(reach_to) == len(goal_nodes)
            ok_from = len(reach_from) == len(goal_nodes)
            to_goal_max.append(max(reach_to) if ok_to else math.inf)
            from_goal_min.append(min(reach_from) if ok_from else -math.inf)
            if ok_to or ok_from:
                usable.append(i)
        return cls(table, tuple(to_goal_max), tuple(from_goal_min), tuple(usable))

    def explain(self, node: Node) -> dict | None:
        """Which landmark produced `self(node)`, and from which two distances.

        Returns None when no landmark gives a positive bound (at the goal, or
        when every landmark sits "beside" the trip). Otherwise:

            via="beyond_goal"  the landmark lies past the goal, n -> goal -> L:
                               bound = d(n -> L) - d(goal -> L)
            via="behind_you"   the landmark lies behind n, L -> n -> goal:
                               bound = d(L -> goal) - d(L -> n)

        In both cases bound == first - second, so a UI can print the
        subtraction as-is. Ties go to the earlier landmark, matching __call__.
        """
        best: dict | None = None
        table = self.table
        for i in self.usable:
            d_to = table.dist_to[i].get(node)
            if d_to is not None and self.to_goal_max[i] != math.inf:
                value = d_to - self.to_goal_max[i]
                if value > (best["bound"] if best else 0.0):
                    best = {"landmark": table.landmarks[i], "via": "beyond_goal",
                            "first": d_to, "second": self.to_goal_max[i], "bound": value}
            d_from = table.dist_from[i].get(node)
            if d_from is not None and self.from_goal_min[i] != -math.inf:
                value = self.from_goal_min[i] - d_from
                if value > (best["bound"] if best else 0.0):
                    best = {"landmark": table.landmarks[i], "via": "behind_you",
                            "first": self.from_goal_min[i], "second": d_from, "bound": value}
        return best

    def __call__(self, node: Node) -> float:
        """The ALT lower bound on the cost from `node` to the goal set."""
        best = 0.0
        table = self.table
        for i in self.usable:
            d_to = table.dist_to[i].get(node)
            if d_to is not None:
                candidate = d_to - self.to_goal_max[i]
                if candidate > best:
                    best = candidate
            d_from = table.dist_from[i].get(node)
            if d_from is not None:
                candidate = self.from_goal_min[i] - d_from
                if candidate > best:
                    best = candidate
        return best


def select_landmarks(adjacency: Adjacency, cost_fn: CostFn, count: int,
                     seed_node: Node | None = None) -> list[Node]:
    """Farthest-point sampling: repeatedly add the node furthest from every
    landmark chosen so far.

    Landmarks are useful when the goal sits roughly *behind* one of them, so
    they want to be at the extremes of the network — the ends of the lines, in
    practice. Deterministic by construction: the seed is the smallest node id
    and every argmax breaks ties on the node id, so the same graph always yields
    the same landmarks and therefore the same expansion counts.
    """
    nodes = sorted(adjacency)
    if not nodes or count < 1:
        return []
    start = seed_node if seed_node is not None else nodes[0]

    def farthest(spread: dict[Node, float]) -> Node:
        # reachable nodes first; ties by node id for determinism
        return max(nodes, key=lambda n: (spread.get(n, -1.0), n))

    chosen: list[Node] = [farthest(dijkstra(adjacency, [start], cost_fn))]
    spread = dict(dijkstra(adjacency, [chosen[0]], cost_fn))
    while len(chosen) < min(count, len(nodes)):
        nxt = farthest(spread)
        if nxt in chosen:
            break                                   # nothing new left to cover
        chosen.append(nxt)
        for node, d in dijkstra(adjacency, [nxt], cost_fn).items():
            if d < spread.get(node, math.inf):
                spread[node] = d
    return chosen


def build_table(adjacency: Adjacency, cost_fn: CostFn,
                count: int = DEFAULT_LANDMARKS) -> LandmarkTable:
    """Choose landmarks and run the 2k Dijkstras that define the heuristic."""
    chosen = select_landmarks(adjacency, cost_fn, count)
    dist_from = tuple(dijkstra(adjacency, [L], cost_fn) for L in chosen)
    dist_to = tuple(dijkstra(adjacency, [L], cost_fn, reverse=True) for L in chosen)
    symmetric = all(
        a.keys() == b.keys() and all(abs(a[n] - b[n]) <= 1e-9 for n in a)
        for a, b in zip(dist_from, dist_to)
    )
    return LandmarkTable(tuple(chosen), dist_from, dist_to, symmetric)


__all__ = [
    "DEFAULT_LANDMARKS", "GoalBound", "LandmarkTable",
    "build_table", "dijkstra", "select_landmarks",
]
