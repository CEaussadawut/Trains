# Bangkok Metro Search — Thailand Commute

A Classical AI project: route-finding across the Bangkok rail network with
**blind search** (BFS, uniform-cost, DFS, depth-limited, iterative deepening,
bidirectional), **heuristic search** (greedy best-first, hill climbing, beam) and **informed
search** (A\*, IDA\*), plus a web visualizer that replays any search step by step
and benchmarks all eleven against Dijkstra's optimum.

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

## Running it

### Command line

```bash
python3 metro_router.py N24 BL01 --algorithm astar
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

**Compare** runs all five algorithms on the same pair and scores them against
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
| `POST /api/compare` | all five algorithms scored against Dijkstra |

A search that finds no route returns **200** with `solved: false` and its trace;
only malformed requests are 4xx.
