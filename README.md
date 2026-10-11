# Bangkok Metro Search — Thailand Commute

A Classical AI project: route-finding across the Bangkok rail network with
**heuristic search** (greedy best-first, hill climbing, beam) and **informed
search** (A\*, A\* + ALT landmarks, IDA\*), plus a web visualizer that replays any
search step by step and benchmarks all six against Dijkstra's optimum.

The engine is pure Python standard library. So is the API server. Only the
frontend needs Node.

---

## The model

A node is a **(station, line)** pair, not just a station. That is what makes
changing line an explicit edge with its own cost, so "fewest transfers" is a
thing the search can actually reason about.

| | |
|---|---|
| **Rail edge** | `travel_sec` from `datasets/edges.csv` |
| **Transfer edge** | `walk_sec` from `datasets/transfers.csv`, or 180 s implicit for a station serving two lines |
| **Heuristic** | great-circle distance to the goal ÷ 45 km/h — admissible and consistent (both are asserted in the tests) |
| **Tighter heuristic** | **ALT landmarks** — exact distances from 6 landmarks, max'd with the geographic bound. 69% fewer expansions, same routes. [Details below](#the-alt-landmark-heuristic) |
| **Scenarios** | `operating` (194 stations) · `under_construction` (238) · `planned` (302) |
| **Weights** | `Weights(time, cost, transit)` — seconds, baht, and transfers combined into one edge cost |

Most Bangkok interchanges are two *separate* stations joined by a walk, not one
station on two lines — only six stations serve multiple lines. The itinerary
builder handles both.

---

## The ALT landmark heuristic

A\* is only as good as its `h`. The geographic one divides great-circle distance
by the fastest train, which is admissible but loose — it believes you can fly to
the goal at 45 km/h through buildings, so on a network that bends around the
river it leaves a lot on the table.

**ALT** (Goldberg & Harrelson, 2005) buys a tighter bound with preprocessing.
Pick a few **landmarks**, precompute *exact* shortest-path distances to and from
each, and bound the unknown distance by the triangle inequality:

&nbsp;&nbsp;`d(n, goal) ≥ d(n, L) − d(goal, L)` &nbsp;&nbsp;and&nbsp;&nbsp; `d(n, goal) ≥ d(L, goal) − d(L, n)`

Both hold for every landmark, so the largest is a valid bound — and since each
term is a difference of *exact* shortest paths it is **consistent**, not merely
admissible, so A\* still never reopens a closed node. `astar_landmark` takes the
max of that and the geographic bound: both are admissible, so their maximum is,
and it is never worse than either alone.

Landmarks are chosen by farthest-point sampling, which lands them at the ends of
the lines — Min Buri, Kheha, Khlong Bang Phai, Suvarnabhumi, Lak Song, Rangsit —
exactly where the triangle inequality is tight for trips across the city.

### What it buys

Same six benchmark pairs, same optimal routes on all six:

| pair | A\* expanded | ALT expanded | saved | A\* ms | ALT ms |
|---|---:|---:|---:|---:|---:|
| N8 → BL15 | 6 | 4 | 33% | 0.027 | 0.047 |
| CEN → E4 | 9 | 5 | 44% | 0.032 | 0.047 |
| S4 → E20 | 71 | 26 | 63% | 0.200 | 0.160 |
| N12 → PK03 | 81 | 22 | 73% | 0.246 | 0.159 |
| RN10 → A7 | 84 | 21 | 75% | 0.240 | 0.138 |
| N24 → BL01 | 103 | 31 | 70% | 0.292 | 0.181 |
| **total** | **354** | **109** | **69.2%** | **1.04** | **0.73** |

### What it costs — read the right-hand columns

Expansions fall 69%, but wall clock only ~30%, **and on the two short pairs ALT
is 1.5–1.75× _slower_**. Both facts have the same cause: evaluating `h` is no
longer one haversine but a maximum over 2k table lookups, so ALT trades many
cheap expansions for few expensive ones. The crossover on this network is around
70 expanded nodes. Quoting the 69% without the millisecond column would be
selling the wrong number.

