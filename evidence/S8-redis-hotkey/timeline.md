# 故障演练时间线 — S8-redis-hotkey

- 场景名称：Redis 大 key / 热 key 导致缓存抖动
- 影响组件：cache
- 仿真规模：共 260 tick（1 tick = 1 仿真秒），实际墙钟耗时 7.93s
- 阶段划分：pre [0,40) → fault [40,170) → post [170,260)

> 某个热 key/大 key 被高频访问，单分片 CPU/网络被打满，缓存层响应抖动、部分请求超时，回源压力上升。

## 关键指标采样（每 20 tick）

| tick | phase | p95(ms) | p99(ms) | 5xx累计 | 连接池占用 | DB CPU% | 节点CPU% | 节点内存% | 磁盘% |inode% | 重启累计 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | pre | 191 | 208 | 0 | 10/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 20 | pre | 193 | 204 | 0 | 6/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 40 | fault | 313 | 530 | 3 | 7/10 | 34 | 25 | 42 | 55 | 30 | 0 |
| 60 | fault | 530 | 673 | 143 | 7/10 | 45 | 25 | 42 | 55 | 30 | 0 |
| 80 | fault | 613 | 720 | 290 | 8/10 | 45 | 25 | 42 | 55 | 30 | 0 |
| 100 | fault | 528 | 798 | 427 | 8/10 | 45 | 25 | 42 | 55 | 30 | 0 |
| 120 | fault | 314 | 529 | 559 | 8/10 | 45 | 25 | 42 | 55 | 30 | 0 |
| 140 | fault | 337 | 679 | 714 | 9/10 | 45 | 25 | 42 | 55 | 30 | 0 |
| 160 | fault | 384 | 546 | 830 | 7/10 | 45 | 25 | 42 | 55 | 30 | 0 |
| 180 | post | 189 | 206 | 886 | 7/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 200 | post | 166 | 190 | 886 | 4/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 220 | post | 187 | 203 | 886 | 6/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 240 | post | 194 | 206 | 886 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |

## 阶段对比（实测均值 / 增量）

| 指标 | pre | fault | post |
|---|---|---|---|
| http_request_duration_ms_p95 | 181.967 | 463.476 | 177.503 |
| http_request_duration_ms_p99 | 199.675 | 628.600 | 198.963 |
| app_conn_pool_in_use | 5.775 | 7.592 | 5.744 |
| db_cpu_percent | 22.000 | 44.823 | 22.256 |
| db_connections_used | 8.800 | 10.608 | 8.744 |
| node_cpu_percent | 25.000 | 25.000 | 25.000 |
| node_mem_percent | 42.000 | 42.000 | 42.000 |
| node_disk_percent | 55.000 | 55.000 | 55.000 |
| node_inode_percent | 30.000 | 30.000 | 30.000 |
| cache_latency_ms | 4.364 | 369.283 | 4.355 |
| cache_hit_ratio | 0.852 | 0.754 | 0.851 |
| http_5xx_rate | 0.0000 | 0.0577 | 0.0000 |
| container_restarts_total (故障期增量) | - | 0 | - |
| db_slow_queries_total (故障期增量) | - | 0 | - |
| gateway_upstream_5xx_total (故障期增量) | - | 883 | - |

## 观测到的异常信号（基于实测数据判定）

- 5xx 错误率从 0.00% 升至 5.77%（故障期实测）
- P99 从 200ms 恶化到 629ms
- app_worker_busy 升至 17（worker 池接近饱和）
- cache_latency_ms 从 4.4ms 升至 369.3ms
- gateway_upstream_5xx_total 故障期新增 883 次

## 预期信号与根因线索

- [预期信号] cache_latency_ms 显著升高
- [预期信号] cache_hit_ratio 下降
- [预期信号] http_request_duration_ms_p99 抬升
- [预期信号] app 日志出现 Redis command timed out / hot key
- [根因线索] redis-cli --hotkeys / --bigkeys 定位热 key 与大 key
- [根因线索] 确认是否同一 key 被集中在单分片（分片倾斜）
- [根因线索] 对热 key 做本地缓存/打散，或对大 key 拆分

## 取证文件

- `metrics.csv`：逐 tick 时间序列（可直接入 Prometheus/CSV 分析）
- `app.log`：真实风格应用/内核/SLB 日志
- `summary.json`：pre/fault/post 关键指标快照、异常信号、根因线索

