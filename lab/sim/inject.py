"""Fault injector: parameter degradation + real-style log emission.

``app.py`` owns the simulation engine, but every *fault-driven* log line is
produced here from templates, so the log vocabulary lives in exactly one place.
The injector also exposes ``apply()`` which pushes the scenario parameters onto
the live ``Application`` each tick (the "injection" step of the drill).
"""

from __future__ import annotations

import datetime

import scenarios


# --------------------------------------------------------------------------
# Real-style log templates. ``{ts} {service}`` are prefixed by emit().
# These mirror the messages a real incident would carry in app logs / kubelet.
# --------------------------------------------------------------------------
TEMPLATES: dict = {
    "info_request": (
        "INFO", "app",
        'order/list handled in {duration_ms:.0f}ms cache={cache} status={status}',
    ),
    "cpu_saturation": (
        "WARN", "app",
        "worker pool saturated: busy={busy}/{workers} queue={queue} cpu={cpu:.0f}% "
        "-> request timeout after {duration_ms:.0f}ms",
    ),
    "conn_pool": (
        "ERROR", "app",
        "HikariPool-1 - Connection is not available, request timed out after "
        "{conn_timeout_ms:.0f}ms (total={pool_size}, active={pool_size}, "
        "idle=0, waiting={waiting})",
    ),
    "conn_leak": (
        "WARN", "app",
        "connection leak detected: {leaked}/{pool_size} connections checked out "
        "and never returned to HikariPool-1",
    ),
    "request_timeout": (
        "ERROR", "app",
        "request timed out after {duration_ms:.0f}ms: GET /api/order/list "
        "(worker=busy)",
    ),
    "slow_query": (
        "WARN", "db",
        "# slow query: SELECT o.id,o.buyer_id,o.amount FROM orders o "
        "JOIN order_items i ON i.order_id=o.id WHERE o.status=? "
        "took {slow_ms:.0f}ms rows_examined={rows}",
    ),
    "db_cpu": (
        "WARN", "db",
        "mysqld CPU above threshold: db_cpu={db_cpu:.0f}% "
        "threads_running={threads} slow_queries={slow_total}",
    ),
    "oom_kill": (
        "ERROR", "kernel",
        "Out of memory: Killed process {pid} (java) total-vm:{vm}kB anon-rss:{rss}kB "
        "container=app memory limit exceeded at {mem:.0f}%",
    ),
    "container_restart": (
        "WARN", "kubelet",
        "Container app failed with Reason OOMKilled (exit code 137); restarting "
        "container {restart_count} time(s)",
    ),
    "disk_full": (
        "ERROR", "node",
        "No space left on device: failed to write /var/log/app/app.log "
        "(disk={disk:.0f}% inode={inode:.0f}%)",
    ),
    "inode_full": (
        "ERROR", "node",
        "inode exhausted on /var/lib/app: 'No space left on device' "
        "while creating temp file (inode={inode:.0f}%)",
    ),
    "dns_timeout": (
        "ERROR", "app",
        "getaddrinfo failed for 'order-db.svc.cluster.local': "
        "nameserver 10.96.0.10 timeout after {dns_ms:.0f}ms",
    ),
    "cache_timeout": (
        "ERROR", "app",
        "Redis command timed out after {cache_ms:.0f}ms: GET order:{key} "
        "(jedis ReadTimeoutException)",
    ),
    "hotkey": (
        "WARN", "cache",
        "hot key detected: key 'order:promo:2026' hit_rate bursts, shard latency "
        "{cache_ms:.0f}ms (big key size ~{big_kb}KB)",
    ),
    "healthcheck_fail": (
        "ERROR", "slb",
        "health check failed for backend 10.0.0.21:8080 GET /healthz "
        "-> HTTP 500, backend marked DOWN (consecutive={fails})",
    ),
    "upstream_timeout": (
        "ERROR", "nginx",
        "upstream timed out (110: Connection timed out) while reading response "
        "header from upstream, client 198.51.100.{octet}, server: order-api",
    ),
    "gateway_502": (
        "ERROR", "nginx",
        "no live upstreams while connecting to upstream, "
        "status=502, request: GET /api/order/list",
    ),
    "gateway_504": (
        "ERROR", "nginx",
        "upstream timed out (110) client 198.51.100.{octet} "
        "status=504, request: GET /api/order/list",
    ),
}


def _ts(sim_time_s: int) -> str:
    base = datetime.datetime(2026, 1, 12, 9, 0, 0)  # fixed drill clock
    return (base + datetime.timedelta(seconds=int(sim_time_s))).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )


def emit(log_sink, sim_time_s: int, kind: str, **fields) -> None:
    """Render a template and append it to the log sink."""
    tmpl = TEMPLATES.get(kind)
    if tmpl is None:
        return
    level, service, fmt = tmpl
    try:
        message = fmt.format(**fields)
    except (KeyError, ValueError):
        message = fmt
    ts = _ts(sim_time_s)
    log_sink.append(f"{ts} {level:<5} [{service}] {message}")


def apply(app, scenario: dict, tick: int) -> None:
    """Inject the fault parameters active at ``tick`` into the application."""
    app.set_params(scenarios.params_at(scenario, tick))
    app.scenario = scenario
    app.tick = tick
