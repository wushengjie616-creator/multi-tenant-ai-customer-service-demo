# 多租户 AI 客服平台 Demo

教育平台高并发 AI 客服机器人后端。通过 IM 接入消息、识别用户意图，完成 **平台指令、知识问答、日程提醒、财务查询** 四类业务，并支持人工转接、多租户隔离、高并发治理、安全审计与可观测。

项目附带一套**零配置的本地演示环境**：5 个确定性外部依赖 mock（IM / LLM / 知识库 / 平台 / 财务），无需真实 LLM 或外部服务，`make up` 一键启动即可离线走通完整消息闭环。

## 功能特性

- **IM 异步消息闭环**：HTTP webhook 与持久 WebSocket 双通道接入；事务型 outbox + NATS JetStream 实现「至少一次投递、业务恰好一次」；显式 ACK、有限重试、死信重放。
- **四类核心业务**：
  - 平台指令 —— 课程表、学习报告、请假、课程提醒、自动续费（高风险操作二次确认）。
  - 知识问答 —— 多表示语义 RAG，命中带来源，无证据拒答、不编造。
  - 日程提醒 —— 单次/每天/每周/工作日，多实例调度、occurrence 幂等。
  - 财务查询 —— 订单/账单/发票/退费/余额，返回前递归脱敏。
- **多租户隔离与安全**：JWT 认证 + RBAC（`user` / `agent` / `admin`）；租户与资源归属强制校验；PII 脱敏；审计日志；LLM 无工具执行权限。
- **人工转接**：连续两次不满触发，投递脱敏后的上下文包，在线/离线分别处理。
- **提前提醒**：事项时间与通知时间分开持久化，支持到点、提前 30 分钟、1 小时或自定义分钟数。
- **高并发治理**：tenant/user 双维限流（Lua 原子计数）、下游独立超时/重试/熔断、Redis 热上下文缓存（PostgreSQL 为事实源，故障自动回填）。
- **可观测**：Prometheus 指标、JSON 结构化日志（自动脱敏）、OpenTelemetry trace 贯穿 API → NATS → worker → 下游。

## 技术栈

Python 3.12 · FastAPI · PostgreSQL 15 · Redis 7 · NATS JetStream · Qdrant · Alembic · Docker Compose · Locust · Prometheus · OpenTelemetry

## 架构概览

```text
IM Client
   │  HTTP webhook / WebSocket（JWT 鉴权 + message_id 去重，快速返回 ACK）
   ▼
FastAPI API ── 事务型 outbox ──> NATS JetStream (im.inbound)
                                     │
                                     ▼
                              Worker（加载上下文 → 意图分类与风险判定
                                     → RAG / 平台工具 / 提醒 / 财务 / 人工转接
                                     → 安全检查与话术生成）
                                     │
                                     ▼
                           NATS JetStream (im.outbound) ──> mock-im 推送 / WS 下发
                                     │
                                     ▼
                         落库（PostgreSQL 事实源）/ 审计 / 指标 / trace
```

- **LLM 无执行权限**：写操作与敏感数据由确定性代码完成鉴权、校验、幂等、审计；LLM 只负责意图兜底与基于证据的话术组织。
- **一份镜像多入口**：Compose 只构建一次 `education-ai-service-app` 镜像，api / worker / scheduler / 5 个 mock 用不同 `command` 复用。

## 环境要求

- **Docker + Docker Compose v2**
- macOS 用 Docker Desktop 或 Colima；Linux 用原生 Docker；Windows 通过 WSL2 运行（见下）。
- 首次构建需访问网络拉取镜像与 Python 依赖（国内环境可在 `.env` 里设置 `PIP_INDEX_URL` 加速）。

### Windows 用户（WSL2）

本项目依赖 `make` 与 Docker Compose，Windows 下推荐通过 WSL2 运行，后续步骤与 Linux 完全一致。原生 PowerShell / CMD 不含 `make`，不推荐直接跑。

前置条件：Windows 10 2004 及以上，或 Windows 11；BIOS 已开启虚拟化（VT-x / AMD-V）。

