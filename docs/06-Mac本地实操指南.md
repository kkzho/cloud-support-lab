# Mac (Apple Silicon / arm64) 本地实操指南

> 目标：在 MacBook M1/M2/M3（arm64）上，把 `cloud-support-lab` 跑到"最接近真实业务"的程度，
> 并且能按 **售后技术支持** 的真实工作姿势演练：**看监控 → 接工单 → 分层定位 → 止损 → 复盘**。
>
> 全文命令均为 macOS `zsh`（Apple Silicon 原生 arm64）。

---

## 0. 两条路径，先想清楚练什么

| 路径 | 依赖 | 练什么 | 最接近真实度 |
|---|---|---|---|
| **A. 标准库仿真** | 只要 `python3` | CLI 排障、看指标/日志、写取证、跑 8 场景 | ★★☆ 快（10 分钟开练） |
| **B. Docker 全栈** | Docker Desktop | 看板(实时) + 告警(真的会 firing) + 网关/DB/缓存真实容器 + 多容器排障 | ★★★★★ |

**建议两条都跑**：先用 A 把 8 个场景的"现象—根因"打通；再用 B 把"监控 + 告警 + 客户沟通"的
实战流程走一遍。B 是面试时最有说服力的部分。

> 说明：A 与 B 共享同一套仿真引擎（`lab/sim/app.py`）。B 模式下 app 默认带 **tick-loop**，
> 会以 1 仿真秒 = 1 真实秒 的速度把场景时间线 **循环播放**（`pre → fault → post → 回到 pre`），
> 所以 Prometheus / Grafana / Alertmanager 上看到的是 **会动、会告警、会恢复** 的实时数据。
> 一个完整周期约 260 秒，其中故障窗口约 130 秒。

---

## 1. 环境准备（macOS zsh）

### 1.1 Python
macOS 自带 `python3` 需要装命令行工具；或直接用 Homebrew。

```bash
xcode-select -p >/dev/null 2>&1 || xcode-select --install   # 若没装过 CLT

# 或者：
brew install python@3.12
python3 --version      # 需要 >= 3.9（3.11/3.12 均可）
```

本项目 **只用 Python 标准库**，无需 `pip install` 任何东西。

### 1.2 Docker Desktop（路径 B 用）
用 Apple Silicon 原生版，别装 Intel 版。

```bash
brew install --cask docker
# 首次启动 Docker Desktop，菜单栏鲸鱼图标变绿后再继续
docker version
docker compose version
```

**给 Docker Desktop ≥ 6 GB 内存**（Settings → Resources → Memory），因为要同时跑
mysql + prometheus + grafana + 7 个容器。低于 4 GB 时 mysql 容易 OOM。

### 1.3 可选但加分的小工具

```bash
brew install hey mysql-client redis-cli   # hey: 压测；mysql-client/redis-cli: 真实连库/缓存
```

---

## 2. 把项目放到 Mac

项目当前在沙箱 `/workspace/cloud-support-lab`。把它同步到 Mac（任选一种）：

```bash
# 方案1：整个目录打包下载/拷贝后解压
cd ~/projects
unzip cloud-support-lab.zip

# 方案2：走 Git
git clone <你的仓库地址> cloud-support-lab

# 方案3：scp（如果你能从沙箱导出）
scp -r <sandbox>:/workspace/cloud-support-lab ~/projects/
```

最终目录结构应包含 `lab/ outputs/ docs/ ops/ evidence/`（见 `README.md`）。

```bash
cd ~/projects/cloud-support-lab
ls        # 应看到 README.md lab docs ops tickets evidence
```

---

## 3. 路径 A：标准库仿真（先跑通，10 分钟）

### 3.1 一键跑全部 8 个场景

```bash
cd ~/projects/cloud-support-lab
python3 lab/sim/runner.py --all --out "$PWD/evidence"
```

每个场景会写入 `evidence/<id>/`：`metrics.csv`（逐秒指标）、`app.log`（真实风格日志）、
`summary.json`（pre/fault/post 快照 + 异常信号 + 根因线索）、`timeline.md`（人类可读时间线）。
`evidence/INDEX.json` 汇总每个场景的耗时与异常条数。

单场景：

```bash
python3 lab/sim/runner.py --scenario S5-lb-healthcheck-5xx --out "$PWD/evidence"
# 或
lab/scripts/run_scenario.sh S2-conn-pool-exhaustion
```

