# 云上业务稳定性保障与故障处置实战项目

> 面向**云厂商售后技术支持 / 技术支持工程师 / TAM** 岗位的求职作品集项目。
> 类型：**自主搭建的故障演练（Chaos Lab）项目**，非真实客户生产环境——这一点在面试中会主动说明，因为"能自己搭环境、能复现故障、能写清根因"恰恰是技术支持岗位最看重的方法论能力。

---

## 一、项目背景（为什么做这个项目）

云厂商的售后技术支持面试，考察的不是"背产品参数"，而是三件事：

1. **排障方法论**：能否在"现象 → 假设 → 验证 → 定位 → 止损 → 根因 → 预防"的链路里快速收敛；
2. **跨层定位能力**：能否区分"接入层 5xx、应用层慢、数据库瓶颈、节点资源不足"这几类长得像但根因完全不同的问题；
3. **服务工程化能力**：工单分级、SLA、客户沟通、复盘（RCA）、知识库沉淀。

本项目用一套**可运行的仿真三层业务栈**，把云厂商技术支持日常会遇到的高频故障逐一复现，并对每个故障留存**真实运行产生的指标、日志与复盘报告**，作为可展示的能力证据。

---

## 二、目标岗位与能力映射

| 岗位能力项 | 本项目对应证据 |
|---|---|
| 工单受理、分级、SLA 与升级 | [docs/02-售后技术支持工作流.md](docs/02-售后技术支持工作流.md)、[tickets/工单模板与话术.md](tickets/工单模板与话术.md) |
| 跨层排障（接入/应用/数据/节点） | [docs/03-故障场景库.md](docs/03-故障场景库.md)（8 个场景决策树） |
| 指标体系与告警配置 | [evidence/](evidence/)、[ops/prometheus/rules.yml](ops/prometheus/rules.yml)、[ops/alertmanager/alertmanager.yml](ops/alertmanager/alertmanager.yml) |
| 故障复盘与 RCA 报告 | [docs/rca/](docs/rca/)（4 份实测复盘） |
| 稳定性治理与高可用改造 | [docs/04-稳定性治理方案.md](docs/04-稳定性治理方案.md) |
| 云产品与架构映射 | [docs/01-架构与云产品映射.md](docs/01-架构与云产品映射.md) |
| 面试表达 | [docs/05-面试讲述稿与高频题.md](docs/05-面试讲述稿与高频题.md) |

---

## 三、交付物清单

```
cloud-support-lab/
├── README.md                      # 本文件：项目说明书
├── docs/                          # 文档资产
│   ├── 01-架构与云产品映射.md
│   ├── 02-售后技术支持工作流.md
│   ├── 03-故障场景库.md            # 8 个场景：决策树 + 命令 + 根因 + 预防
│   ├── 04-稳定性治理方案.md
│   ├── 05-面试讲述稿与高频题.md
│   └── rca/                        # 4 份基于实测证据的 RCA 复盘报告
│       ├── RCA-01-接入层健康检查失败-502-504.md
│       ├── RCA-02-应用连接池耗尽.md
│       ├── RCA-03-慢SQL-DB-CPU飙升.md
│       └── RCA-04-内存泄漏-容器OOMKilled.md
├── lab/                            # 可运行的仿真实验环境（纯 Python 标准库）
│   ├── sim/                        # 仿真应用栈 + 故障注入 + 指标导出 + 运行器
│   ├── scenarios/                  # 8 个场景 JSON（可读）
│   ├── scripts/                    # 便捷运行脚本
│   ├── docker-compose.yml          # 本地真实版编排（nginx+app+redis+mysql+prom/prom/alert+g）
│   └── README.md                   # 实验环境说明
├── ops/                            # 可观测性与告警配置
│   ├── prometheus/{prometheus.yml,rules.yml}
│   ├── alertmanager/alertmanager.yml
│   └── grafana/dashboard.json
├── tickets/                        # 工单模板、客户话术、知识库条目
└── evidence/                       # 8 个场景的实测取证输出
    ├── INDEX.json
    └── S1../S8..{metrics.csv,app.log,summary.json,timeline.md}
```

---

## 四、如何运行实验环境

### 方式一：沙箱/本机纯进程仿真（无需 Docker）

```bash
python3 lab/sim/runner.py --all --out evidence        # 跑全部 8 个场景
python3 lab/sim/runner.py --scenario S3-slow-sql-db-cpu   # 跑单个场景
```

运行后每个场景在 `evidence/<场景id>/` 下生成 `metrics.csv`（逐秒指标）、`app.log`（真实风格日志）、`summary.json`（故障前/中/后指标快照）、`timeline.md`（可读时间线）。

### 方式二：本地 Docker 真实版

```bash
cd lab && docker compose up -d      # 需本机有 Docker
```

详见 [lab/README.md](lab/README.md)。

---

## 五、8 个故障场景一览（实测数据）

| 场景 | 组件 | 故障期关键实测指标 |
|---|---|---|
| S1 应用 CPU 打满 | app/node | node_cpu → 97.4%，P99 → 951ms，5xx 13.2% |
| S2 连接池耗尽 | app | 连接池 10/10 打满，5xx 20.5% |
| S3 慢 SQL / DB CPU | db | db_cpu → 95.4%，慢查询 +364，P99 → 2028ms |
| S4 内存泄漏 OOMKilled | app/容器 | node_mem 峰值 99.8%，容器重启 +3 次 |
| S5 LB 健康检查失败 | slb | 网关 5xx +7097，到达率 118→60 req/tick |
| S6 磁盘/inode 打满 | node | disk 与 inode 峰值均 100%，5xx 15.1% |
| S7 DNS 解析异常 | node/网络 | 5xx 30.1%，getaddrinfo failed |
| S8 Redis 热 key | cache | cache_latency 4.4ms→369ms，P99 → 629ms |

> 全部数值来自 `python3 lab/sim/runner.py --all` 的真实运行输出（见 `evidence/INDEX.json`），非人工填写。

---

## 六、使用方式建议（面试怎么讲）

1. 先讲**项目动机**：为补齐"云售后技术支持"的实操短板，自主搭建故障演练环境，复现云上高频故障并沉淀标准处置流程。
2. 用 **RCA-01（接入层 5xx）** 做主线故事：讲"后端资源全正常、但用户全 5xx"的二阶排查过程——这是最能体现排障思维的案例。
3. 展开**排障方法论**（[docs/02](docs/02-售后技术支持工作流.md)）与**场景决策树**（[docs/03](docs/03-故障场景库.md)），展示跨层定位能力。
4. 用 **ops/ 的告警规则与抑制配置** 说明"如何把一次故障沉淀为一条永不遗漏的告警"。
5. 完整讲稿见 [docs/05-面试讲述稿与高频题.md](docs/05-面试讲述稿与高频题.md)。

---

## 七、诚实声明

- 本项目为**学习者自建的故障演练环境**，业务栈与故障均为**仿真模型**；指标趋势与信号方向贴合真实故障，但绝对值不等同于生产环境。所有结论在面试中可复现、可解释。
- 场景设计参考了云厂商公开的产品文档、公开技术社区的高频故障案例与通用 SRE/可观测性实践。
- 本仓库不含任何真实客户数据。
