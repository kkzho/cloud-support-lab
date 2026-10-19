# 故障演练时间线 — S1-app-cpu-saturation

- 场景名称：应用实例 CPU 打满
- 影响组件：app
- 仿真规模：共 260 tick（1 tick = 1 仿真秒），实际墙钟耗时 7.93s
- 阶段划分：pre [0,40) → fault [40,170) → post [170,260)

> 业务实例 CPU 被占满（死循环/批量任务），单实例处理能力下降，接口延迟升高并出现部分请求超时。

## 关键指标采样（每 20 tick）

| tick | phase | p95(ms) | p99(ms) | 5xx累计 | 连接池占用 | DB CPU% | 节点CPU% | 节点内存% | 磁盘% |inode% | 重启累计 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | pre | 166 | 201 | 0 | 6/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 20 | pre | 188 | 195 | 0 | 4/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 40 | fault | 193 | 205 | 0 | 8/10 | 22 | 62 | 42 | 55 | 30 | 0 |
| 60 | fault | 849 | 1110 | 332 | 10/10 | 22 | 98 | 42 | 55 | 30 | 0 |
| 80 | fault | 744 | 798 | 660 | 10/10 | 22 | 98 | 42 | 55 | 30 | 0 |
| 100 | fault | 745 | 801 | 942 | 10/10 | 22 | 98 | 42 | 55 | 30 | 0 |
| 120 | fault | 724 | 832 | 1275 | 10/10 | 22 | 98 | 42 | 55 | 30 | 0 |
| 140 | fault | 683 | 813 | 1609 | 9/10 | 22 | 98 | 42 | 55 | 30 | 0 |
| 160 | fault | 753 | 846 | 1915 | 10/10 | 22 | 98 | 42 | 55 | 30 | 0 |
| 180 | post | 168 | 179 | 2060 | 4/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 200 | post | 185 | 205 | 2060 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 220 | post | 190 | 197 | 2060 | 6/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 240 | post | 187 | 204 | 2060 | 6/10 | 22 | 25 | 42 | 55 | 30 | 0 |

## 阶段对比（实测均值 / 增量）

| 指标 | pre | fault | post |
|---|---|---|---|
| http_request_duration_ms_p95 | 178.791 | 814.370 | 178.097 |
| http_request_duration_ms_p99 | 198.476 | 951.493 | 198.276 |
| app_conn_pool_in_use | 5.900 | 9.815 | 5.700 |
| db_cpu_percent | 22.000 | 22.000 | 22.000 |
| db_connections_used | 8.900 | 13.608 | 8.700 |
| node_cpu_percent | 25.000 | 97.439 | 25.811 |
| node_mem_percent | 42.000 | 42.000 | 42.000 |
| node_disk_percent | 55.000 | 55.000 | 55.000 |
| node_inode_percent | 30.000 | 30.000 | 30.000 |
| cache_latency_ms | 4.349 | 4.362 | 4.364 |
| cache_hit_ratio | 0.848 | 0.852 | 0.851 |
| http_5xx_rate | 0.0000 | 0.1321 | 0.0000 |
| container_restarts_total (故障期增量) | - | 0 | - |
| db_slow_queries_total (故障期增量) | - | 0 | - |
| gateway_upstream_5xx_total (故障期增量) | - | 2060 | - |

## 观测到的异常信号（基于实测数据判定）

- 5xx 错误率从 0.00% 升至 13.21%（故障期实测）
- P99 从 198ms 恶化到 951ms
- node_cpu_percent 达 97.4%
- app_worker_busy 升至 17（worker 池接近饱和）
- app_conn_pool_in_use=10/10（连接池打满）
- gateway_upstream_5xx_total 故障期新增 2060 次

## 预期信号与根因线索

- [预期信号] node_cpu_percent 逼近 100
- [预期信号] http_request_duration_ms_p95/p99 显著升高
- [预期信号] app_worker_busy 达到 worker 池上限
- [预期信号] http_requests_total{status="5xx"} 出现少量超时
- [根因线索] 确认是否单实例 CPU 打满（top / cAdvisor node_cpu_percent 接近 100）
- [根因线索] 结合 app 日志中的 worker pool saturated / request timeout
- [根因线索] 排查是否有死循环、定时批量任务或流量突增导致单实例过载

## 取证文件

- `metrics.csv`：逐 tick 时间序列（可直接入 Prometheus/CSV 分析）
- `app.log`：真实风格应用/内核/SLB 日志
- `summary.json`：pre/fault/post 关键指标快照、异常信号、根因线索