1. **安装 WSL2**：在 PowerShell（管理员）执行

   ```powershell
   wsl --install
   ```

   默认安装 Ubuntu 发行版，装完按提示**重启**。重启后从开始菜单打开「Ubuntu」，首次会要求设置一个 Linux 用户名和密码（之后 `sudo` 会用到，请记住）。

2. **安装 Docker Desktop**：安装 [Docker Desktop for Windows](https://www.docker.com/products/docker-desktop/)，启动后在 *Settings → Resources → WSL Integration* 里勾选刚装的 Ubuntu 发行版，再 Apply & Restart。

   > 不想装 Docker Desktop，也可在 WSL 内直接装 Docker Engine（`curl -fsSL https://get.docker.com | sh`），但 WSL 默认不启用 systemd，每次开机后需 `sudo service docker start` 才能用，属于进阶路径，新手不推荐。

3. **进入 WSL，安装 make 并确认 Docker 可用**（在 Ubuntu 终端里执行）

   ```bash
   sudo apt update && sudo apt install -y make
   docker --version        # 能打印版本号即说明 WSL 里的 docker 已连上 Docker Desktop
   ```

   如果 `docker --version` 报「command not found」，说明第 2 步的 WSL Integration 没勾对发行版，回 Docker Desktop 重勾即可。

4. **把项目放进 WSL 的 Linux 文件系统**，**不要**放在 `/mnt/c/...`（Windows 盘）——那会导致 Docker 挂载与文件 IO 极慢。二选一：

   ```bash
   # 方式 A：项目在 GitHub/Git 上，直接在 WSL 里克隆
   git clone <仓库地址> ~/education-ai-service

   # 方式 B：项目是 Windows 上解压的文件夹（假设在「下载」目录）
   cp -r /mnt/c/Users/<你的用户名>/Downloads/education-ai-service ~/
   ```

5. **启动并验证**

   ```bash
   cd ~/education-ai-service
   cp .env.example .env   # 可选
   make up
   curl http://localhost:8000/health/ready   # 依赖就绪：postgres/redis/nats/qdrant 均为 ok
   ```

   之后用 Windows 浏览器打开 <http://localhost:8000/ui/> 即可（Docker Desktop 已把端口转发到 localhost）。其余健康检查、演示界面、常用命令见上文「快速开始」，与 Linux 完全一致。

## 快速开始

### 1. 准备环境变量（可选）

```bash
cp .env.example .env
```

**默认即可一键启动，无需任何密钥**：所有配置项在 `docker-compose.yml` 里都有内置默认值，不复制 `.env` 也会以「本地 mock 模式」运行，直接跳到第 2 步。

只有需要**真实 DeepSeek 回复**时，才需要编辑 `.env` 填入三个值（见下文「接入真实 DeepSeek」）。

### 2. 一键启动

```bash
make up
```

该命令会构建一次共享应用镜像，自动完成数据库迁移、导入示例知识库并启动全部服务（首次构建较慢）。

### 3. 验证服务

```bash
curl http://localhost:8000/health/ready   # 依赖就绪：postgres/redis/nats/qdrant 均为 ok
curl http://localhost:8000/health/live    # 进程存活
```

### 4. 打开演示界面

浏览器访问 <http://localhost:8000/ui/>：

- **完整虚拟租户**：无需输入账号，直接进入“星河未来成长中心”。每次进入都从空白聊天视图开始，顶部 8 个录屏问法使用确定性 Mock 回复；课表、报告、财务、请假和人工入口直接显示在对应回复下方。关闭续费前会检查当前状态，未开启时直接给出人类可读提示；创建提醒会拦截同一用户内容与时间完全相同的有效提醒；人工客服固定显示离线（每日 09:00–18:00），回复下方提供留言板且留言不会触发 AI。该租户不会调用 `.env` 中的真实 DeepSeek。
- **新建租户**：首页先进入独立的注册/登录页。注册时填写机构和管理员信息、按需勾选模块，系统同时生成 4 个归属该 tenant_id 的虚构客户。认证完成后进入纯租户工作台；工作台不再显示注册、登录、再建租户或客户视角入口。
- **虚拟租户演示**：入口固定展示 `T01–T05` 五个策展租户，避免历史手工测试数据影响录屏。短编号只用于展示，底层仍用 UUID tenant_id 隔离。虚拟链路可从租户工作台进入所选模拟客户视角，并只返回演示租户列表或首页，不与新建租户链路串联。
- **租户注册 / 登录**：`/ui/tenant-auth.html` 同页提供注册和已有租户登录；已有管理员使用租户 ID、邮箱和密码恢复 `/ui/tenant.html` 工作台。新建租户的 AI 客服按 `.env` 使用真实 LLM：确定性关键词先识别明确业务，未命中时由受白名单约束的 LLM 分类，再进入租户隔离的 RAG 检索或对应功能导航；页面只展示已启用模块。T01–T05 演示租户始终使用确定性 Mock，便于稳定录屏。
- **人工客服工作台**：启用人工服务的租户可从租户平台进入独立 `/ui/agent.html`。客服可接入会话、使用常用话术模板一键填充并发送、发起结束；客户侧实时展示待处理/处理中/待确认结束/已结束，客户可确认或继续，10 分钟不回应由 scheduler 自动结束。
- **LLM 全局排队池**：所有进程通过 Redis 共享准入状态，最多 600 个已发出的在途 LLM 请求，滚动一秒最多启动 20 个；第 601 个或超过 20/秒的请求等待，不向 DeepSeek 建立新连接。Redis 故障时使用进程内有界 semaphore + 每秒启动限制，不会无限直连。
- **Token 成本**：每次 LLM 调用记录供应商 `usage`，Mock 无 usage 时使用字符估算。租户工作台按会话展示输入、输出、总 token 和成本；单价由 `LLM_INPUT_COST_PER_MILLION_USD` 与 `LLM_OUTPUT_COST_PER_MILLION_USD` 配置，默认 0，避免对未知模型价格作错误假设。

建议验收时直接使用 [Word 要求验收对照](docs/acceptance-checklist.md) 和 [10–15 分钟演示剧本](docs/demo-script.md)。`make acceptance` 会依次执行 70% 覆盖率门禁、E2E、50 条 LLM 评测和就绪检查。

## `.env` 配置说明

| 分类 | 变量 | 说明 |
|---|---|---|
| LLM | `DEEPSEEK_API_URL` / `DEEPSEEK_API_KEY` / `DEEPSEEK_MODEL` | 真实 DeepSeek；留空则走本地 mock-llm |
| 演示登录 | `DEMO_TENANT_ADMIN_EMAIL` / `DEMO_TENANT_ADMIN_PASSWORD` | 租户工作台登录账号 |
| 客户样例 | `DEMO_CUSTOMER_TENANT_ID` / `DEMO_CUSTOMER_KNOWLEDGE_ROOT` | 客户一键示例的独立固定租户与本地知识目录 |
| 安全 | `JWT_SECRET` | 仅本地开发用；生产样环境使用空值/弱默认值时服务会拒绝启动 |
| 开关 | `DEMO_MODE` | 应用默认为 `false`；Compose 本地演示显式开启，生产样环境开启时服务会拒绝启动 |
| 限流 | `TENANT_RATE_LIMIT_PER_MINUTE` / `USER_RATE_LIMIT_PER_MINUTE` | 租户/用户每分钟配额 |
| 上下文 | `CONTEXT_MAX_MESSAGES` / `CONTEXT_TTL_SECONDS` | Redis 热上下文窗口与 TTL |

其余变量（数据库、Redis、NATS、Qdrant、各 mock 地址）已配置为 Compose 内服务名，本地开发一般无需改动。

### 接入真实 DeepSeek（可选）

默认使用本地 `mock-llm`，不需要密钥。需要真实回复时，在 `.env` 中替换：

```dotenv
DEEPSEEK_API_URL=https://api.deepseek.com/v1/chat/completions
DEEPSEEK_API_KEY=你的_key
DEEPSEEK_MODEL=deepseek-flash
```

然后重新执行 `make up`。模型名按你 DeepSeek 账号实际可用的填写（如 `deepseek-flash` / `deepseek-chat`）。

- API Key 只注入服务端，不会返回给前端。
- 知识检索有可靠证据时，DeepSeek 只负责基于证据组织话术，引用仍由服务端生成；调用失败自动回退到原始证据答案。
- 如果宿主机能访问 DeepSeek、但容器报 `Temporary failure in name resolution`，可在 `.env` 用 `DOCKER_DNS_PRIMARY` / `DOCKER_DNS_SECONDARY` 覆盖 Compose 为 API 和 worker 配置的公网 DNS。

## 自定义示例知识库（客户体验视角）

仓库已内置 `sample-data/tenants/xinghe-future/` 虚拟演示数据（非真实客户数据），可直接使用。如需换成你自己的演示租户，按下列结构放入知识文件：

```text
sample-data/
└── tenants/
    └── your-tenant/          # 任意目录名，例如你的机构名
        ├── manifest.json     # 文档清单（必需）
        └── knowledge/        # 知识正文（Markdown / 纯文本）
            ├── faq.md
            └── refund-policy.md
```

### 1. 编写 `manifest.json`

```json
{
  "tenant_id": "20000000-0000-0000-0000-000000000001",
  "documents": [
    {
      "document_id": "faq",
      "title": "常见问题",
      "source": "demo://knowledge/faq",
      "version": 1,
      "file": "knowledge/faq.md"
    }
  ]
}
```

> `tenant_id` 必须与 `.env` 里的 `DEMO_CUSTOMER_TENANT_ID` 一致（默认 `20000000-0000-0000-0000-000000000001`）；`file` 是相对该租户目录的路径。`expected-queries.jsonl` 仅用于离线评测，演示可省略。

### 2. 编写知识正文

支持 `.md` / `.markdown` / `.txt`，用 Markdown 标题组织问答即可，例如 `knowledge/faq.md`：

```markdown
# 常见问题

## 课程类

### Q: 一节课多长时间？
A: 常规课程每次 90 分钟，具体以课表为准。

### Q: 请假后能补课吗？
A: 可以，每学期有 3 次免费补课机会，须在请假后 30 天内完成。
```

### 3. 指向你的租户目录并导入

在 `.env` 里把客户示例指向你的目录：

```dotenv
DEMO_CUSTOMER_KNOWLEDGE_ROOT=sample-data/tenants/your-tenant
```

然后导入知识（首次 `make up` 已带起全部依赖；改完文件后只需重跑 bootstrap）：

```bash
docker compose run --rm demo-bootstrap
```

完成后打开 <http://localhost:8000/ui/> 进入「客户一键示例」，即可用你自己的知识进行问答。

## 常用命令

| 命令 | 说明 |
|---|---|
| `make up` | 构建共享镜像并启动完整环境 |
| `make down` | 停止并移除容器 |
| `make logs` | 跟踪日志 |
| `make ps` | 查看服务状态 |
| `make migrate` | 执行数据库迁移 |
| `make seed` | 初始化演示数据（租户/用户/会话） |
| `make test` | 容器内运行全部 pytest |
| `make loadtest` | Locust 可配置混合 smoke |
| `make loadtest-stable` | 20 msg/s 稳定消息接入 |
| `make loadtest-burst` | 100 msg/s 短时突发消息接入 |
| `make loadtest-finance` | 100 QPS 财务查询 |
| `make evaluate` | 运行 50 条固定离线意图评测 |
| `make demo` | 演示消息闭环（seed + 发送 + 幂等 + 轮询回复） |
| `make faulttest` | 短暂停止 Redis/NATS/worker，验证降级与积压恢复 |
| `make clean` | 清理容器与数据卷 |

## 服务端口

| 服务 | 端口 | 说明 |
|---|---|---|
| api | 8000 | HTTP/WS 入口 + OpenAPI `/docs` |
| mock-im | 8100 | IM 收发/在线状态/推送记录 |
| mock-llm | 8101 | 流式/延迟/500/非法 JSON/幻觉注入 |
| mock-knowledge | 8102 | 检索片段、来源、score |
| mock-platform | 8103 | 平台指令执行 |
| mock-finance | 8104 | 财务查询/鉴权/故障注入 |
| otel-collector | 4318 | OTLP/HTTP trace 接收与 debug 导出 |
| postgres / nats / qdrant | 5432 / 4222·8222 / 6333·6334 | 开发环境发布到宿主机的基础设施端口 |
| redis | 不发布 | 仅 Compose 网络内通过 `redis:6379` 访问 |

## 测试与质量

- **测试**：`tests/unit`（单元）、`tests/integration`（依赖集成）、`tests/e2e`（核心场景 E2E）。`make test` 全量运行，核心模块覆盖率 ≥ 70%（`--cov-fail-under=70`）。
- **意图评测**：`make evaluate` 在 50 条固定样本上跑离线意图评测，输出到 `evaluation/report.json`。
- **压测**：Locust 覆盖稳定接入、突发接入、100 QPS 财务查询与 WebSocket/LLM 降级。

## 压测报告

四组面试规模压测已执行并记录在 **[`report/压测报告.md`](report/压测报告.md)**：

- 环境：macOS arm64 + Colima，Docker Compose 本地完整环境。
- 结果：20.08 msg/s 稳定接入、100.27 msg/s 突发、100.27 QPS 财务查询和 20% LLM 超时降级均为 **0 失败**。
- 财务查询 P95 140 ms，满足题目 P95 < 500 ms 目标；NATS 突发后无积压。
- 报告如实记录 LLM 超时下的队头阻塞与未执行的生产级规模，**不代表生产容量承诺**。

复现命令：

```bash
DEEPSEEK_API_URL=http://mock-llm:8101/v1/chat/completions \
TENANT_RATE_LIMIT_PER_MINUTE=1000000 \
USER_RATE_LIMIT_PER_MINUTE=1000000 \
docker compose up -d --no-deps --force-recreate api worker scheduler mock-llm
make seed
make loadtest-stable
make loadtest-burst
make loadtest-finance

# 结束后恢复 .env 中的真实配置
docker compose up -d --no-deps --force-recreate api worker scheduler mock-llm
```

## 目录结构

```
app/             FastAPI 应用（api/core/models/schemas/services/clients/middleware/workers/utils）
frontend/        FastAPI 直接托管的本地演示页面（无前端构建步骤）
mocks/           5 个确定性的外部依赖 mock（llm/im/knowledge/platform/finance）
migrations/      Alembic 迁移
tests/           unit / integration / e2e
loadtests/       Locust HTTP + 持久 WebSocket 场景
evaluation/      50 条固定评测集、评分器与 JSON 报告
sample-data/     示例知识库目录骨架（.gitkeep 占位；真实数据不提交，见「自定义示例知识库」）
report/          面试提交版报告（含压测报告）
```

## 已知限制

正式交付结论与证据索引见 **[`report/正式验收报告.md`](report/正式验收报告.md)**；容量、WebSocket 多实例广播和 Prometheus 补齐方案见 **[`docs/optimization/06-08-delivery-optimization-plan.md`](docs/optimization/06-08-delivery-optimization-plan.md)**。

- 已在本地 2C4G 环境执行 500 WS、200 msg/s 稳定流、1000 msg/s 突发、100 QPS 财务和 20% LLM 超时场景。500 WS 与财务通过；200 msg/s 入口通过但消费未追平；1000 msg/s 未达标。详见压测报告与正式验收报告。
- Redis 中断、NATS 中断和 worker 积压恢复已有 `make faulttest` 可重复证据；详见 [`report/故障注入报告.md`](report/故障注入报告.md)。
- 确定性 embedding 负责低成本候选召回，语义弹性依赖 DeepSeek 改写与有上限的语义重排，适合演示与小型租户库，不代表大规模专用 embedding 模型的容量表现。
