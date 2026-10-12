# Bangkok Metro Search — Thailand Commute

A Classical AI project: route-finding across the Bangkok rail network with
**blind search** (BFS, uniform-cost, DFS, depth-limited, iterative deepening,
bidirectional) and **informed (heuristic) search** (greedy best-first, hill climbing,
beam, A\*, A\* + ALT landmarks, IDA\*), plus a web visualizer that replays any search
step by step and benchmarks all twelve against Dijkstra's optimum.

The informed family is one group on purpose: greedy, hill climbing and beam steer
by the same heuristic A\* does — they are informed searches that trade away
optimality for speed, and the Compare view shows exactly what that trade costs.

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

### What "optimal" means for each blind search

| Algorithm | Guarantee | Notes |
|---|---|---|
| BFS | fewest **hops** | Goal-tested when generated (AIMA early goal test), so the goal layer is never expanded |
| Iterative deepening | fewest **hops** | Re-expands the shallow levels each round |
| Bidirectional | fewest **hops** | Expands whole layers from the smaller side, then picks the shallowest meeting |
| Uniform-cost | least **weighted cost** | With the default `Weights(time=1)` that is the fastest route, matching Dijkstra |
| DFS / depth-limited | none | Any route; DLS only within `depth_limit` hops |

A hop is any edge, **transfers included**, so a hop-optimal route can be
minutes slower than the fastest one. Only uniform-cost (and A\*/IDA\*) optimise time.

Uniform-cost prices fares exactly as `fare_for` does: the base fare is charged
on each operator segment's first hop and the `max_fare` cap applies. Because
that cost depends on the segment so far, UCS searches over (node, segment hops)
states whenever `cost` has a weight. The informed searches still use the
per-edge approximation (`per_station` per hop, `base_fare` on a cross-operator
transfer).

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
python3 metro_router.py N24 BL01 --algorithm dls --depth-limit 25
python3 bench_informed.py --scenario operating --out bench.md
```

### Tests

```bash
python3 -m unittest discover -p 'test_*.py'
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

## Hosting it for review (Cloudflare named tunnel)

The app serves from a single Python process, so sharing it is just "run it and
point a tunnel at it". `scripts/host.sh` type-checks, builds the frontend and
serves everything on `127.0.0.1:8000`.

It binds to loopback deliberately — `cloudflared` connects locally, so the app
is never directly reachable on your network and everything public goes through
Cloudflare.

### One-time setup

Steps 1, 2 and 4 talk to your Cloudflare account; step 1 opens a browser.

```bash
# 1. Authorise cloudflared and pick the domain to use
cloudflared tunnel login

# 2. Create the tunnel (prints a UUID and writes ~/.cloudflared/<UUID>.json)
cloudflared tunnel create metro-visualizer

# 3. Fill in the three placeholders: your username, that UUID, your hostname
cp scripts/cloudflared.config.example.yml ~/.cloudflared/config.yml
$EDITOR ~/.cloudflared/config.yml

# 4. Point DNS at the tunnel
cloudflared tunnel route dns metro-visualizer metro.example.com
```

### Running it

Two processes, in either order:

```bash
./scripts/host.sh                       # builds, then serves on :8000
cloudflared tunnel run metro-visualizer # publishes it at your hostname
```

To keep it up across reboots, install the user services in `scripts/systemd/`
(the header of `metro-app.service` has the commands). Those need
`sudo loginctl enable-linger $USER` to survive logout.

### Restricting it to one person

A named tunnel is public by default — anyone with the hostname can open it. To
limit it to your professor, add a Cloudflare Access policy in the dashboard:
**Zero Trust → Access → Applications → Add a self-hosted application**, set the
domain to your hostname, and add a policy of *Allow / Emails / their address*.
They get a one-time code by email the first time they visit. This requires no
change to the app, which has no authentication of its own.

### Notes

- The API is read-only — nothing it exposes can modify the repo or the dataset.
  User-supplied knobs (`repeats`, `max_events`, `beam_width`, `depth_limit`) are
  range-checked, because every engine call holds a process-wide lock and an
  unbounded value would otherwise stall the server for everyone.
- The server speaks HTTP/1.1 with keep-alive; without it the ~640 KB bundle and
  ~135 KB network payload would each reconnect per file over the tunnel.

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

**Seeing ALT work.** With *A\* + ALT landmarks* selected, the six landmarks
appear on the map as **L** diamonds, and the one supplying h for the node being
expanded is drawn solid. Under the g/h/f cards the inspector prints the
arithmetic behind h, e.g. on Khu Khot → Tha Phra at Phahon Yothin 59:

> h from landmark **Lak Song** — it lies beyond the goal:
> here → Lak Song 78.0 − goal → Lak Song 17.6 = **60.4 min** · straight line would say 29.1

**▶ Animate how h is calculated** (in the Current node tab) replays that
calculation one landmark at a time. For each landmark the map draws the two real
shortest routes whose lengths are subtracted — A in orange, B as a blue band
underneath — and the table adds a row with A − B and marks the running maximum.
The last step compares the best landmark with the straight-line bound. For Lak
Song the orange route visibly runs *through* Tha Phra on its way out to Lak Song,
on top of the blue goal → Lak Song band: that overlap is the triangle inequality
holding with equality, which is why h is exact there.

f then stays at 76.7 min — the true trip time — for every station on the route,
which is why ALT expands exactly the 31 stations of the route and nothing else
(plain A\* expands 103). "Shade by h(n) to goal" and the h/h\* note always show
the heuristic the *selected* algorithm actually uses: the landmark bound for ALT,
the straight-line one otherwise, and no h/h\* at all for blind searches.

**Compare** runs every algorithm on the same pair and scores them against
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
| `GET /api/landmark_calc?scenario=&goal=&station=&line=&w_time=&w_cost=&w_transit=` | the ALT calculation of h for one node: per landmark, A, B, A − B and both routes |
| `GET /api/heuristic?scenario=&goal=&algorithm=&w_time=&w_cost=&w_transit=` | h(n) per station as `algorithm` computes it; for `astar_landmark` also the landmarks and, per node, which landmark gave h and from which two distances |
| `POST /api/search` | one route |
| `POST /api/trace` | one route plus its full replay |
| `POST /api/compare` | every algorithm scored against Dijkstra |

A search that finds no route returns **200** with `solved: false` and its trace;
only malformed requests are 4xx.
