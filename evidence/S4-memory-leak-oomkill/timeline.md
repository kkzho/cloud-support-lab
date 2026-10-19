# 故障演练时间线 — S4-memory-leak-oomkill

- 场景名称：内存泄漏导致容器 OOMKilled
- 影响组件：app
- 仿真规模：共 260 tick（1 tick = 1 仿真秒），实际墙钟耗时 7.92s
- 阶段划分：pre [0,40) → fault [40,190) → post [190,260)

> 应用存在内存泄漏，RSS 持续增长，超过 limit 后被内核 OOMKilled，容器反复重启形成锯齿状内存曲线。

## 关键指标采样（每 20 tick）

| tick | phase | p95(ms) | p99(ms) | 5xx累计 | 连接池占用 | DB CPU% | 节点CPU% | 节点内存% | 磁盘% |inode% | 重启累计 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | pre | 186 | 199 | 0 | 6/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 20 | pre | 185 | 205 | 0 | 7/10 | 22 | 25 | 42 | 55 | 30 | 0 |
| 40 | fault | 191 | 198 | 0 | 6/10 | 22 | 25 | 44 | 55 | 30 | 0 |
| 60 | fault | 162 | 182 | 0 | 4/10 | 22 | 25 | 78 | 55 | 30 | 0 |
| 80 | fault | 189 | 195 | 15 | 5/10 | 22 | 25 | 41 | 55 | 30 | 1 |
| 100 | fault | 177 | 196 | 15 | 8/10 | 22 | 25 | 75 | 55 | 30 | 1 |
| 120 | fault | 186 | 195 | 27 | 7/10 | 22 | 25 | 40 | 55 | 30 | 2 |
| 140 | fault | 163 | 188 | 27 | 5/10 | 22 | 25 | 74 | 55 | 30 | 2 |
| 160 | fault | 175 | 198 | 45 | 5/10 | 22 | 25 | 43 | 55 | 30 | 3 |
| 180 | fault | 160 | 197 | 45 | 6/10 | 22 | 25 | 77 | 55 | 30 | 3 |
| 200 | post | 185 | 200 | 45 | 6/10 | 22 | 25 | 42 | 55 | 30 | 3 |
| 220 | post | 186 | 204 | 45 | 7/10 | 22 | 25 | 42 | 55 | 30 | 3 |
| 240 | post | 164 | 194 | 45 | 6/10 | 22 | 25 | 42 | 55 | 30 | 3 |

## 阶段对比（实测均值 / 增量）

| 指标 | pre | fault | post |
|---|---|---|---|
| http_request_duration_ms_p95 | 180.224 | 178.297 | 181.061 |
| http_request_duration_ms_p99 | 200.253 | 198.225 | 200.181 |
| app_conn_pool_in_use | 5.750 | 5.713 | 5.957 |
| db_cpu_percent | 22.000 | 22.000 | 22.000 |
| db_connections_used | 8.750 | 8.713 | 8.957 |
| node_cpu_percent | 25.000 | 25.000 | 25.000 |
| node_mem_percent | 42.000 | 66.448 | 43.068 |
| node_disk_percent | 55.000 | 55.000 | 55.000 |
| node_inode_percent | 30.000 | 30.000 | 30.000 |
| cache_latency_ms | 4.362 | 4.357 | 4.353 |
| cache_hit_ratio | 0.851 | 0.854 | 0.854 |
| http_5xx_rate | 0.0000 | 0.0025 | 0.0000 |
| container_restarts_total (故障期增量) | - | 3 | - |
| db_slow_queries_total (故障期增量) | - | 0 | - |
| gateway_upstream_5xx_total (故障期增量) | - | 45 | - |

## 观测到的异常信号（基于实测数据判定）

- node_mem_percent 峰值达 99.8%（均值 66.4%）
- container_restarts_total 故障期新增 3 次
- gateway_upstream_5xx_total 故障期新增 45 次

## 预期信号与根因线索

- [预期信号] node_mem_percent 反复逼近 100 后骤降（锯齿）
- [预期信号] container_restarts_total 递增
- [预期信号] 重启瞬间出现少量 5xx
- [预期信号] app 日志出现 'Out of memory: Killed process' / 'OOMKilled'
- [根因线索] cAdvisor / kubelet 事件里 OOMKilled 与 restartCount 增加
- [根因线索] dmesg / app 日志定位被 kill 的进程与分配点
- [根因线索] 结合 heap 分析确认内存泄漏源；必要时先抬高 limit 再定位

## 取证文件

- `metrics.csv`：逐 tick 时间序列（可直接入 Prometheus/CSV 分析）
- `app.log`：真实风格应用/内核/SLB 日志
- `summary.json`：pre/fault/post 关键指标快照、异常信号、根因线索

