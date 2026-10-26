# 故障演练时间线 — S6-disk-inode-full

- 场景名称：磁盘空间 / inode 打满
- 影响组件：node
- 仿真规模：共 260 tick（1 tick = 1 仿真秒），实际墙钟耗时 7.92s
- 阶段划分：pre [0,40) → fault [40,180) → post [180,260)

> 日志或临时文件写满磁盘/inode，应用写入失败并开始堆积，容器内 'No space left on device'。

## 关键指标采样（每 20 tick）

| tick | phase | p95(ms) | p99(ms) | 5xx累计 | 连接池占用 | DB CPU% | 节点CPU% | 节点内存% | 磁盘% |inode% | 重启累计 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | pre | 171 | 202 | 0 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 20 | pre | 186 | 196 | 0 | 7/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 40 | fault | 188 | 200 | 0 | 7/10 | 22 | 25 | 42 | 56 | 31 | 0 |
| 60 | fault | 160 | 182 | 0 | 5/10 | 22 | 25 | 42 | 68 | 45 | 0 |
| 80 | fault | 178 | 199 | 0 | 7/10 | 22 | 25 | 42 | 80 | 59 | 0 |
| 100 | fault | 179 | 205 | 0 | 6/10 | 22 | 25 | 42 | 93 | 73 | 0 |
| 120 | fault | 184 | 207 | 357 | 5/10 | 22 | 25 | 42 | 100 | 87 | 0 |
| 140 | fault | 190 | 206 | 1110 | 7/10 | 22 | 25 | 42 | 100 | 100 | 0 |
| 160 | fault | 187 | 206 | 1821 | 9/10 | 22 | 25 | 42 | 100 | 100 | 0 |
| 180 | post | 163 | 204 | 2510 | 6/10 | 22 | 25 | 42 | 82 | 72 | 0 |
| 200 | post | 176 | 197 | 2510 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 220 | post | 180 | 202 | 2510 | 5/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 240 | post | 180 | 195 | 2510 | 6/10 | 22 | 25 | 42 | 55 | 30 | 0 |

## 阶段对比（实测均值 / 增量）

| 指标 | pre | fault | post |
|---|---|---|---|
| http_request_duration_ms_p95 | 177.862 | 178.296 | 179.558 |
| http_request_duration_ms_p99 | 197.828 | 198.849 | 198.826 |
| app_conn_pool_in_use | 5.825 | 5.850 | 5.963 |
| db_cpu_percent | 22.000 | 22.000 | 22.000 |
| db_connections_used | 8.825 | 8.857 | 8.963 |
| node_cpu_percent | 25.000 | 25.000 | 25.000 |
| node_mem_percent | 42.000 | 42.000 | 42.000 |
| node_disk_percent | 55.000 | 88.495 | 55.844 |
| node_inode_percent | 30.000 | 75.250 | 31.312 |
| cache_latency_ms | 4.369 | 4.368 | 4.360 |
| cache_hit_ratio | 0.855 | 0.852 | 0.846 |
| http_5xx_rate | 0.0000 | 0.1509 | 0.0000 |
| container_restarts_total (故障期增量) | - | 0 | - |
| db_slow_queries_total (故障期增量) | - | 0 | - |
| gateway_upstream_5xx_total (故障期增量) | - | 2510 | - |

## 观测到的异常信号（基于实测数据判定）

- 5xx 错误率从 0.00% 升至 15.09%（故障期实测）
- node_disk_percent 峰值达 100.0%
- node_inode_percent 峰值达 100.0%
- gateway_upstream_5xx_total 故障期新增 2510 次

## 预期信号与根因线索

- [预期信号] node_disk_percent 接近 100
- [预期信号] node_inode_percent 接近 100
- [预期信号] http_requests_total{status="5xx"} 上升（写入失败）
- [预期信号] app 日志出现 'No space left on device'
- [根因线索] df -h / df -i 确认是空间还是 inode 打满
- [根因线索] 定位占满来源（日志轮转失效 / 大量小文件 / 临时目录）
- [根因线索] 清理并修复 logrotate，或扩容/挂载独立磁盘

## 取证文件

- `metrics.csv`：逐 tick 时间序列（可直接入 Prometheus/CSV 分析）
- `app.log`：真实风格应用/内核/SLB 日志
- `summary.json`：pre/fault/post 关键指标快照、异常信号、根因线索

