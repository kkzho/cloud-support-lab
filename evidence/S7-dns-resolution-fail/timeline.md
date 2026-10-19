# 故障演练时间线 — S7-dns-resolution-fail

- 场景名称：DNS 解析异常
- 影响组件：network
- 仿真规模：共 260 tick（1 tick = 1 仿真秒），实际墙钟耗时 7.92s
- 阶段划分：pre [0,40) → fault [40,170) → post [170,260)

> 集群 DNS/CoreDNS 解析异常，下游域名间歇性解析超时，业务出现间歇性 5xx 与连接超时。

## 关键指标采样（每 20 tick）

| tick | phase | p95(ms) | p99(ms) | 5xx累计 | 连接池占用 | DB CPU% | 节点CPU% | 节点内存% | 磁盘% |inode% | 重启累计 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | pre | 175 | 186 | 0 | 4/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 20 | pre | 178 | 200 | 0 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 40 | fault | 355 | 360 | 29 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 60 | fault | 516 | 552 | 750 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 80 | fault | 456 | 481 | 1468 | 3/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 100 | fault | 462 | 484 | 2261 | 3/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 120 | fault | 454 | 496 | 3030 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 140 | fault | 431 | 473 | 3725 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 160 | fault | 410 | 436 | 4426 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 180 | post | 191 | 204 | 4745 | 4/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 200 | post | 167 | 203 | 4745 | 4/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 220 | post | 179 | 199 | 4745 | 6/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 240 | post | 188 | 200 | 4745 | 4/10 | 22 | 25 | 42 | 55 | 30 | 0 |

## 阶段对比（实测均值 / 增量）

| 指标 | pre | fault | post |
|---|---|---|---|
| http_request_duration_ms_p95 | 179.754 | 411.686 | 181.157 |
| http_request_duration_ms_p99 | 199.275 | 435.310 | 199.313 |
| app_conn_pool_in_use | 6.100 | 4.431 | 5.889 |
| db_cpu_percent | 22.000 | 22.000 | 22.000 |
| db_connections_used | 9.100 | 7.431 | 8.900 |
| node_cpu_percent | 25.000 | 25.000 | 25.000 |
| node_mem_percent | 42.000 | 42.000 | 42.000 |
| node_disk_percent | 55.000 | 55.000 | 55.000 |
| node_inode_percent | 30.000 | 30.000 | 30.000 |
| cache_latency_ms | 4.362 | 4.359 | 4.360 |
| cache_hit_ratio | 0.852 | 0.848 | 0.847 |
| http_5xx_rate | 0.0000 | 0.3014 | 0.0000 |
| container_restarts_total (故障期增量) | - | 0 | - |
| db_slow_queries_total (故障期增量) | - | 0 | - |
| gateway_upstream_5xx_total (故障期增量) | - | 4716 | - |

## 观测到的异常信号（基于实测数据判定）

- 5xx 错误率从 0.00% 升至 30.14%（故障期实测）
- P99 从 199ms 恶化到 435ms
- app_worker_busy 升至 17（worker 池接近饱和）
- gateway_upstream_5xx_total 故障期新增 4716 次

## 预期信号与根因线索

- [预期信号] http_requests_total{status="5xx"} 间歇性升高（约 30%）
- [预期信号] http_request_duration_ms_p99 抬升到解析超时量级
- [预期信号] app 日志出现 'nameserver ... timeout' / 'getaddrinfo ENOTFOUND'
- [预期信号] 节点资源指标正常
- [根因线索] 确认 CoreDNS / resolv.conf 与 ndots 配置
- [根因线索] 对比失败请求的耗时分布是否卡在解析阶段（解析超时 3s）
- [根因线索] 排查 DNS 上游不可达或 search domain 导致的解析放大

## 取证文件

- `metrics.csv`：逐 tick 时间序列（可直接入 Prometheus/CSV 分析）
- `app.log`：真实风格应用/内核/SLB 日志
- `summary.json`：pre/fault/post 关键指标快照、异常信号、根因线索

