# cloud-support-lab — 三层业务故障演练仿真环境

一个**纯 Python 标准库**实现的“公有云三层业务”最小可运行仿真栈，用于云厂商售后技术支持
的故障演练与取证。它可以被注入 8 类故障，导出 Prometheus 指标，产生真实风格日志，并
把每个场景的实测数据落盘到 `evidence/`。

> 仅使用 Python 标准库（`http.server` / `json` / `csv` / `threading` / `time` / `heapq`）。
> 不依赖 `pyyaml`、`requests`、`prometheus_client` 等任何第三方包。

---

## 一、三种运行模式

### 模式 ⓿：跟着 Notebook 一步步做（最推荐，尤其新手）

```bash
jupyter lab notebooks/cloud-fault-drill.ipynb
```

`notebooks/cloud-fault-drill.ipynb` 是一份**渐进式实验手册**：从 Lab 0 健康基线开始，
逐个复现 8 类故障（CPU 打满 / 连接池耗尽 / 慢 SQL / 内存泄漏 OOM / SLB 健康检查 502 /
磁盘 inode / DNS / Redis 热 key），每个 Lab 都包含「原理 → 注入 → 观察 → 对答案 → 恢复验证
→ 面试一句话」，最后还有盲测、值班 SOP 与 80 分自评卡。它会自动拉起/回收下面的仿真进程，
不需要记命令。

### 模式 ①：沙箱内纯进程仿真（无需 docker，推荐）

一次 tick 代表 **1 个仿真秒**，但实际只 `sleep 0.03s`，所以一个 240~300 tick 的场景
十几秒内即可跑完。指标与日志全部来自真实运行。

```bash
# 跑全部 8 个场景，产出到 <repo>/evidence/
python3 lab/sim/runner.py --all --out /workspace/cloud-support-lab/evidence

# 只跑单个场景
python3 lab/sim/runner.py --scenario S2-conn-pool-exhaustion --out /workspace/cloud-support-lab/evidence

# 便捷脚本
lab/scripts/run_all.sh
lab/scripts/run_scenario.sh S3-slow-sql-db-cpu

# 把场景目录导出成可读 JSON（lab/scenarios/<id>.json）
python3 lab/sim/runner.py --export-scenarios
```

也可以用真实 HTTP 方式启动应用本体（演示 /healthz、业务接口与 /metrics）。
**默认开启 tick-loop**：以 1 仿真秒 = 1 真实秒 循环播放场景时间线，`/metrics` 会实时变化，
可直接被 Prometheus 抓取；加 `--no-tick-loop` 则只返回静态指标。

```bash
python3 lab/sim/app.py --port 8080                    # 健康态（live metrics）
python3 lab/sim/app.py --port 8080 --scenario S5-lb-healthcheck-5xx   # 预置故障（循环播放）
python3 lab/sim/app.py --port 8080 --tick-period 0.2  # 加快节奏
curl -s localhost:8080/healthz
curl -s localhost:8080/api/order/list
curl -s localhost:8080/metrics
```

> Mac (Apple Silicon / arm64) 本地完整实操（含 Docker 全栈）见
> [`docs/06-Mac本地实操指南.md`](../docs/06-Mac本地实操指南.md)。

### 模式 ②：本地 Docker 真实编排

`lab/docker-compose.yml` 提供了一个“真·可运行”版本（haproxy + nginx + app + redis + mysql +
prometheus + grafana + alertmanager + nginx-exporter + loadgen）。**在有 Docker 的本机执行**，
且前提是 Docker daemon 已启动（`docker info` 能正常输出）：

```bash
# 0) 先确认 daemon 在跑；Mac 上通常是 Docker Desktop 没开
docker info >/dev/null && echo "docker OK"

cd lab && docker compose up -d
# Grafana:      http://localhost:3000  (admin/admin)
# Prometheus:   http://localhost:9090
# Alertmanager: http://localhost:9093
# app:          http://localhost:8080/healthz  /metrics  /api/order/list
```

