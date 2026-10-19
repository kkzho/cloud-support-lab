# 知识库条目示例（KB）

> 每条知识库条目都遵循统一结构：【现象】【快速判断】【根因】【处置】【预防/关联告警】。
> 这些条目与 [docs/rca/](../docs/rca/) 的复盘报告一一对应，体现"**一次故障 → 一个 RCA → 一条知识库 + 一条告警**"的闭环。

---

## KB-001 负载均衡健康检查失败导致 502/504

- **现象**：用户侧大面积 502/504，但后端 CPU/内存/DB 指标均正常。
- **快速判断**：
  - `gateway_upstream_5xx_total` 突增 且 业务到达率骤降；
  - 后端资源正常 → 问题在接入层；
  - 日志关键词：`health check failed ... marked DOWN`、`no live upstreams`、`upstream timed out (110) ... 504`。
- **根因**：健康检查探针 `/healthz` 返回非预期状态码 / 配置与后端不一致 / 安全组未放通检查来源，导致后端被判 DOWN 并摘除。
- **处置**：
  1. 直连后端验证探针返回码（`curl http://<后端IP>:<端口>/healthz`）；
  2. 核对 SLB 健康检查路径、端口、期望状态码、间隔与阈值；
  3. 核对安全组/ACL 是否放通健康检查来源 IP 与端口；
  4. 修正后确认后端恢复健康、流量回归。
- **预防/关联告警**：`GatewayUpstream5xxSurge`、`HealthcheckBackendDown`（见 [ops/prometheus/rules.yml](../ops/prometheus/rules.yml)）。
- **关联 RCA**：[RCA-01](../docs/rca/RCA-01-接入层健康检查失败-502-504.md)

---

## KB-002 应用数据库连接池耗尽（Connection is not available）

- **现象**：应用大面积报 `Connection is not available`；数据库本身指标正常。
- **快速判断**：
  - `app_conn_pool_in_use ≈ app_conn_pool_size`（打满）；
  - 日志关键词：`Connection is not available, request timed out`、`connection leak detected ... never returned`。
- **根因**：连接泄漏（异常路径未归还）或慢调用长期占用连接，导致连接池被占满。
- **处置**：
  1. 滚动重启受影响实例，释放连接（先止损）；
  2. 临时扩大 `maximumPoolSize`、缩短 `connectionTimeout`；
  3. 开启 `leakDetectionThreshold`，定位未归还连接的代码路径；
  4. 复核 `实例数 × 池大小 ≤ DB 最大连接数`。
- **预防/关联告警**：`DbConnectionPoolExhausted`。
- **关联 RCA**：[RCA-02](../docs/rca/RCA-02-应用连接池耗尽.md)

---

## KB-003 慢 SQL / 缺失索引导致 DB CPU 飙升

- **现象**：接口 P99 进入秒级，DB CPU 逼近 100%，慢查询堆积。
- **快速判断**：
  - `db_cpu_percent` 高 + `db_slow_queries_total` 增速突增；
  - 慢日志中 `rows_examined` 达百万级（全表扫描特征）；
  - 连接池被慢查询占用（与 KB-002 联动）。
- **根因**：新上线查询缺索引 / 索引失效（隐式转换、函数包裹字段）导致全表扫描。
- **处置**：
  1. 从慢查询日志提取 TOP SQL，`EXPLAIN` 确认是否走索引；
  2. 应急加索引 / 改写 SQL / 加只读实例分流 / 缓存兜底；
  3. 若为发布引入，评估回滚。
- **预防/关联告警**：`DbCpuSaturation`、`DbSlowQuerySurge`；上线 SQL 审核 + 索引基线。
- **关联 RCA**：[RCA-03](../docs/rca/RCA-03-慢SQL-DB-CPU飙升.md)

---

## KB-004 内存泄漏导致容器 OOMKilled、周期性重启

- **现象**：服务周期性重启，重启窗口内少量 5xx；内存曲线呈锯齿状。
- **快速判断**：
  - 容器退出码 **137** + `Last State: OOMKilled`；
  - `container_restarts_total` 增长；
  - 日志关键词：`Out of memory: Killed process`、`Reason OOMKilled (exit code 137)`。
- **根因**：应用内存泄漏，RSS 持续增长超过容器 limit 被内核杀死。
- **处置**：
  1. 临时抬高内存 limit / 扩容；多实例时先摘除异常实例；
  2. `kubectl logs --previous` 看崩溃前日志；heap dump 分析泄漏对象；
  3. 修复泄漏后做长稳内存回归。
- **预防/关联告警**：`ContainerMemoryPressure`、`ContainerRestartLoop`。
- **关联 RCA**：[RCA-04](../docs/rca/RCA-04-内存泄漏-容器OOMKilled.md)

---

## KB-005 磁盘空间 / inode 打满

- **现象**：写入失败，报 `No space left on device`；空间监控看起来未必满。
- **快速判断**：`df -h` 空间满 **或** `df -i` inode 满（**两者都要看**）；日志未轮转。
- **根因**：日志未轮转 / 大量小文件占满 inode / 文件被删但句柄未释放。
- **处置**：清理或归档（先确认非业务数据）→ 启用 `logrotate` → `lsof | grep deleted` 释放句柄（必要时重启进程）。
- **预防/关联告警**：`NodeDiskSpaceFull`、`NodeInodeFull`。

---

## KB-006 DNS 解析异常导致间歇性 5xx

- **现象**：服务"时好时坏"，报 `getaddrinfo failed`，节点资源正常。
- **快速判断**：错误信息含域名 + `nameserver ... timeout`；`dig @<ns> <domain>` 复现超时。
- **根因**：DNS/CoreDNS 解析超时、`resolv.conf` 参数不合理、高峰期并发解析打爆 DNS。
- **处置**：增加可用 nameserver → 应用侧本地缓存 + 解析重试 → CoreDNS 扩容/重启 → 核心依赖考虑直连/服务发现。
- **预防/关联告警**：`DnsResolutionFailure`。

---

## KB-007 Redis 热 key / 大 key 导致缓存抖动

- **现象**：接口偶发变慢，缓存延迟升高、命中率下降，DB 压力连带上升。
- **快速判断**：`cache_latency_ms` 突增、`cache_hit_ratio` 下降；`redis-cli --bigkeys` / `--hotkeys` 命中。
- **根因**：单个热 key 高并发打满单节点；大 key 单次操作耗时长。
- **处置**：热 key 本地缓存 / 热点打散（加后缀拆 key）；大 key 拆分为小 key；提升实例规格或启用集群分片。
- **预防/关联告警**：`CacheLatencyDegraded`、`CacheHitRatioDrop`；定期 `--bigkeys` 巡检。
