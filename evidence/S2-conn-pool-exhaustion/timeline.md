# 故障演练时间线 — S2-conn-pool-exhaustion

- 场景名称：数据库连接池耗尽
- 影响组件：app
- 仿真规模：共 260 tick（1 tick = 1 仿真秒），实际墙钟耗时 7.93s
- 阶段划分：pre [0,40) → fault [40,170) → post [170,260)

> 应用侧数据库连接池被占满（连接泄漏或慢查询长期占用），新请求获取连接时排队直至超时。

## 关键指标采样（每 20 tick）

| tick | phase | p95(ms) | p99(ms) | 5xx累计 | 连接池占用 | DB CPU% | 节点CPU% | 节点内存% | 磁盘% |inode% | 重启累计 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | pre | 199 | 208 | 0 | 7/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 20 | pre | 190 | 201 | 0 | 8/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 40 | fault | 414 | 415 | 23 | 10/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 60 | fault | 414 | 415 | 517 | 10/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 80 | fault | 413 | 415 | 1018 | 10/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 100 | fault | 415 | 415 | 1502 | 10/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 120 | fault | 414 | 415 | 1999 | 10/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 140 | fault | 415 | 424 | 2475 | 10/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 160 | fault | 414 | 415 | 2972 | 10/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 180 | post | 176 | 196 | 3186 | 7/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 200 | post | 186 | 200 | 3186 | 7/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 220 | post | 174 | 196 | 3186 | 7/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 240 | post | 179 | 196 | 3186 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |

## 阶段对比（实测均值 / 增量）

| 指标 | pre | fault | post |
|---|---|---|---|
| http_request_duration_ms_p95 | 181.831 | 416.272 | 180.676 |
| http_request_duration_ms_p99 | 199.340 | 422.363 | 199.910 |
| app_conn_pool_in_use | 6.025 | 10.000 | 6.044 |
| db_cpu_percent | 22.000 | 22.000 | 22.000 |
| db_connections_used | 9.025 | 3.000 | 9.056 |
| node_cpu_percent | 25.000 | 25.000 | 25.000 |
| node_mem_percent | 42.000 | 42.000 | 42.000 |
| node_disk_percent | 55.000 | 55.000 | 55.000 |
| node_inode_percent | 30.000 | 30.000 | 30.000 |
| cache_latency_ms | 4.365 | 4.363 | 4.365 |
| cache_hit_ratio | 0.846 | 0.795 | 0.844 |
| http_5xx_rate | 0.0000 | 0.2054 | 0.0000 |
| container_restarts_total (故障期增量) | - | 0 | - |
| db_slow_queries_total (故障期增量) | - | 0 | - |
| gateway_upstream_5xx_total (故障期增量) | - | 3163 | - |

## 观测到的异常信号（基于实测数据判定）

- 5xx 错误率从 0.00% 升至 20.54%（故障期实测）
- P99 从 199ms 恶化到 422ms
- app_conn_pool_in_use=10/10（连接池打满）
- gateway_upstream_5xx_total 故障期新增 3163 次

## 预期信号与根因线索

- [预期信号] app_conn_pool_in_use 等于 app_conn_pool_size（打满）
- [预期信号] http_request_duration_ms_p99 抬升到连接超时量级
- [预期信号] http_requests_total{status="5xx"} 明显增加
- [预期信号] app 日志出现 HikariPool 'Connection is not available'
- [根因线索] Grafana 上 app_conn_pool_in_use / app_conn_pool_size 比值达到 100%
- [根因线索] 检查是否存在未关闭的连接（connection leak）或长事务
- [根因线索] 对照 MySQL Threads_connected / max_connections 确认 DB 侧是否也接近上限

## 取证文件

- `metrics.csv`：逐 tick 时间序列（可直接入 Prometheus/CSV 分析）
- `app.log`：真实风格应用/内核/SLB 日志
- `summary.json`：pre/fault/post 关键指标快照、异常信号、根因线索