沙箱内没有 docker daemon，因此该文件只作为可移植的真实版编排（不在此环境启动）。

---

## 二、目录结构

```
lab/
├── sim/
│   ├── __init__.py
│   ├── app.py        # 真实 HTTP 应用 + 离散事件仿真引擎（worker池/连接池/DB/缓存/节点资源）
│   ├── metrics.py    # Prometheus 文本格式指标注册表（stdlib）
│   ├── inject.py     # 故障参数注入 + 真实风格日志模板
│   ├── scenarios.py  # 8 个场景目录（唯一真源）
│   └── runner.py     # 加速运行器：--scenario / --all / --out
├── scenarios/        # 由 scenarios.py 导出的 <id>.json（可读）
├── scripts/
│   ├── run_all.sh
│   └── run_scenario.sh
├── docker-compose.yml
└── README.md
```

配套 `ops/`：

```
ops/
├── prometheus/prometheus.yml   # scrape 配置
├── prometheus/rules.yml        # 8 个场景告警规则（for / severity / annotations）
├── alertmanager/alertmanager.yml  # 分级路由 + 分组 + 告警抑制
└── grafana/dashboard.json      # 可导入看板
```

---

## 三、8 个故障场景

| id | 名称 | 影响组件 | 关键劣化信号 |
|---|---|---|---|
| S1-app-cpu-saturation | 应用实例 CPU 打满 | app | node_cpu→~98%，p95/p99 升高，部分超时 |
| S2-conn-pool-exhaustion | 数据库连接池耗尽 | app | app_conn_pool_in_use 打满，连接超时 5xx |
| S3-slow-sql-db-cpu | 慢 SQL / 缺失索引 | db | db_cpu 飙升，db_slow_queries_total 增长，P99 秒级 |
| S4-memory-leak-oomkill | 内存泄漏 → OOMKilled | app | node_mem 锯齿逼近 100%，container_restarts 递增 |
| S5-lb-healthcheck-5xx | 负载均衡健康检查失败 | slb | gateway_upstream_5xx 激增，到达量下降 |
| S6-disk-inode-full | 磁盘/inode 打满 | node | node_disk/node_inode→100%，写入失败 5xx |
| S7-dns-resolution-fail | DNS 解析异常 | network | 间歇性 5xx（~30%），P99 升高，节点资源正常 |
| S8-redis-hotkey | Redis 大 key/热 key | cache | cache_latency 飙升，命中率下降 |

---

## 四、产物说明（每个场景写入 `evidence/<id>/`）

| 文件 | 内容 |
|---|---|
| `metrics.csv` | 逐 tick 关键指标时间序列（含全部 18 个指标） |
| `app.log` | 真实风格应用/内核/SLB 日志（HikariPool、No space left、OOMKilled、upstream timed out、nameserver timeout、slow query…） |
| `summary.json` | pre/fault/post 三阶段关键指标快照 + 观测到的异常信号 + 根因线索 |
| `timeline.md` | 人类可读时间线（采样表 + 阶段对比 + 异常/预期信号/根因） |

`evidence/INDEX.json` 汇总每个场景的运行耗时与异常条数。

---

## 五、指标清单（`/metrics` 与 `metrics.csv` 同名）

`http_requests_total{status="2xx|5xx"}`、`http_request_duration_ms_p50|p95|p99`、
`app_worker_busy`、`app_conn_pool_in_use`、`app_conn_pool_size`、
`db_connections_used`、`db_connections_max`、`db_cpu_percent`、
`db_slow_queries_total`、`node_cpu_percent`、`node_mem_percent`、
`node_disk_percent`、`node_inode_percent`、`cache_latency_ms`、
`cache_hit_ratio`、`container_restarts_total`、`gateway_upstream_5xx_total`。

> 所有 evidence 中的数字均来自真实运行输出，运行器不会硬编码任何指标值。