Then there is preprocessing: **5.4 ms** for 6 landmarks (12 Dijkstras), paid once
per weighting and cached on the graph. At ~0.05 ms saved per query, ALT pays for
itself after roughly **110 queries at the same weights** — fine for a server
answering many, pointless for a one-shot CLI call. `warm_up()` builds the default
table at startup so the first request is not the one that pays.

### How many landmarks?

Six, and that is measured rather than picked. The knee is sharp:

| `k` | 0 (plain A\*) | 1 | 2 | 4 | **6** | 8 | 12 | 24 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| expanded | 354 | 254 | 157 | 121 | **109** | 108 | 108 | 106 |
| vs A\* | — | 28% | 56% | 66% | **69%** | 70% | 70% | 70% |
| precompute ms | 0 | 1.2 | 3.2 | 6.9 | **6.7** | 7.9 | 17.5 | 26.0 |

Past 6 the bound stops improving (109 → 106 all the way out to k=24) while
precompute quadruples and every `h` call gets slower. More heuristic is not free.

### Two places the textbook one-liner would be wrong here

Most write-ups give ALT as `max_L |d(L, goal) − d(L, n)|`. That is unsound on
this problem for two independent reasons, and both are regression-tested.

**1. The weighted graph is directed.** A transfer between operators is charged
the *destination* operator's base fare, and those differ (BTSC 17 ฿, SRTET 12),
so once `Weights.cost` is non-zero `d(u, v) ≠ d(v, u)` and one distance table
cannot answer both triangle inequalities. `LandmarkTable` keeps two — distances
*from* each landmark and *to* it. Substituting one for the other is not a
theoretical worry; measured against the true remaining cost:

| weighting | metric | violations by the single-table shortcut |
|---|---|---:|
| time only | symmetric | **0** |
| transfers only | symmetric | **0** |
| time + fare×30 | **directed** | **300** (up to 150 s over) |
| fare only | **directed** | **118** (up to 2 ฿ over) |

Sound while the metric is symmetric, unsound the moment fares make it directed —
and an A\* using the shortcut would keep returning routes while quietly ceasing
to be optimal. `test_one_table_is_not_enough_once_fares_are_priced` pins this.

**2. The goal is a station, not a node.** Search runs over `(station, line)`
pairs and stops at whichever platform of the goal station surfaces first, so the
quantity to bound is a `min` over the goal's nodes. That makes the two reductions
asymmetric — `max` over the goal set in one term, `min` in the other:

&nbsp;&nbsp;`min_g d(n,g) ≥ d(n,L) − max_g d(g,L)` &nbsp;&nbsp;and&nbsp;&nbsp; `min_g d(n,g) ≥ min_g d(L,g) − d(L,n)`

Swap those extrema and the heuristic looks fine on most pairs and overestimates
on some — the worst kind of bug to ship.
`test_taking_the_wrong_extremum_over_the_goal_set_would_overestimate` constructs
the swapped version and asserts it really does break, so the test fails if
someone "simplifies" `GoalBound`.

---

## Running it

### Command line

```bash
python3 metro_router.py N24 BL01 --algorithm astar
python3 metro_router.py N24 BL01 --algorithm astar_landmark --landmarks 6
python3 metro_router.py N24 BL01 --algorithm greedy --weight-transit 900
python3 bench_informed.py --scenario operating --out bench.md
```

### Tests

```bash
python3 -m unittest discover -p 'test_*.py'      # 115 tests
```

- `test_metro_router.py` — the engine: heuristic admissibility and consistency
  on every edge of every scenario, A\*/IDA\* against a Dijkstra oracle, fares,
  determinism.
- `test_trace.py` — tracing never changes a result, every counter matches its
  events, failed searches still produce a replay, the trace stays bounded.