### 3.2 起一个"真实 HTTP"的 app（会实时动）

```bash
cd ~/projects/cloud-support-lab/lab
python3 sim/app.py --port 8080 --scenario S5-lb-healthcheck-5xx
# 输出： [app] serving on http://0.0.0.0:8080  scenario=S5-lb-healthcheck-5xx  mode=tick-loop (live metrics)

# 另开一个终端：
curl -s localhost:8080/healthz            # pre 阶段=200；进入 fault 后=500
curl -s localhost:8080/api/order/list
curl -s localhost:8080/metrics | grep -E "http_requests_total|gateway_upstream_5xx_total"
```

观察 `http_requests_total`、`gateway_upstream_5xx_total` 会随 tick 增长（`/metrics` 是实时刷新的）。
想回到"静态指标"演示：加 `--no-tick-loop`。想加快节奏：`--tick-period 0.2`（默认 1.0）。

### 3.3 路径 A 的练习清单（面试可直接讲）

对每个场景产出 **1 页排障笔记**，格式对齐 `docs/03-故障场景库.md`：

1. 现象（客户视角一句话 + 监控上看到什么）
2. 影响面（哪个接口、错误率、有没有到影响业务）
3. 决策树（先看什么、再二分到哪一层）
4. 关键命令 / 关键指标（写在笔记里）
5. 根因（对应 `summary.json.root_cause_hints`）
6. 止损 → 修复 → 预防
7. 复盘一句结论

写完对照 `docs/rca/RCA-0x-*.md` 校对，尤其 **S5 健康检查失败 502/504**（招牌二阶问题）。

---

## 4. 路径 B：Docker 全栈（最接近真实业务场景）

### 4.1 拉起全栈

```bash
cd ~/projects/cloud-support-lab/lab
docker compose up -d
docker compose ps          # 等到全部 healthy / running
```

首次会构建 app 镜像并拉取 mysql 等镜像，较慢；可先预热：

```bash
docker pull mysql:8.0 redis:7-alpine nginx:1.27-alpine \
  prom/prometheus:v2.54.1 prom/alertmanager:v0.27.0 grafana/grafana:11.2.0 \
  nginx/nginx-prometheus-exporter:1.3.0
```

> **⚠️ 国内常见报错：`context deadline exceeded` / `No such image`**
> 这是因为 Docker Hub（`registry-1.docker.io`）在国内连接不稳定，所有镜像都拉不下来，
> compose 就会逐个报 `No such image`。两种修复，**任选其一**：
>
> **方案一（推荐，一劳永逸）：给 Docker Desktop 配置镜像加速器**
> 打开 Docker Desktop → Settings → Docker Engine，把 JSON 改成：
> ```json
> {
>   "registry-mirrors": [
>     "https://docker.m.daocloud.io",
>     "https://hub-mirror.c.163.com",
>     "https://mirror.baidubce.com"
>   ]
> }
> ```
> 点 **Apply & Restart**，然后重跑 `docker compose up -d`。
>
> **方案二（兜底，不动全局配置）：用脚本走镜像源拉取并重打标**
> ```bash
> cd ~/projects/cloud-support-lab/lab
> MIRROR=https://docker.m.daocloud.io bash scripts/pull-images.sh
> docker compose up -d
> ```
> 脚本会用 `docker.m.daocloud.io/xxx` 拉取再 `docker tag` 回 compose 期望的镜像名，
> 已存在的镜像自动跳过，可重复执行。

### 4.2 入口与账号

| 组件 | 地址 | 说明 |
|---|---|---|
| Grafana | http://localhost:3000 | `admin` / `admin`，看板已自动导入（folder：云厂商售后技术支持） |
| Prometheus | http://localhost:9090 | Targets / Alerts 页最有用 |
| Alertmanager | http://localhost:9093 | 看告警分组与抑制 |
| 业务 app | http://localhost:8080/healthz 、`/api/order/list` 、`/metrics` | 直连后端 |
| 网关 (nginx/SLB) | http://localhost/api/order/list | 走接入层，故障时返回 502/504 |
| MySQL / Redis | localhost:3306 (`root`/`drill-root`) 、 localhost:6379 | 真实下游 |
| 各 exporter | 9100 node / 9104 mysql / 9121 redis / 9113 nginx-exporter | 供 Prometheus 抓取 |

