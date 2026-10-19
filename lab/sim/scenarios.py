"""Scenario catalogue for the cloud-support fault-drill lab.

This module is the single source of truth for the eight fault scenarios. It is
consumed by:

  * ``runner.py``   -- drives the accelerated in-process simulation, and
  * ``app.py``/``inject.py`` -- read fault parameters to degrade behaviour.

The catalogue is also exported to ``lab/scenarios/<id>.json`` by ``runner.py``
(with the standard library ``json`` module) so humans can read it.

All numeric values here are *fault parameters* (targets / rates), NOT observed
metric values. Observed values are produced only by real simulation runs and
are written to ``evidence/``.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# BASELINE: healthy-state fault parameters. A scenario overrides a subset of
# these during its "fault" window.
# --------------------------------------------------------------------------
BASELINE: dict = {
    # --- load / worker pool ------------------------------------------------
    "rps": 120.0,                # client requests per simulated second
    "app_workers": 16,           # app worker-pool threads
    "timeout_ms": 600.0,         # request deadline before the app gives up
    # --- app -> db connection pool ----------------------------------------
    "conn_pool_size": 10,        # Hikari-style pool upper bound
    "leaked_conns": 0,           # connections checked out and never returned
    "conn_timeout_ms": 400.0,    # wait before "connection is not available"
    # --- node resources ----------------------------------------------------
    "node_cpu_target": 25.0,     # %
    "cpu_service_mult": 3.2,     # extra service-time multiplier at 100% cpu
    "node_mem_target": 42.0,     # %
    "mem_leak_rate": 0.0,        # %/tick growth (memory leak)
    "oom_enabled": False,        # OOMKill when memory crosses 100%
    "mem_cap": 100.0,
    "node_disk_target": 55.0,    # %
    "disk_fill_rate": 0.0,       # %/tick growth
    "node_inode_target": 30.0,   # %
    "inode_fill_rate": 0.0,      # %/tick growth
    "fs_cap": 100.0,
    # --- downstream database ----------------------------------------------
    "db_base_ms": 150.0,         # cold-path query cost on a cache miss
    "db_cpu_target": 22.0,       # %
    "db_slow_query_rate": 0.0,   # probability a query is a slow query
    "db_slow_query_ms": 0.0,     # extra cost of a slow query
    "db_max_conns": 100,
    # --- gateway / SLB -----------------------------------------------------
    "healthcheck_fail": False,   # /healthz returns 500 -> backend unhealthy
    "lb_drop_ratio": 0.0,        # fraction of client calls dropped at the SLB
    # --- DNS ---------------------------------------------------------------
    "dns_fail_rate": 0.0,        # probability of resolver failure per request
    "dns_timeout_ms": 340.0,     # resolver timeout cost
    # --- cache (redis) -----------------------------------------------------
    "cache_base_ms": 3.0,
    "cache_hit_ratio": 0.85,
    "cache_hotkey": False,       # hot-key / big-key latency spikes
    "cache_hotkey_ms": 220.0,
    "cache_hotkey_prob": 0.6,    # fraction of lookups hitting the hot key
    "cache_timeout_rate": 0.0,   # probability the cache client times out
    "cache_timeout_ms": 520.0,
    # --- app overhead ------------------------------------------------------
    "request_base_ms": 6.0,
}

# --------------------------------------------------------------------------
# Scenario definitions
# --------------------------------------------------------------------------
SCENARIOS: dict = {
    "S1-app-cpu-saturation": {
        "id": "S1-app-cpu-saturation",
        "name": "应用实例 CPU 打满",
        "short": "app-cpu",
        "service": "app",
        "description": "业务实例 CPU 被占满（死循环/批量任务），单实例处理能力下降，"
                       "接口延迟升高并出现部分请求超时。",
        "total_ticks": 260,
        "pre_ticks": 40,
        "fault_ticks": 130,          # fault window = [40, 170)
        "tick_sleep_s": 0.03,
        "fault_params": {
            "node_cpu_target": 98.0,
            "cpu_service_mult": 3.4,
        },
        "expected_signals": [
            "node_cpu_percent 逼近 100",
            "http_request_duration_ms_p95/p99 显著升高",
            "app_worker_busy 达到 worker 池上限",
            "http_requests_total{status=\"5xx\"} 出现少量超时",
        ],
        "root_cause_hints": [
            "确认是否单实例 CPU 打满（top / cAdvisor node_cpu_percent 接近 100）",
            "结合 app 日志中的 worker pool saturated / request timeout",
            "排查是否有死循环、定时批量任务或流量突增导致单实例过载",
        ],
    },
    "S2-conn-pool-exhaustion": {
        "id": "S2-conn-pool-exhaustion",
        "name": "数据库连接池耗尽",
        "short": "conn-pool",
        "service": "app",
        "description": "应用侧数据库连接池被占满（连接泄漏或慢查询长期占用），"
                       "新请求获取连接时排队直至超时。",
        "total_ticks": 260,
        "pre_ticks": 40,
        "fault_ticks": 130,
        "tick_sleep_s": 0.03,
        "fault_params": {
            "leaked_conns": 10,          # == conn_pool_size -> pool fully drained
            "cache_hit_ratio": 0.80,     # more requests need a real DB connection
            "conn_timeout_ms": 400.0,
        },
        "expected_signals": [
            "app_conn_pool_in_use 等于 app_conn_pool_size（打满）",
            "http_request_duration_ms_p99 抬升到连接超时量级",
            "http_requests_total{status=\"5xx\"} 明显增加",
            "app 日志出现 HikariPool 'Connection is not available'",
        ],
        "root_cause_hints": [
            "Grafana 上 app_conn_pool_in_use / app_conn_pool_size 比值达到 100%",
            "检查是否存在未关闭的连接（connection leak）或长事务",
            "对照 MySQL Threads_connected / max_connections 确认 DB 侧是否也接近上限",
        ],
    },
    "S3-slow-sql-db-cpu": {
        "id": "S3-slow-sql-db-cpu",
        "name": "慢 SQL / 缺失索引导致 DB CPU 飙升",
        "short": "slow-sql",
        "service": "db",
        "description": "新上线的接口走了缺失索引的 SQL，数据库 CPU 飙升，慢查询堆积，"
                       "业务侧 P99 恶化。",
        "total_ticks": 260,
        "pre_ticks": 40,
        "fault_ticks": 130,
        "tick_sleep_s": 0.03,
        "fault_params": {
            "db_cpu_target": 96.0,
            "db_slow_query_rate": 0.15,
            "db_slow_query_ms": 1200.0,
            "db_base_ms": 180.0,
        },
        "expected_signals": [
            "db_cpu_percent 逼近 100",
            "db_slow_queries_total 持续增长",
            "http_request_duration_ms_p99 进入秒级",
            "db_connections_used 升高（慢查询占用连接）",
        ],
        "root_cause_hints": [
            "慢查询日志确认具体 SQL（慢查询 took 数千毫秒）",
            "用 EXPLAIN 检查是否走了缺失索引导致全表扫描",
            "对照 db_cpu_percent 与 QPS，评估是否加索引/改写 SQL",
        ],
    },
    "S4-memory-leak-oomkill": {
        "id": "S4-memory-leak-oomkill",
        "name": "内存泄漏导致容器 OOMKilled",
        "short": "oom",
        "service": "app",
        "description": "应用存在内存泄漏，RSS 持续增长，超过 limit 后被内核 OOMKilled，"
                       "容器反复重启形成锯齿状内存曲线。",
        "total_ticks": 260,
        "pre_ticks": 40,
        "fault_ticks": 150,
        "tick_sleep_s": 0.03,
        "fault_params": {
            "mem_leak_rate": 1.7,        # %/tick -> crosses 100% in ~34 ticks
            "oom_enabled": True,
        },
        "expected_signals": [
            "node_mem_percent 反复逼近 100 后骤降（锯齿）",
            "container_restarts_total 递增",
            "重启瞬间出现少量 5xx",
            "app 日志出现 'Out of memory: Killed process' / 'OOMKilled'",
        ],
        "root_cause_hints": [
            "cAdvisor / kubelet 事件里 OOMKilled 与 restartCount 增加",
            "dmesg / app 日志定位被 kill 的进程与分配点",
            "结合 heap 分析确认内存泄漏源；必要时先抬高 limit 再定位",
        ],
    },
    "S5-lb-healthcheck-5xx": {
        "id": "S5-lb-healthcheck-5xx",
        "name": "负载均衡后端健康检查失败",
        "short": "healthcheck",
        "service": "slb",
        "description": "SLB 健康检查探针失败，后端被摘除，网关大量返回 502/504，"
                       "但后端实例本身资源指标正常。",
        "total_ticks": 240,
        "pre_ticks": 40,
        "fault_ticks": 120,
        "tick_sleep_s": 0.03,
        "fault_params": {
            "healthcheck_fail": True,
            "lb_drop_ratio": 0.5,        # half of client calls never reach app
        },
        "expected_signals": [
            "gateway_upstream_5xx_total 大幅增长",
            "业务接口到达量（http_requests_total）下降",
            "app 自身 node_cpu/mem 正常",
            "nginx 日志出现 upstream timed out / 502 / 504",
        ],
        "root_cause_hints": [
            "检查 SLB 健康检查配置与后端 /healthz 实际返回码",
            "对比后端资源指标（正常）与网关 5xx（异常）锁定在接入层",
            "确认健康检查路径/超时/阈值是否与业务行为一致",
        ],
    },
    "S6-disk-inode-full": {
        "id": "S6-disk-inode-full",
        "name": "磁盘空间 / inode 打满",
        "short": "disk-full",
        "service": "node",
        "description": "日志或临时文件写满磁盘/inode，应用写入失败并开始堆积，"
                       "容器内 'No space left on device'。",
        "total_ticks": 260,
        "pre_ticks": 40,
        "fault_ticks": 140,
        "tick_sleep_s": 0.03,
        "fault_params": {
            "disk_fill_rate": 0.62,
            "inode_fill_rate": 0.70,
        },
        "expected_signals": [
            "node_disk_percent 接近 100",
            "node_inode_percent 接近 100",
            "http_requests_total{status=\"5xx\"} 上升（写入失败）",
            "app 日志出现 'No space left on device'",
        ],
        "root_cause_hints": [
            "df -h / df -i 确认是空间还是 inode 打满",
            "定位占满来源（日志轮转失效 / 大量小文件 / 临时目录）",
            "清理并修复 logrotate，或扩容/挂载独立磁盘",
        ],
    },
    "S7-dns-resolution-fail": {
        "id": "S7-dns-resolution-fail",
        "name": "DNS 解析异常",
        "short": "dns",
        "service": "network",
        "description": "集群 DNS/CoreDNS 解析异常，下游域名间歇性解析超时，"
                       "业务出现间歇性 5xx 与连接超时。",
        "total_ticks": 260,
        "pre_ticks": 40,
        "fault_ticks": 130,
        "tick_sleep_s": 0.03,
        "fault_params": {
            "dns_fail_rate": 0.30,
            "dns_timeout_ms": 340.0,
        },
        "expected_signals": [
            "http_requests_total{status=\"5xx\"} 间歇性升高（约 30%）",
            "http_request_duration_ms_p99 抬升到解析超时量级",
            "app 日志出现 'nameserver ... timeout' / 'getaddrinfo ENOTFOUND'",
            "节点资源指标正常",
        ],
        "root_cause_hints": [
            "确认 CoreDNS / resolv.conf 与 ndots 配置",
            "对比失败请求的耗时分布是否卡在解析阶段（解析超时 3s）",
            "排查 DNS 上游不可达或 search domain 导致的解析放大",
        ],
    },
    "S8-redis-hotkey": {
        "id": "S8-redis-hotkey",
        "name": "Redis 大 key / 热 key 导致缓存抖动",
        "short": "redis-hotkey",
        "service": "cache",
        "description": "某个热 key/大 key 被高频访问，单分片 CPU/网络被打满，"
                       "缓存层响应抖动、部分请求超时，回源压力上升。",
        "total_ticks": 260,
        "pre_ticks": 40,
        "fault_ticks": 130,
        "tick_sleep_s": 0.03,
        "fault_params": {
            "cache_hotkey": True,
            "cache_hotkey_ms": 120.0,
            "cache_hotkey_prob": 0.5,
            "cache_timeout_rate": 0.06,
            "cache_hit_ratio": 0.75,
            "db_cpu_target": 45.0,
        },
        "expected_signals": [
            "cache_latency_ms 显著升高",
            "cache_hit_ratio 下降",
            "http_request_duration_ms_p99 抬升",
            "app 日志出现 Redis command timed out / hot key",
        ],
        "root_cause_hints": [
            "redis-cli --hotkeys / --bigkeys 定位热 key 与大 key",
            "确认是否同一 key 被集中在单分片（分片倾斜）",
            "对热 key 做本地缓存/打散，或对大 key 拆分",
        ],
    },
}

# Scenarios that are obviously storage/network/K8s level, used by the runner to
# tag the evidence tree.
ALL_IDS = list(SCENARIOS.keys())


def get(scenario_id: str) -> dict:
    return SCENARIOS[scenario_id]


def phases(scenario: dict):
    """Return (pre_end, fault_end) tick boundaries for a scenario."""
    pre_end = int(scenario["pre_ticks"])
    fault_end = pre_end + int(scenario["fault_ticks"])
    return pre_end, fault_end


def phase_of(scenario: dict, tick: int) -> str:
    pre_end, fault_end = phases(scenario)
    if tick < pre_end:
        return "pre"
    if tick < fault_end:
        return "fault"
    return "post"


def params_at(scenario: dict, tick: int) -> dict:
    """Fault parameters active at ``tick`` (BASELINE merged with overrides)."""
    params = dict(BASELINE)
    if phase_of(scenario, tick) == "fault":
        params.update(scenario["fault_params"])
    return params


def export_json(out_dir) -> list:
    """Write one readable JSON file per scenario into ``out_dir`` (stdlib only).

    Returns the list of written paths.
    """
    import json
    import os
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for sid, scenario in SCENARIOS.items():
        payload = dict(scenario)
        payload["baseline_params"] = BASELINE
        payload["phase_boundaries"] = {
            "pre": [0, scenario["pre_ticks"]],
            "fault": [scenario["pre_ticks"],
                      scenario["pre_ticks"] + scenario["fault_ticks"]],
            "post": [scenario["pre_ticks"] + scenario["fault_ticks"],
                     scenario["total_ticks"]],
        }
        path = os.path.join(out_dir, f"{sid}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=False)
            fh.write("\n")
        written.append(path)
    return written