- `test_server.py` — the API, including strict-JSON responses and concurrent
  traced requests.
- `test_landmarks.py` — the ALT heuristic: admissibility against an exact
  backwards Dijkstra under five weightings, consistency on every edge, the
  directed-metric and goal-set subtleties below, and the same Dijkstra-oracle
  optimality check A\* gets.

### The visualizer

The backend needs nothing installed:

```bash
python3 -m server                 # http://127.0.0.1:8000
```

The frontend needs Node 18+ (Vite rejects the Node 12 in Ubuntu's apt):

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt-get install -y nodejs

cd web && npm install && npm run dev      # http://localhost:5173
```

`npm run dev` proxies `/api` to the Python server, so run both.

For a single-process build with no Node at runtime:

```bash
cd web && npm run build
python3 -m server --dist web/dist         # serves the app and the API on :8000
```

---

## What the visualizer shows

**Animate** replays one search: the frontier, the nodes already expanded, the
node being expanded now, and the final route — with play/pause/step/scrub and
markers on the timeline for IDA\*'s rising thresholds and beam's rounds. Side
panels show the open list in priority order (including stale heap entries, so
lazy deletion is visible), the current node's g/h/f, and the journey itself.

The map draws both the **schematic** layout (`map_x`/`map_y`, the official
diagram geometry) and the **geographic** one (`lat`/`lon`), morphing between
them; both are fitted to one shared canvas so panning and zooming survive the
toggle.

**Compare** runs all six algorithms on the same pair and scores them against
Dijkstra: nodes expanded vs generated, runtime, and effort against route
quality. Log scales throughout — greedy expands tens of nodes where IDA\*
expands tens of thousands.

Failed searches are a feature, not an error. `hill_climbing` from `S4` to `E20`
stalls at Sala Daeng on a **local optimum**: every unvisited neighbour looks
further from the goal, and hill climbing cannot go downhill to escape. The API
returns that trace so you can watch it happen.

---

## Tracing

`Tracer` in `metro_router.py` records expansion order, frontier snapshots,
neighbour outcomes, beam pruning, IDA\* thresholds and backtracking.

It is **opt-in** — every search takes `tracer=None` by default, and an untraced
run is byte-for-byte the search it always was. Measured overhead with tracing
off is under 3%.

```python
from metro_router import MetroGraph, Tracer, astar_search

tracer = Tracer()
route = astar_search(MetroGraph(), "N24", "BL01", tracer=tracer)
payload = tracer.to_dict()     # JSON-ready: events, phase summaries, counts
```

IDA\* is the reason the tracer has a budget: on `RN10`→`A7` it expands ~14,600
nodes across 171 rising thresholds, which would be a multi-megabyte trace.
`keep_phases="last"` retains every threshold's *summary* but only the final
pass's detail — 634 events and 117 KB instead of hitting the 20,000-event cap at
3.1 MB. The server applies it to IDA\* automatically.

---

## Layout

```
metro_router.py       engine + Tracer (stdlib only)
landmarks.py          ALT preprocessing: Dijkstra, landmark picking, the bound
bench_informed.py     benchmark harness, reused by /api/compare
server/               stdlib JSON API (engine.py, api.py, serialize.py, colors.py)
web/                  React + Vite frontend
datasets/             5 CSVs — see datasets/README.md
```

### API

| | |
|---|---|
| `GET /api/meta` | algorithms grouped by family, scenarios, defaults |
| `GET /api/network?scenario=` | all 302 stations and 289 edges, each flagged active |
| `GET /api/heuristic?scenario=&goal=` | h(n) per station, for the map shading |
| `POST /api/search` | one route |
| `POST /api/trace` | one route plus its full replay |
| `POST /api/compare` | all six algorithms scored against Dijkstra |

A search that finds no route returns **200** with `solved: false` and its trace;
only malformed requests are 4xx.