拓扑：`loadgen → nginx(SLB/网关) → app → redis + mysql`，观测侧 `prometheus/grafana/alertmanager`
+ `node/mysqld/redis/nginx exporter`。与 `docs/01-架构与云产品映射.md` 一一对应。

### 4.3 注入故障：切换场景

app 用环境变量 `DRILL_SCENARIO` 预置场景：

```bash
# 例：注入 S5（负载均衡健康检查失败）
DRILL_SCENARIO=S5-lb-healthcheck-5xx docker compose up -d --force-recreate app

# 换其它场景（8 个 id 见下）
DRILL_SCENARIO=S2-conn-pool-exhaustion docker compose up -d --force-recreate app
DRILL_SCENARIO= docker compose up -d --force-recreate app   # 清空=健康基线
```

> 由于 tick-loop，注入后 app 会从 **pre 正常阶段** 开始播，约 40 秒后进入故障（fault）阶段，
> Prometheus 15 秒抓一次，告警一般再等 1–3 分钟（规则里有 `for` 持续时间）才 firing。
> 这正好模拟"故障是逐步暴露的"，练习"从苗头到告警"的观察能力。

### 4.4 一次完整的实战演练 SOP（照这个走，面试就按这个讲）

以 **S5 健康检查失败** 为例：

```bash
# ① 注入故障
DRILL_SCENARIO=S5-lb-healthcheck-5xx docker compose up -d --force-recreate app

# ② 客户视角复现：打网关，看到 502/504
for i in {1..20}; do curl -s -o /dev/null -w "%{http_code}\n" http://localhost/api/order/list; done

# ③ 但直连后端 /healthz 会变 500，业务侧 5xx 却不高（二阶问题的关键证据）
curl -s -o /dev/null -w "healthz=%{http_code}\n" http://localhost:8080/healthz
curl -s http://localhost:8080/metrics | grep gateway_upstream_5xx_total

# ④ 看接入层日志
docker logs drill-nginx --tail 30         # upstream timed out / no live upstreams
docker logs drill-app  --tail 30          # health check failed -> backend marked DOWN
```

**在 Grafana 看板上的观察顺序**（对应真题排障路径）：
1. 先看 **请求量(按状态码)** + **5xx 错误率** → 确认"确实在错、错在哪一层"
2. 再看 **P50/P95/P99 延迟** → 区分"超时(慢)" vs "立刻 502(后端被摘)"
3. 再看 **网关 upstream 5xx** 面板 → 确认是接入层而非应用/DB
4. 最后核对 **app 连接池 / DB CPU / 节点 CPU·内存·磁盘** → 排除后端资源问题

**在 Prometheus /alerts 看告警生命周期**：`inactive → pending → firing`，
其中 `GatewayUpstream5xxSurge` 就是这个场景的招牌告警。
**在 Alertmanager** 看分组（group）与抑制（inhibit）——`docs/.../alertmanager.yml` 已配置
"接入层故障时抑制下游无谓告警"。

### 4.5 8 场景实操检查点

| 场景 id | 注入方式（`DRILL_SCENARIO=`） | 关键面板 | 关键 PromQL | 招牌告警 |
|---|---|---|---|---|
| S1-app-cpu-saturation | `S1-app-cpu-saturation` | 节点 CPU、延迟 P95/P99 | `node_cpu_percent > 90` | `AppInstanceCpuSaturation` |
| S2-conn-pool-exhaustion | `S2-conn-pool-exhaustion` | 连接池占用 | `app_conn_pool_in_use / app_conn_pool_size > 0.9` | `DbConnectionPoolExhausted` |
| S3-slow-sql-db-cpu | `S3-slow-sql-db-cpu` | DB CPU、慢查询 | `increase(db_slow_queries_total[5m]) > 20` | `DbCpuSaturation` |
| S4-memory-leak-oomkill | `S4-memory-leak-oomkill` | 节点内存、重启次数 | `increase(container_restarts_total[10m]) > 0` | `ContainerRestartLoop` |
| S5-lb-healthcheck-5xx | `S5-lb-healthcheck-5xx` | 5xx 错误率、网关 upstream 5xx | `increase(gateway_upstream_5xx_total[5m]) > 50` | `GatewayUpstream5xxSurge` |
| S6-disk-inode-full | `S6-disk-inode-full` | 磁盘 / inode | `node_disk_percent > 95 or node_inode_percent > 95` | 磁盘/inode 告警 |
| S7-dns-resolution-fail | `S7-dns-resolution-fail` | 5xx 错误率（间歇）、P99 | `http_requests_total{status="5xx"}` 间歇上升 | 应用层 5xx 告警 |
| S8-redis-hotkey | `S8-redis-hotkey` | 缓存延迟、命中率 | `cache_latency_ms` 升高 + `cache_hit_ratio` 下降 | 缓存延迟告警 |

