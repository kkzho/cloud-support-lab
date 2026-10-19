"""Public-cloud style "3-tier business" application (stdlib only).

This module is a *real HTTP application* built on ``http.server``:

    GET /healthz          health probe (500 when the instance is unhealthy)
    GET /api/order/list   business endpoint backed by cache + database
    GET /metrics          Prometheus text exposition

The very same :class:`Application` object also drives an accelerated,
in-process discrete-event simulation (used by ``runner.py``) so that the
Prometheus metrics and the log lines come from one single code path:

  * worker pool        -> ``app_worker_busy`` (a real queueing simulation)
  * db connection pool -> ``app_conn_pool_in_use`` / ``..._size``
  * database           -> ``db_connections_used`` / ``db_cpu_percent`` / slow queries
  * node resources     -> ``node_cpu_percent`` / mem / disk / inode (+ OOMKill)
  * cache (redis)      -> ``cache_latency_ms`` / ``cache_hit_ratio``
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import inject
import metrics as metrics_mod
import scenarios

TICK_MS = 1000.0  # one tick == one simulated second


# --------------------------------------------------------------------------
# Metric declarations
# --------------------------------------------------------------------------
def _declare(reg: metrics_mod.Registry) -> None:
    reg.declare_counter("http_requests_total", "Total HTTP requests by status class",
                        ["status"])
    reg.declare_gauge("http_request_duration_ms_p50", "Request latency p50 (ms)")
    reg.declare_gauge("http_request_duration_ms_p95", "Request latency p95 (ms)")
    reg.declare_gauge("http_request_duration_ms_p99", "Request latency p99 (ms)")
    reg.declare_gauge("app_worker_busy", "Number of busy app worker threads")
    reg.declare_gauge("app_conn_pool_in_use", "DB connections currently checked out")
    reg.declare_gauge("app_conn_pool_size", "Configured DB connection pool size")
    reg.declare_gauge("db_connections_used", "Server-side DB connections in use")
    reg.declare_gauge("db_connections_max", "Server-side DB max connections")
    reg.declare_gauge("db_cpu_percent", "Database CPU utilisation (%)")
    reg.declare_counter("db_slow_queries_total", "Total slow queries observed")
    reg.declare_gauge("node_cpu_percent", "Node/serving-instance CPU utilisation (%)")
    reg.declare_gauge("node_mem_percent", "Node/serving-instance memory utilisation (%)")
    reg.declare_gauge("node_disk_percent", "Node disk space utilisation (%)")
    reg.declare_gauge("node_inode_percent", "Node inode utilisation (%)")
    reg.declare_gauge("cache_latency_ms", "Cache (redis) latency p95 (ms)")
    reg.declare_gauge("cache_hit_ratio", "Cache hit ratio (0..1)")
    reg.declare_counter("container_restarts_total", "Total container restarts")
    reg.declare_counter("gateway_upstream_5xx_total", "Gateway 5xx responses (502/504)")


class SimState:
    """Mutable simulation state shared by the engine and the HTTP server."""

    def __init__(self, seed: int = 20260112):
        self.rng = random.Random(seed)
        self.reg = metrics_mod.Registry()
        _declare(self.reg)
        # node / instance resources
        self.node_cpu = 25.0
        self.node_mem = 42.0
        self.node_disk = 55.0
        self.node_inode = 30.0
        # downstream
        self.db_cpu = 22.0
        # counters mirrored for convenience
        self.sim_time = 0
        self.lock = threading.Lock()
        self.logs: list[str] = []


def percentile(values, q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(s[lo])
    return float(s[lo] * (hi - k) + s[hi] * (k - lo))


def _max_overlap(intervals) -> int:
    events = []
    for a, b in intervals:
        events.append((a, 1))
        events.append((b, -1))
    events.sort(key=lambda e: (e[0], -e[1]))
    cur = mx = 0
    for _, delta in events:
        cur += delta
        mx = max(mx, cur)
    return mx


class Application:
    """The business application and its fault-aware simulation engine."""

    def __init__(self, state: SimState | None = None):
        self.state = state or SimState()
        self.params = dict(scenarios.BASELINE)
        self.scenario = None
        self.tick = 0

    # ------------------------------------------------------------------
    def set_params(self, params: dict) -> None:
        self.params = params

    # ------------------------------------------------------------------
    # Resource dynamics (node / container memory, disk, inode)
    # ------------------------------------------------------------------
    def _update_resources(self) -> int:
        st = self.state
        p = self.params
        rng = st.rng
        restarts_now = 0

        # CPU: relax quickly toward target
        st.node_cpu += (p["node_cpu_target"] - st.node_cpu) * 0.5

        # Memory
        if p["mem_leak_rate"] > 0:
            st.node_mem += p["mem_leak_rate"]
        else:
            st.node_mem += (p["node_mem_target"] - st.node_mem) * 0.4
        if p["oom_enabled"] and st.node_mem >= p["mem_cap"]:
            st.node_mem = 30.0 + rng.uniform(0.0, 8.0)
            st.reg.inc("container_restarts_total")
            restarts_now = 1
            restarts = int(st.reg.get_counter("container_restarts_total"))
            inject.emit(st.logs, st.sim_time, "oom_kill",
                        pid=rng.randint(1000, 9999), vm=rng.randint(3_000_000, 4_200_000),
                        rss=rng.randint(2_800_000, 3_900_000), mem=100.0)
            inject.emit(st.logs, st.sim_time, "container_restart", restart_count=restarts)

        # Disk
        if p["disk_fill_rate"] > 0:
            st.node_disk = min(p["fs_cap"], st.node_disk + p["disk_fill_rate"])
        else:
            st.node_disk += (p["node_disk_target"] - st.node_disk) * 0.4

        # Inode
        if p["inode_fill_rate"] > 0:
            st.node_inode = min(p["fs_cap"], st.node_inode + p["inode_fill_rate"])
        else:
            st.node_inode += (p["node_inode_target"] - st.node_inode) * 0.4

        # DB CPU relax toward target
        st.db_cpu += (p["db_cpu_target"] - st.db_cpu) * 0.5
        return restarts_now

    # ------------------------------------------------------------------
    # One simulated second of traffic
    # ------------------------------------------------------------------
    def run_second(self) -> dict:
        st = self.state
        p = self.params
        rng = st.rng
        reg = st.reg

        restarts_now = self._update_resources()

        # --- factors derived from resource pressure ---
        cpu_factor = 1.0 + max(0.0, (st.node_cpu - 70.0) / 30.0) * p["cpu_service_mult"]
        db_cpu_factor = 1.0 + max(0.0, (st.db_cpu - 60.0) / 40.0) * 4.0

        n = int(round(p["rps"] * rng.uniform(0.85, 1.15)))
        dropped = int(n * p["lb_drop_ratio"])
        n_eff = max(0, n - dropped)
        if dropped:
            reg.inc("gateway_upstream_5xx_total", value=dropped)
            if rng.random() < 0.5:
                inject.emit(st.logs, st.sim_time,
                            rng.choice(["gateway_504", "upstream_timeout", "gateway_502"]),
                            octet=rng.randint(1, 254), dns_ms=p["dns_timeout_ms"])
        if p["healthcheck_fail"]:
            inject.emit(st.logs, st.sim_time, "healthcheck_fail",
                        fails=rng.randint(3, 9))

        # --- discrete-event request simulation ---
        workers = max(1, int(p["app_workers"]))
        worker_heap = [0.0] * workers
        heapq.heapify(worker_heap)
        free_conns = max(0, int(p["conn_pool_size"]) - int(p["leaked_conns"]))
        conn_heap = [0.0] * free_conns
        heapq.heapify(conn_heap)
        pool_size = int(p["conn_pool_size"])

        durations: list[float] = []
        cache_samples: list[float] = []
        worker_intervals = []
        conn_intervals = []
        db_intervals = []
        hits = 0
        slow_count = 0
        n_2xx = n_5xx = 0
        err = {"conn_timeout": 0, "req_timeout": 0, "dns": 0,
               "cache_timeout": 0, "disk": 0, "restart": 0}
        disk_full = self._disk_full()

        for i in range(n_eff):
            t = i * (TICK_MS / n_eff) + rng.uniform(0.0, 3.0)
            ws = heapq.heappop(worker_heap)
            start = max(t, ws)
            cursor = start + p["request_base_ms"] * cpu_factor * rng.uniform(0.8, 1.3)

            # cache lookup
            hit = rng.random() < p["cache_hit_ratio"]
            if hit:
                hits += 1
            cache_dur = p["cache_base_ms"] * rng.uniform(0.7, 1.5)
            if p["cache_hotkey"] and rng.random() < p["cache_hotkey_prob"]:
                cache_dur = p["cache_hotkey_ms"] * rng.uniform(0.5, 1.6)
            cache_timed_out = False
            if rng.random() < p["cache_timeout_rate"]:
                cache_timed_out = True
                cache_dur = p["cache_timeout_ms"]
            cache_samples.append(cache_dur)
            cursor += cache_dur

            overhead_post = (p["request_base_ms"] * 0.6) * cpu_factor * rng.uniform(0.8, 1.3)

            # DNS failure aborts the downstream call
            if rng.random() < p["dns_fail_rate"]:
                cursor += p["dns_timeout_ms"]
                finish = cursor + overhead_post
                heapq.heappush(worker_heap, finish)
                worker_intervals.append((start, finish))
                durations.append(finish - t)
                n_5xx += 1
                err["dns"] += 1
                continue

            conn_wait = 0.0
            if not hit:
                db_dur = (p["db_base_ms"] * cpu_factor * db_cpu_factor
                          * rng.uniform(0.8, 1.3))
                if rng.random() < p["db_slow_query_rate"]:
                    db_dur += p["db_slow_query_ms"]
                    slow_count += 1
                if conn_heap:
                    cs = heapq.heappop(conn_heap)
                    db_start = max(cursor, cs)
                    conn_wait = db_start - cursor
                    db_end = db_start + db_dur
                    heapq.heappush(conn_heap, db_end)
                    conn_intervals.append((db_start, db_end))
                    db_intervals.append((db_start, db_end))
                else:
                    # pool fully drained (leaked) -> cannot obtain a connection
                    conn_wait = p["conn_timeout_ms"] + 1.0
                    db_end = cursor + p["conn_timeout_ms"]
                cursor = db_end

            finish = cursor + overhead_post
            heapq.heappush(worker_heap, finish)
            worker_intervals.append((start, finish))
            dur = finish - t
            durations.append(dur)

            disk_writerr = disk_full and rng.random() < 0.30
            if disk_writerr:
                n_5xx += 1
                err["disk"] += 1
            elif restarts_now and rng.random() < 0.15:
                n_5xx += 1
                err["restart"] += 1
            elif conn_wait > p["conn_timeout_ms"]:
                n_5xx += 1
                err["conn_timeout"] += 1
            elif cache_timed_out:
                n_5xx += 1
                err["cache_timeout"] += 1
            elif dur > p["timeout_ms"]:
                n_5xx += 1
                err["req_timeout"] += 1
            else:
                n_2xx += 1

        # --- metrics ---
        reg.inc("http_requests_total", {"status": "2xx"}, n_2xx)
        reg.inc("http_requests_total", {"status": "5xx"}, n_5xx)
        reg.inc("gateway_upstream_5xx_total", value=n_5xx)
        reg.inc("db_slow_queries_total", value=slow_count)
        reg.set_gauge("app_conn_pool_size", pool_size)
        reg.set_gauge("app_conn_pool_in_use",
                      min(pool_size, int(p["leaked_conns"]) + _max_overlap(conn_intervals)))
        reg.set_gauge("app_worker_busy", _max_overlap(worker_intervals))
        reg.set_gauge("db_connections_used",
                      min(int(p["db_max_conns"]), 3 + _max_overlap(db_intervals)))
        reg.set_gauge("db_connections_max", int(p["db_max_conns"]))
        reg.set_gauge("db_cpu_percent", st.db_cpu)
        reg.set_gauge("node_cpu_percent", st.node_cpu)
        reg.set_gauge("node_mem_percent", st.node_mem)
        reg.set_gauge("node_disk_percent", st.node_disk)
        reg.set_gauge("node_inode_percent", st.node_inode)
        reg.set_gauge("http_request_duration_ms_p50", percentile(durations, 0.50))
        reg.set_gauge("http_request_duration_ms_p95", percentile(durations, 0.95))
        reg.set_gauge("http_request_duration_ms_p99", percentile(durations, 0.99))
        reg.set_gauge("cache_latency_ms", percentile(cache_samples, 0.95))
        reg.set_gauge("cache_hit_ratio", (hits / n_eff) if n_eff else 0.0)

        # --- logs driven by what actually happened this tick ---
        self._emit_logs(n_eff, n_5xx, err, slow_count, restarts_now)

        return {"n": n_eff, "dropped": dropped, "n_2xx": n_2xx, "n_5xx": n_5xx,
                "slow": slow_count}

    # ------------------------------------------------------------------
    def _emit_logs(self, n_eff, n_5xx, err, slow_count, restarts_now) -> None:
        st = self.state
        p = self.params
        rng = st.rng
        if n_eff <= 0:
            return
        # baseline request log (sampled)
        if rng.random() < 0.25 and n_5xx == 0:
            inject.emit(st.logs, st.sim_time, "info_request",
                        duration_ms=percentile([p["request_base_ms"] * 2.0], 0.95),
                        cache="hit" if rng.random() < p["cache_hit_ratio"] else "miss",
                        status=200)
        if err["conn_timeout"]:
            if int(p["leaked_conns"]) > 0:
                inject.emit(st.logs, st.sim_time, "conn_leak",
                            leaked=p["leaked_conns"], pool_size=p["conn_pool_size"])
            inject.emit(st.logs, st.sim_time, "conn_pool",
                        conn_timeout_ms=p["conn_timeout_ms"],
                        pool_size=p["conn_pool_size"],
                        waiting=err["conn_timeout"])
        if err["req_timeout"] and p["cpu_service_mult"] and st.node_cpu > 80:
            inject.emit(st.logs, st.sim_time, "cpu_saturation",
                        busy=int(st.reg.get_gauge("app_worker_busy")),
                        workers=int(p["app_workers"]), queue=err["req_timeout"],
                        cpu=st.node_cpu, duration_ms=p["timeout_ms"])
        if err["dns"]:
            inject.emit(st.logs, st.sim_time, "dns_timeout", dns_ms=p["dns_timeout_ms"])
        if err["cache_timeout"]:
            inject.emit(st.logs, st.sim_time, "cache_timeout",
                        cache_ms=p["cache_timeout_ms"], key=rng.randint(100000, 999999))
        if p["cache_hotkey"] and rng.random() < p["cache_hotkey_prob"]:
            inject.emit(st.logs, st.sim_time, "hotkey",
                        cache_ms=p["cache_hotkey_ms"], big_kb=rng.randint(800, 4096))
        if slow_count:
            inject.emit(st.logs, st.sim_time, "slow_query",
                        slow_ms=p["db_slow_query_ms"] or p["db_base_ms"],
                        rows=rng.randint(500000, 9000000))
            if st.db_cpu > 80 and rng.random() < 0.4:
                inject.emit(st.logs, st.sim_time, "db_cpu",
                            db_cpu=st.db_cpu,
                            threads=rng.randint(40, 120),
                            slow_total=int(st.reg.get_counter("db_slow_queries_total")))
        if st.node_disk >= 99.5 and rng.random() < 0.5:
            inject.emit(st.logs, st.sim_time, "disk_full",
                        disk=st.node_disk, inode=st.node_inode)
        if st.node_inode >= 99.5 and rng.random() < 0.5:
            inject.emit(st.logs, st.sim_time, "inode_full", inode=st.node_inode)

    # ------------------------------------------------------------------
    # Single-request path reused by the real HTTP server
    # ------------------------------------------------------------------
    def order_list(self):
        p = self.params
        rng = self.state.rng
        with self.state.lock:
            if rng.random() < p["dns_fail_rate"]:
                return 500, {"error": "upstream dns resolution failed"}, p["dns_timeout_ms"]
            hit = rng.random() < p["cache_hit_ratio"]
            if not hit and int(p["leaked_conns"]) >= int(p["conn_pool_size"]):
                return 500, {"error": "db connection not available"}, p["conn_timeout_ms"]
            if rng.random() < p["cache_timeout_rate"]:
                return 500, {"error": "cache timeout"}, p["cache_timeout_ms"]
            if self._disk_full():
                if rng.random() < 0.35:
                    return 500, {"error": "No space left on device"}, 5.0
            latency = p["request_base_ms"] * 1.8
            return 200, {"orders": [], "count": 0, "cache": "hit" if hit else "miss"}, latency

    def healthz(self):
        if self.params.get("healthcheck_fail"):
            return 500, {"status": "unhealthy"}
        return 200, {"status": "ok"}

    def _disk_full(self) -> bool:
        return self.state.node_disk >= 99.5 or self.state.node_inode >= 99.5

    # ------------------------------------------------------------------
    def snapshot(self) -> dict:
        reg = self.state.reg
        return {
            "http_requests_total_2xx": reg.get_counter("http_requests_total", {"status": "2xx"}),
            "http_requests_total_5xx": reg.get_counter("http_requests_total", {"status": "5xx"}),
            "http_request_duration_ms_p50": reg.get_gauge("http_request_duration_ms_p50"),
            "http_request_duration_ms_p95": reg.get_gauge("http_request_duration_ms_p95"),
            "http_request_duration_ms_p99": reg.get_gauge("http_request_duration_ms_p99"),
            "app_worker_busy": reg.get_gauge("app_worker_busy"),
            "app_conn_pool_in_use": reg.get_gauge("app_conn_pool_in_use"),
            "app_conn_pool_size": reg.get_gauge("app_conn_pool_size"),
            "db_connections_used": reg.get_gauge("db_connections_used"),
            "db_connections_max": reg.get_gauge("db_connections_max"),
            "db_cpu_percent": reg.get_gauge("db_cpu_percent"),
            "db_slow_queries_total": reg.get_counter("db_slow_queries_total"),
            "node_cpu_percent": reg.get_gauge("node_cpu_percent"),
            "node_mem_percent": reg.get_gauge("node_mem_percent"),
            "node_disk_percent": reg.get_gauge("node_disk_percent"),
            "node_inode_percent": reg.get_gauge("node_inode_percent"),
            "cache_latency_ms": reg.get_gauge("cache_latency_ms"),
            "cache_hit_ratio": reg.get_gauge("cache_hit_ratio"),
            "container_restarts_total": reg.get_counter("container_restarts_total"),
            "gateway_upstream_5xx_total": reg.get_counter("gateway_upstream_5xx_total"),
        }


# column order used by runner.py when writing metrics.csv
CSV_COLUMNS = [
    "http_requests_total_2xx", "http_requests_total_5xx",
    "http_request_duration_ms_p50", "http_request_duration_ms_p95",
    "http_request_duration_ms_p99",
    "app_worker_busy", "app_conn_pool_in_use", "app_conn_pool_size",
    "db_connections_used", "db_connections_max", "db_cpu_percent",
    "db_slow_queries_total",
    "node_cpu_percent", "node_mem_percent", "node_disk_percent", "node_inode_percent",
    "cache_latency_ms", "cache_hit_ratio",
    "container_restarts_total", "gateway_upstream_5xx_total",
]


# --------------------------------------------------------------------------
# Real HTTP server
# --------------------------------------------------------------------------
class _Handler(BaseHTTPRequestHandler):
    app: Application = None  # injected

    def log_message(self, *args):  # silence default stderr logging
        return

    def _send(self, status: int, body: dict | str, fmt: str = "json"):
        if fmt == "json":
            payload = json.dumps(body).encode()
            ctype = "application/json"
        else:
            payload = (body if isinstance(body, str) else str(body)).encode()
            ctype = "text/plain; version=0.0.4; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/healthz":
            status, body = self.app.healthz()
            self._send(status, body)
        elif path == "/api/order/list":
            status, body, _ = self.app.order_list()
            self._send(status, body)
        elif path == "/metrics":
            # hold the state lock so a concurrent simulation tick cannot mutate
            # the registry mid-render
            with self.app.state.lock:
                self._send(200, self.app.state.reg.render(), fmt="text")
        else:
            self._send(404, {"error": "not found"})


def _tick_loop(app: Application, scenario: dict | None,
               period_s: float = 1.0) -> None:
    """Drive one simulated second of traffic per real second.

    In ``serve`` / docker mode nothing else calls ``run_second()``; without this
    loop ``/metrics`` would only ever expose the declared defaults (all zeros),
    so Prometheus, Grafana and Alertmanager would stay flat. The loop cycles the
    scenario timeline, so the fault appears, peaks and recovers — keeping a live
    drill window running for as long as the container is up.
    """
    tick = 0
    total = int(scenario["total_ticks"]) if scenario else None

    def _phase_name(t: int) -> str:
        if not scenario:
            return "healthy"
        pre = int(scenario["pre_ticks"])
        fault_end = pre + int(scenario["fault_ticks"])
        if t < pre:
            return "pre"
        if t < fault_end:
            return "fault"
        return "post"

    while True:
        t0 = time.time()
        if scenario:
            phase_tick = tick % total
            inject.apply(app, scenario, phase_tick)
            app.state.sim_time = phase_tick
        else:
            phase_tick = tick
        with app.state.lock:
            app.run_second()
            # bound log memory for long-running demos
            if len(app.state.logs) > 5000:
                del app.state.logs[:-5000]
            # heartbeat every 10 simulated seconds so an idle terminal
            # does not look "frozen"; surface the current phase and the
            # signals that matter most for the running drill.
            if tick % 10 == 0:
                reg = app.state.reg
                hz, _ = app.healthz()
                p99 = reg.get_gauge("http_request_duration_ms_p99")
                gw5xx = reg.get_counter("gateway_upstream_5xx_total")
                cpu = reg.get_gauge("node_cpu_percent")
                phase = _phase_name(phase_tick)
                print(f"[tick {phase_tick:>4} | {phase:<5}] "
                      f"healthz={hz} p99={p99:.0f}ms "
                      f"gw5xx={int(gw5xx)} cpu={cpu:.0f}%",
                      flush=True)
        tick += 1
        dt = period_s - (time.time() - t0)
        if dt > 0:
            time.sleep(dt)


def serve(host: str = "0.0.0.0", port: int = 8080,
          scenario_id: str | None = None, tick_loop: bool = True,
          tick_period_s: float = 1.0) -> None:
    app = Application()
    scenario = None
    if scenario_id:
        scenario = scenarios.get(scenario_id)
        # Params resolved from the loop each tick; seed one fault-window tick so
        # the static demo still shows the fault if the loop is disabled.
        app.set_params(scenarios.params_at(scenario, scenario["pre_ticks"] + 1))
        app.scenario = scenario
    _Handler.app = app
    if tick_loop:
        threading.Thread(target=_tick_loop, args=(app, scenario, tick_period_s),
                         daemon=True).start()
    server = ThreadingHTTPServer((host, port), _Handler)
    mode = "tick-loop (live metrics)" if tick_loop else "static"
    print(f"[app] serving on http://{host}:{port}  "
          f"scenario={scenario_id or 'healthy'}  mode={mode}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="3-tier business app (stdlib)")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--scenario", default=None,
                        help="preload a fault scenario for manual demos")
    parser.add_argument("--no-tick-loop", dest="tick_loop", action="store_false",
                        help="serve static metrics only (no background simulation)")
    parser.add_argument("--tick-period", type=float, default=1.0,
                        help="real seconds per simulated second (default 1.0)")
    args = parser.parse_args()
    serve(args.host, args.port, args.scenario,
          tick_loop=args.tick_loop, tick_period_s=args.tick_period)


if __name__ == "__main__":
    main()
