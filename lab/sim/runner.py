"""Accelerated fault-drill runner.

Usage:
    python3 lab/sim/runner.py --scenario S2-conn-pool-exhaustion --out <dir>
    python3 lab/sim/runner.py --all --out /workspace/cloud-support-lab/evidence

Each tick represents one *simulated* second of traffic but is executed in
milliseconds, so a 240-300 tick scenario completes in a few seconds. Metrics,
logs, a JSON summary and a Markdown timeline are written to
``<out>/<scenario_id>/``. Everything is produced by real simulation runs; no
metric value is ever hard-coded.
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import statistics
import sys
import time

# make sibling imports work no matter how the script is invoked
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_mod          # noqa: E402
import inject                  # noqa: E402
import scenarios               # noqa: E402


def _stable_seed(sid: str) -> int:
    """Deterministic seed so evidence runs are reproducible."""
    return 20260112 + sum((i + 1) * ord(c) for i, c in enumerate(sid)) % 100000

GAUGE_COLS = [
    "http_request_duration_ms_p50", "http_request_duration_ms_p95",
    "http_request_duration_ms_p99",
    "app_worker_busy", "app_conn_pool_in_use", "app_conn_pool_size",
    "db_connections_used", "db_connections_max", "db_cpu_percent",
    "node_cpu_percent", "node_mem_percent", "node_disk_percent", "node_inode_percent",
    "cache_latency_ms", "cache_hit_ratio",
]
COUNTER_COLS = [
    "http_requests_total_2xx", "http_requests_total_5xx",
    "db_slow_queries_total", "container_restarts_total",
    "gateway_upstream_5xx_total",
]

LOG_KEYWORDS = [
    ("conn_pool", "HikariPool"),
    ("conn_leak", "connection leak"),
    ("slow_query", "slow query"),
    ("db_cpu", "mysqld CPU above threshold"),
    ("oom_kill", "Out of memory"),
    ("container_restart", "restarting container"),
    ("disk_full", "No space left on device"),
    ("inode_full", "inode exhausted"),
    ("dns_timeout", "nameserver"),
    ("cache_timeout", "Redis command timed out"),
    ("hotkey", "hot key detected"),
    ("healthcheck_fail", "health check failed"),
    ("upstream_timeout", "upstream timed out"),
    ("gateway_502", "no live upstreams"),
]


# --------------------------------------------------------------------------
def _mean(rows, col):
    vals = [r[col] for r in rows if col in r]
    return statistics.mean(vals) if vals else 0.0


def _last(rows, col):
    vals = [r[col] for r in rows if col in r]
    return vals[-1] if vals else 0.0


def _phase_stats(rows):
    """Return per-phase gauge means and end-of-phase counter totals."""
    stats = {}
    for phase in ("pre", "fault", "post"):
        seg = [r for r in rows if r["phase"] == phase]
        if not seg:
            stats[phase] = {"ticks": 0}
            continue
        block = {"ticks": len(seg)}
        for col in GAUGE_COLS:
            block[col] = round(_mean(seg, col), 4)
            block[col + "__max"] = round(max(r[col] for r in seg), 4)
        for col in COUNTER_COLS:
            block[col + "__end"] = _last(seg, col)
            block[col + "__delta"] = round(_last(seg, col) - seg[0][col], 4)
        req = (seg[0]["http_requests_total_2xx"] + seg[0]["http_requests_total_5xx"])
        req_end = (seg[-1]["http_requests_total_2xx"] + seg[-1]["http_requests_total_5xx"])
        req_delta = max(0.0, req_end - req)
        err_delta = max(0.0, seg[-1]["http_requests_total_5xx"] - seg[0]["http_requests_total_5xx"])
        block["requests_total_delta"] = req_delta
        block["req_rate_per_tick"] = round(req_delta / len(seg), 2)
        block["http_5xx_rate"] = round(err_delta / req_delta, 4) if req_delta else 0.0
        stats[phase] = block
    return stats


def _detect_anomalies(stats, scenario):
    """Derive observed anomalies from the *actual* data (not hard-coded)."""
    pre, fault = stats["pre"], stats["fault"]
    if not pre.get("ticks") or not fault.get("ticks"):
        return []
    out = []

    def ratio(a, b):
        return (a / b) if b else float("inf") if a else 1.0

    if fault["http_5xx_rate"] > pre["http_5xx_rate"] + 0.01:
        out.append(
            f"5xx 错误率从 {pre['http_5xx_rate']*100:.2f}% 升至 "
            f"{fault['http_5xx_rate']*100:.2f}%（故障期实测）"
        )
    if fault["http_request_duration_ms_p99"] > pre["http_request_duration_ms_p99"] * 1.8 + 20:
        out.append(
            f"P99 从 {pre['http_request_duration_ms_p99']:.0f}ms 恶化到 "
            f"{fault['http_request_duration_ms_p99']:.0f}ms"
        )
    if fault["node_cpu_percent"] > max(80.0, pre["node_cpu_percent"] + 30):
        out.append(f"node_cpu_percent 达 {fault['node_cpu_percent']:.1f}%")
    if fault["app_worker_busy"] > max(4.0, pre["app_worker_busy"] * 2):
        out.append(f"app_worker_busy 升至 {fault['app_worker_busy']:.0f}（worker 池接近饱和）")
    if fault["app_conn_pool_in_use"] >= fault["app_conn_pool_size"] * 0.95 and fault["app_conn_pool_size"]:
        out.append(
            f"app_conn_pool_in_use={fault['app_conn_pool_in_use']:.0f}/"
            f"{fault['app_conn_pool_size']:.0f}（连接池打满）"
        )
    if fault["db_cpu_percent"] > pre["db_cpu_percent"] + 30:
        out.append(f"db_cpu_percent 从 {pre['db_cpu_percent']:.1f}% 升至 {fault['db_cpu_percent']:.1f}%")
    if fault["db_slow_queries_total__delta"] > 0:
        out.append(f"db_slow_queries_total 在故障期新增 {fault['db_slow_queries_total__delta']:.0f} 次")
    if fault["node_mem_percent__max"] > max(80.0, pre["node_mem_percent__max"] + 25):
        out.append(f"node_mem_percent 峰值达 {fault['node_mem_percent__max']:.1f}%（均值 "
                   f"{fault['node_mem_percent']:.1f}%）")
    if fault["container_restarts_total__delta"] > 0:
        out.append(f"container_restarts_total 故障期新增 {fault['container_restarts_total__delta']:.0f} 次")
    if fault["node_disk_percent__max"] > 95:
        out.append(f"node_disk_percent 峰值达 {fault['node_disk_percent__max']:.1f}%")
    if fault["node_inode_percent__max"] > 95:
        out.append(f"node_inode_percent 峰值达 {fault['node_inode_percent__max']:.1f}%")
    if pre.get("req_rate_per_tick") and fault["req_rate_per_tick"] < pre["req_rate_per_tick"] * 0.7:
        out.append(f"业务接口到达率从 {pre['req_rate_per_tick']:.0f} 降至 "
                   f"{fault['req_rate_per_tick']:.0f} req/tick（后端被摘除）")
    if fault["cache_latency_ms"] > pre["cache_latency_ms"] * 3 + 2:
        out.append(
            f"cache_latency_ms 从 {pre['cache_latency_ms']:.1f}ms 升至 {fault['cache_latency_ms']:.1f}ms"
        )
    if fault["cache_hit_ratio"] < pre["cache_hit_ratio"] - 0.1:
        out.append(
            f"cache_hit_ratio 从 {pre['cache_hit_ratio']:.2f} 降到 {fault['cache_hit_ratio']:.2f}"
        )
    if fault["gateway_upstream_5xx_total__delta"] > max(5, pre["gateway_upstream_5xx_total__delta"] * 2):
        out.append(
            f"gateway_upstream_5xx_total 故障期新增 {fault['gateway_upstream_5xx_total__delta']:.0f} 次"
        )
    return out


def _count_logs(log_text):
    counts = {}
    for kind, kw in LOG_KEYWORDS:
        c = log_text.count(kw)
        if c:
            counts[kind] = c
    return counts


# --------------------------------------------------------------------------
def run_scenario(scenario, out_root):
    sid = scenario["id"]
    out_dir = os.path.join(out_root, sid)
    os.makedirs(out_dir, exist_ok=True)

    tick_sleep = float(scenario.get("tick_sleep_s", 0.03))
    total_ticks = int(scenario["total_ticks"])
    pre_end, fault_end = scenarios.phases(scenario)

    state = app_mod.SimState(seed=_stable_seed(sid))
    application = app_mod.Application(state)

    rows = []
    t0 = time.time()
    for tick in range(total_ticks):
        application.state.sim_time = tick
        inject.apply(application, scenario, tick)
        application.run_second()
        snap = application.snapshot()
        row = {"tick": tick, "sim_time_s": tick,
               "phase": scenarios.phase_of(scenario, tick)}
        row.update(snap)
        rows.append(row)
        time.sleep(tick_sleep)
    wall = time.time() - t0

    # ---- metrics.csv ----
    columns = ["tick", "sim_time_s", "phase"] + app_mod.CSV_COLUMNS
    with open(os.path.join(out_dir, "metrics.csv"), "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    # ---- app.log ----
    log_text = "\n".join(state.logs) + ("\n" if state.logs else "")
    with open(os.path.join(out_dir, "app.log"), "w", encoding="utf-8") as fh:
        fh.write(log_text)

    # ---- summary.json ----
    stats = _phase_stats(rows)
    anomalies = _detect_anomalies(stats, scenario)
    summary = {
        "scenario": {
            "id": sid,
            "name": scenario["name"],
            "service": scenario.get("service"),
            "description": scenario["description"],
        },
        "run": {
            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "total_ticks": total_ticks,
            "tick_sleep_s": tick_sleep,
            "wall_seconds": round(wall, 3),
            "phase_boundaries": {"pre": [0, pre_end],
                                 "fault": [pre_end, fault_end],
                                 "post": [fault_end, total_ticks]},
        },
        "phases": stats,
        "anomalies": anomalies,
        "expected_signals": scenario["expected_signals"],
        "root_cause_hints": scenario["root_cause_hints"],
        "log_signal_counts": _count_logs(log_text),
        "log_lines": len(state.logs),
    }
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    # ---- timeline.md ----
    _write_timeline(out_dir, scenario, rows, stats, anomalies, wall)

    return {
        "id": sid,
        "wall_seconds": round(wall, 3),
        "log_lines": len(state.logs),
        "anomalies": anomalies,
        "fault_phase": stats.get("fault", {}),
    }


def _write_timeline(out_dir, scenario, rows, stats, anomalies, wall):
    sid = scenario["id"]
    pre_end, fault_end = scenarios.phases(scenario)
    lines = []
    lines.append(f"# 故障演练时间线 — {sid}")
    lines.append("")
    lines.append(f"- 场景名称：{scenario['name']}")
    lines.append(f"- 影响组件：{scenario.get('service')}")
    lines.append(f"- 仿真规模：共 {scenario['total_ticks']} tick（1 tick = 1 仿真秒），"
                 f"实际墙钟耗时 {wall:.2f}s")
    lines.append(f"- 阶段划分：pre [0,{pre_end}) → fault [{pre_end},{fault_end}) → "
                 f"post [{fault_end},{scenario['total_ticks']})")
    lines.append("")
    lines.append(f"> {scenario['description']}")
    lines.append("")
    lines.append("## 关键指标采样（每 20 tick）")
    lines.append("")
    lines.append("| tick | phase | p95(ms) | p99(ms) | 5xx累计 | 连接池占用 | DB CPU% | 节点CPU% | 节点内存% | 磁盘% |inode% | 重启累计 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    step = max(1, scenario["total_ticks"] // 13)
    for row in rows[::step]:
        lines.append(
            "| {tick} | {phase} | {p95} | {p99} | {c5} | {pool} | {dbcpu} | {ncpu} | "
            "{nmem} | {ndisk} | {nino} | {rest} |".format(
                tick=row["tick"], phase=row["phase"],
                p95=f"{row['http_request_duration_ms_p95']:.0f}",
                p99=f"{row['http_request_duration_ms_p99']:.0f}",
                c5=f"{row['http_requests_total_5xx']:.0f}",
                pool=f"{row['app_conn_pool_in_use']:.0f}/{row['app_conn_pool_size']:.0f}",
                dbcpu=f"{row['db_cpu_percent']:.0f}",
                ncpu=f"{row['node_cpu_percent']:.0f}",
                nmem=f"{row['node_mem_percent']:.0f}",
                ndisk=f"{row['node_disk_percent']:.0f}",
                nino=f"{row['node_inode_percent']:.0f}",
                rest=f"{row['container_restarts_total']:.0f}",
            )
        )
    lines.append("")
    lines.append("## 阶段对比（实测均值 / 增量）")
    lines.append("")
    lines.append("| 指标 | pre | fault | post |")
    lines.append("|---|---|---|---|")
    for col in ["http_request_duration_ms_p95", "http_request_duration_ms_p99",
                "app_conn_pool_in_use", "db_cpu_percent", "db_connections_used",
                "node_cpu_percent", "node_mem_percent", "node_disk_percent",
                "node_inode_percent", "cache_latency_ms", "cache_hit_ratio"]:
        lines.append(f"| {col} | {stats['pre'].get(col, 0):.3f} | "
                     f"{stats['fault'].get(col, 0):.3f} | {stats['post'].get(col, 0):.3f} |")
    lines.append(f"| http_5xx_rate | {stats['pre'].get('http_5xx_rate', 0):.4f} | "
                 f"{stats['fault'].get('http_5xx_rate', 0):.4f} | "
                 f"{stats['post'].get('http_5xx_rate', 0):.4f} |")
    lines.append(f"| container_restarts_total (故障期增量) | - | "
                 f"{stats['fault'].get('container_restarts_total__delta', 0):.0f} | - |")
    lines.append(f"| db_slow_queries_total (故障期增量) | - | "
                 f"{stats['fault'].get('db_slow_queries_total__delta', 0):.0f} | - |")
    lines.append(f"| gateway_upstream_5xx_total (故障期增量) | - | "
                 f"{stats['fault'].get('gateway_upstream_5xx_total__delta', 0):.0f} | - |")
    lines.append("")
    lines.append("## 观测到的异常信号（基于实测数据判定）")
    lines.append("")
    if anomalies:
        for a in anomalies:
            lines.append(f"- {a}")
    else:
        lines.append("- （未检测到明显劣化）")
    lines.append("")
    lines.append("## 预期信号与根因线索")
    lines.append("")
    for s in scenario["expected_signals"]:
        lines.append(f"- [预期信号] {s}")
    for h in scenario["root_cause_hints"]:
        lines.append(f"- [根因线索] {h}")
    lines.append("")
    lines.append("## 取证文件")
    lines.append("")
    lines.append("- `metrics.csv`：逐 tick 时间序列（可直接入 Prometheus/CSV 分析）")
    lines.append("- `app.log`：真实风格应用/内核/SLB 日志")
    lines.append("- `summary.json`：pre/fault/post 关键指标快照、异常信号、根因线索")
    lines.append("")
    with open(os.path.join(out_dir, "timeline.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Accelerated fault-drill runner")
    parser.add_argument("--scenario", help="scenario id to run")
    parser.add_argument("--all", action="store_true", help="run all scenarios")
    parser.add_argument("--out", default="/workspace/cloud-support-lab/evidence",
                        help="output directory root")
    parser.add_argument("--export-scenarios", action="store_true",
                        help="only export the scenario catalogue to lab/scenarios/")
    args = parser.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    scenarios_dir = os.path.join(os.path.dirname(here), "scenarios")

    if args.export_scenarios:
        paths = scenarios.export_json(scenarios_dir)
        export_evidence = os.path.join(args.out, "_catalogue")
        scenarios.export_json(export_evidence)
        print(f"exported {len(paths)} scenario files to {scenarios_dir}")
        return

    # always (re)export the catalogue so lab/scenarios mirrors the source of truth
    scenarios.export_json(scenarios_dir)

    if args.all:
        ids = scenarios.ALL_IDS
    elif args.scenario:
        ids = [args.scenario]
    else:
        parser.error("specify --scenario <id> or --all")

    os.makedirs(args.out, exist_ok=True)
    results = []
    for sid in ids:
        scenario = scenarios.get(sid)
        print(f"[runner] running {sid} ({scenario['total_ticks']} ticks, "
              f"sleep {scenario.get('tick_sleep_s', 0.03)}s/tick) ...", flush=True)
        res = run_scenario(scenario, args.out)
        print(f"[runner]   done in {res['wall_seconds']}s, "
              f"log_lines={res['log_lines']}, anomalies={len(res['anomalies'])}",
              flush=True)
        results.append(res)

    print("\n=== 汇总 ===")
    for res in results:
        print(f"- {res['id']}: {res['wall_seconds']}s, {res['log_lines']} 行日志, "
              f"{len(res['anomalies'])} 条异常信号")
    out_index = os.path.join(args.out, "INDEX.json")
    with open(out_index, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                   "results": results}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"\n索引写入 {out_index}")


if __name__ == "__main__":
    main()