（告警名与表达式以 `ops/prometheus/rules.yml` 为准；121 行每条规则的 `description` 里都写了排查提示。）

### 4.6 给演练加"手感"（可选）

```bash
# 真实压测网关（装过 hey 后）
hey -z 60s -c 50 http://localhost/api/order/list

# 进容器看进程/连接（真实排障动作）
docker exec -it drill-app sh -c "ps aux | head; netstat -an | grep 3306 | head"
docker exec -it drill-mysql mysql -uroot -pdrill-root -e "show processlist; show global status like 'Threads_connected';"
docker exec -it drill-redis redis-cli --hotkeys      # 或 info stats
docker stats --no-stream
```

---

## 5. 面试展示建议（在 Mac 上录一段 3 分钟视频）

1. `docker compose up -d` → 打开 Grafana 看板，说明面板与三层架构的对应关系；
2. 注入 S5 → 打网关看到 502/504 → 讲"先看 5xx，再看延迟区分 502/504，再核对后端资源排除法"；
3. Prometheus 上指 `GatewayUpstream5xxSurge` 从 pending→firing；Alertmanager 看分组/抑制；
4. 给结论：根因 + 止损动作 + 改进项（对齐 `docs/rca/RCA-01`）。

这段视频 + `docs/05-面试讲述稿与高频题.md` 的稿子，能直接回答"你有没有真实排障经验"。

---

## 6. arm64 / Docker Desktop 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `failed to resolve reference ... context deadline exceeded` + 一连串 `No such image` | Docker Hub 在国内连接超时 | 见 4.1 节方案一/方案二：配置 `registry-mirrors` 或跑 `scripts/pull-images.sh` |
| `docker compose up` 卡在 mysql | 首次初始化慢 / 内存不足 | 调大 Docker Desktop 内存≥6GB；`docker compose logs mysql` 看进度 |
| 镜像 `no matching manifest for linux/arm64` | 该镜像无 arm64 版 | 本 compose 用的镜像均有 arm64；如自行替换某镜像无 arm64，可在 Docker Desktop 开启"Use Rosetta for x86/amd64 emulation" |
| node-exporter 指标看着不对 | Docker Desktop 是 VM，`pid: host` 是 VM 的 host | 只作参考；本演练的节点指标以 **app 导出的 `node_*_percent`** 为准 |
| 80 / 3000 / 9090 / 3306 端口被占 | 本机已有服务 | 改 compose 里对应 `ports:` 左侧（宿主端口），如 `"8081:8080"` |
| Grafana 打开没有看板 | 旧版本无非 provisioning | 已随项目提供 `ops/grafana/provisioning/`；若仍无，用看板 JSON 手动 Import |
| 告警一直不 firing | 场景还在 pre 阶段 / `for` 未到 | 等 40s 进故障，再等 1–3 分钟；查 `http://localhost:9090/alerts` |
| Apple Silicon 上 fork/线程报错 | 个别 Python 版本 | 用 `python3.11/3.12`；本项目纯 stdlib，无兼容问题 |

---

## 7. 清场

```bash
cd ~/projects/cloud-support-lab/lab
docker compose down            # 停容器
docker compose down -v         # 连 mysql 数据卷一起删（彻底清场）
```

---

## 8. 和岗位的对应关系（面试可直接引用）

- **路径 A** 证明你"会动手复现故障、会取证" → 对应"排障动手能力"。
- **路径 B** 证明你"会看监控、会读告警、会用分层定位" → 对应云厂商售后技术支持
  **一线响应 + 分层定位 + 客户沟通**。
- 全流程（接单→定位→止损→RCA→知识库）见 `docs/02-售后技术支持工作流.md`，
  稳定性治理视角见 `docs/04-稳定性治理方案.md`。

---

> 相关文档：`README.md`、`lab/README.md`、`docs/01-架构与云产品映射.md`、
> `docs/03-故障场景库.md`、`docs/05-面试讲述稿与高频题.md`、`docs/rca/`。
