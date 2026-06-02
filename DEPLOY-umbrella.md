# WebStock 全家桶（Umbrella）一键部署指南

把 **WebStock + StockPulse + AlphaForge + NewsForge** 四套服务用一份合并的
`docker-compose.all.yml` 同时拉起，全部基于已发布镜像（无需源码、无 `build:`），
共享一张 Docker 桥接网络 `webstock-umbrella`。

> 关键文件
> - `docker-compose.all.yml` — 合并后的总编排
> - `.env.all.example` — 环境变量模板（复制为 `.env`）
> - 本文档 — 部署步骤 + **手动签发跨服务 API Key 的精确流程**

---

## 1. 架构与服务清单

四套栈各自保留独立的 PostgreSQL + Redis（**不合并**），共 **13 个容器**：

| 栈 | app 容器 | 数据库 | 缓存 | 额外 |
|---|---|---|---|---|
| **WebStock** | `webstock-app` | `webstock-postgres` | `webstock-redis` | `webstock-rsshub` |
| **StockPulse** | `stockpulse-app` | `stockpulse-postgres` | `stockpulse-redis` | — |
| **AlphaForge** | `alphaforge-app` | `alphaforge-postgres` | `alphaforge-redis` | — |
| **NewsForge** | `newsforge-app` | `newsforge-postgres` | `newsforge-redis` | — |

合计：4 × app + 4 × postgres + 4 × redis + 1 × rsshub = **13 容器**。

> 为什么不合并 Redis：StockPulse 与 AlphaForge 的 Redis 必须用 `noeviction`
> 策略（JWT 黑名单 + 分布式锁，键不可被淘汰），而 NewsForge/WebStock 用
> `allkeys-lru`。两类策略不可共存，因此每栈独立 Redis，`command`/`maxmemory`/
> 策略/密码均按各自源 compose 原样保留。

### 端口表（宿主机 → 容器 :80）

| 服务 | 宿主端口（默认） | 访问地址 | 对应 env |
|---|---|---|---|
| WebStock  | `80`   | http://localhost/        | `APP_PORT` |
| StockPulse| `8010` | http://localhost:8010/   | `SP_APP_PORT` |
| AlphaForge| `8015` | http://localhost:8015/   | `AF_APP_PORT` |
| NewsForge | `8080` | http://localhost:8080/   | `NF_APP_PORT` |

> 每个 app 容器内部都是 **nginx 监听 :80**，反代到本机 uvicorn
> (`127.0.0.1:8000/8010/8015`)。**容器间互访必须走 nginx 的 :80**，不能直连
> uvicorn 端口——这些 uvicorn 端口在合并编排里没有对外发布，WebStock 的 uvicorn
> 更是只绑 `127.0.0.1`。因此所有跨栈 URL 都形如 `http://<service>-app`（即 :80）。

### 跨栈调用关系（DNS 走 :80）

```
WebStock ──/api/v1/data──▶ StockPulse        (STOCKPULSE_URL=http://stockpulse-app)
WebStock ──/api/v1──────▶ AlphaForge         (ALPHAFORGE_URL=http://alphaforge-app)
WebStock ──/api/internal▶ NewsForge          (NEWSFORGE_URL=http://newsforge-app)
AlphaForge ─/api/v1/data▶ StockPulse          (行情数据全部来自 StockPulse)
```

各客户端会自动补上自己的 base path，所以 env 里只填**根 URL**（不带路径）。

---

## 2. 前置条件

1. **登录 GHCR**（镜像可能为私有仓库）：
   ```bash
   docker login ghcr.io
   ```
2. **架构要求：AlphaForge 镜像仅 amd64**（`ghcr.io/avesed/alphaforge:v0.1.1`，
   Qlib 无 arm64 构建）。compose 中已固定 `platform: linux/amd64`。
   - 在 **amd64 宿主机**上部署最稳妥。
   - 在 Apple Silicon / arm 主机上会以 QEMU 模拟运行 AlphaForge（很慢，仅作验证用途）。
3. Docker Engine ≥ 24 + Compose v2（`docker compose`，非 `docker-compose`）。
4. 内存预算：AlphaForge 上限 12G、WebStock 4G、NewsForge 4G、各 pg 约 2G。建议宿主机 ≥ 24G。

---

## 3. 部署步骤

```bash
cd /home/trevor/webstock

# 1) 准备环境变量
cp .env.all.example .env
#   必填：四个 *_POSTGRES_PASSWORD（SP_ 不填会直接启动失败）
#   建议：WS_JWT_SECRET_KEY（openssl rand -hex 32）、NF_OPENAI_API_KEY
#   先留空：四个跨栈 API Key（见 §4 启动后再签发）
nano .env

# 2) 拉镜像（可选，up 时也会自动拉）
docker compose -f docker-compose.all.yml pull

# 3) 启动
docker compose -f docker-compose.all.yml up -d

# 4) 等四个 app 健康
watch -n3 'docker compose -f docker-compose.all.yml ps'
```

启动顺序由 `depends_on: condition: service_healthy` 保证：

- 每个 app 等自己的 postgres + redis 健康；
- `alphaforge-app` 额外等 `stockpulse-app` 健康（它的行情数据来自 StockPulse）；
- `webstock-app` 额外等 `stockpulse-app` / `alphaforge-app` / `newsforge-app`
  三者健康，确保 DNS 可解析、集成可用。

> 首次启动各 app 会自动跑数据库迁移（`alembic upgrade head`），AlphaForge 启动
> 较慢，已设 `start_period: 60s`。
>
> **StockPulse 首启会下载行情种子数据**：空库时 StockPulse 会从 GitHub release
> 拉取一份行情快照（股票列表+资料+日线），耗时数分钟、占数 GB 磁盘，且在导入完成前
> `/health` 不可用、容器长时间显示 `unhealthy`——这是预期行为（跨栈依赖用
> `service_started` 而非 `service_healthy` 正是为此）。若想跳过、改为启动后实时采集，
> 在 `.env` 设 `SP_SEED_RELEASE=skip`。

---

## 4. 手动签发跨服务 API Key（核心步骤）

四个 app 都健康后，按下列**精确流程**签发 4 个 Key。
所有 raw key **只在创建时返回一次**，务必当场复制。

### a. 登录 StockPulse 取 JWT
```bash
SP_JWT=$(curl -sX POST http://localhost:8010/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@stockpulse.dev","password":"Admin123"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["accessToken"])')
echo "$SP_JWT"
```
> 若你的 token 字段名不同，可先 `curl ... | python3 -m json.tool` 查看返回结构。

### b. 为 AlphaForge 在 StockPulse 签发消费者 Key
```bash
curl -sX POST http://localhost:8010/api/v1/admin/consumers \
  -H "Authorization: Bearer $SP_JWT" -H 'Content-Type: application/json' \
  -d '{"name":"alphaforge"}'
```
返回 JSON 中的 `rawApiKey` ⇒ 写入 `.env` 的 **`AF_STOCKPULSE_API_KEY`**。

### c. 为 WebStock 在 StockPulse 签发消费者 Key
```bash
curl -sX POST http://localhost:8010/api/v1/admin/consumers \
  -H "Authorization: Bearer $SP_JWT" -H 'Content-Type: application/json' \
  -d '{"name":"webstock"}'
```
`rawApiKey` ⇒ 写入 `.env` 的 **`STOCKPULSE_API_KEY`**（WebStock 用）。

### d. 登录 AlphaForge，为 WebStock 签发消费者 Key
```bash
AF_JWT=$(curl -sX POST http://localhost:8015/api/v1/admin/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@alphaforge.dev","password":"Admin123"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["accessToken"])')

curl -sX POST http://localhost:8015/api/v1/admin/consumers \
  -H "Authorization: Bearer $AF_JWT" -H 'Content-Type: application/json' \
  -d '{"name":"webstock"}'
```
`rawApiKey` ⇒ 写入 `.env` 的 **`ALPHAFORGE_API_KEY`**。

> 注意：StockPulse 登录在 `/api/v1/auth/login`，AlphaForge 登录在
> `/api/v1/admin/auth/login`（路径不同），但两者签发消费者都在
> `/api/v1/admin/consumers`。

### e. NewsForge —— 先注册首个管理员，再签发 Key
NewsForge **没有自动 admin**：首个 `/register` 的用户成为 admin，且若设置了
`NF_FIRST_ADMIN_EMAIL` 白名单，**只有该邮箱**能拿到 admin。请用与 `.env` 中
`NF_FIRST_ADMIN_EMAIL`（默认 `admin@newsforge.local`）一致的邮箱注册：

> 注：`admin@newsforge.local` 这个默认值由 umbrella 的 `docker-compose.all.yml` 与
> `.env.all.example` 注入；NewsForge 代码本身 `first_admin_email` 默认为 None（无白名单）——
> 独立部署 NewsForge 时，首位**任意**邮箱注册者即成为 admin。

```bash
# 首次注册（成为 admin）
NF_JWT=$(curl -sX POST http://localhost:8080/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"admin@newsforge.local","password":"ChooseAStrongPass1","displayName":"Admin"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["accessToken"])')

# 若已注册过，改用登录：
# NF_JWT=$(curl -sX POST http://localhost:8080/api/v1/auth/login \
#   -H 'Content-Type: application/json' \
#   -d '{"email":"admin@newsforge.local","password":"ChooseAStrongPass1"}' \
#   | python3 -c 'import sys,json;print(json.load(sys.stdin)["accessToken"])')

# 为 WebStock 签发消费者 Key
curl -sX POST http://localhost:8080/api/v1/admin/consumers \
  -H "Authorization: Bearer $NF_JWT" -H 'Content-Type: application/json' \
  -d '{"name":"webstock"}'
```
`rawApiKey` ⇒ 写入 `.env` 的 **`NEWSFORGE_API_KEY`**。

### f. 让消费方加载新 Key
把 4 个 key 写入 `.env` 后，重建会读取这些 key 的两个 app：
```bash
docker compose -f docker-compose.all.yml up -d --force-recreate \
    alphaforge-app webstock-app
```
> （StockPulse / NewsForge 本身不消费别人的 key，无需重建。）

> 安全提示：raw key 只返回一次，丢了只能删旧消费者重新签发；
> StockPulse / AlphaForge 的默认管理员口令均为 **`Admin123`**，请尽快登录各自
> 前端（:8010 / :8015）修改。

---

## 5. 验证清单

```bash
# 四个 /health
curl -sf http://localhost:8010/health        && echo " stockpulse OK"
curl -sf http://localhost:8015/health         && echo " alphaforge OK"
curl -sf http://localhost:8080/api/v1/health  && echo " newsforge  OK"
curl -sf http://localhost/api/v1/health        && echo " webstock   OK"
```

随后在 WebStock 前端（http://localhost/）实测三条集成链路：

1. **行情（StockPulse）**：搜索/打开任一股票，看到报价与 K 线。
2. **预测（AlphaForge）**：进入 ML 预测/Qlib 相关页面，能返回模型/预测数据。
3. **新闻（NewsForge）**：新闻页能拉到文章与情绪分。

> 还需在 **Admin → Settings → LLM Providers** 配置至少一个 LLM provider。
> WebStock **不读取任何 env 里的 OPENAI_API_KEY**，没有兜底；在配置 provider
> 之前，分析/对话/讨论等所有 LLM 功能都处于禁用状态。

---

## 6. 注意事项 / 已知限制

- **NewsForge → WebStock 推送 webhook 被刻意禁用**：WebStock 端没有任何地方校验
  webhook 密钥，NewsForge 自行为每个 webhook 生成独立密钥。情绪数据由 WebStock
  **按需主动拉取**，因此 umbrella 中**故意不设** `NEWSFORGE_WEBHOOK_SECRET`。
- **RD-Agent 的 LLM 未接通**：AlphaForge 的 `AI_GATEWAY_URL` 默认未设置。WebStock
  的 AI Gateway 绑在自身容器的 `127.0.0.1:8004`，跨容器不可达，故 RD-Agent 的
  LLM 能力暂不可用。如需启用，请提供一个跨容器可达的 LLM 端点并在 compose 中取消
  `AI_GATEWAY_URL` 注释。
- **AlphaForge 仅 amd64**：见 §2。arm 宿主机仅能模拟运行。
- **各栈独立 pg/redis**：数据卷按栈命名（`*_pgdata` / `*_redisdata` /
  `webstock_app_data` / `newsforge_articledata` / `alphaforge_qlib_data` /
  `alphaforge_predictions`），互不共享。
- **数据库口令在卷首次初始化时固化**：`*_POSTGRES_PASSWORD` 必须在**第一次
  `up` 之前**设好；之后再改不会生效，需删卷重建。
- **StockPulse 强制要求密码**：`SP_POSTGRES_PASSWORD` 未设置会让 stockpulse-postgres
  与 stockpulse-app 直接启动失败（源 compose 用 `${POSTGRES_PASSWORD:?}`）。
- **NewsForge 自带 rsshub 被省略**：复用 WebStock 的 `webstock-rsshub`
  （`NF_RSSHUB_URL=http://webstock-rsshub:1200`）。若设置了 `WS_RSSHUB_ACCESS_KEY`，
  该 key 已自动透传给 NewsForge（`RSSHUB_ACCESS_KEY`），两端一致。

## 7. 常用运维命令

```bash
# 查看状态 / 日志
docker compose -f docker-compose.all.yml ps
docker compose -f docker-compose.all.yml logs -f webstock-app

# 仅更新某个 app 镜像
docker compose -f docker-compose.all.yml pull webstock-app
docker compose -f docker-compose.all.yml up -d webstock-app

# 停止 / 销毁（保留卷）
docker compose -f docker-compose.all.yml down

# 彻底清除（含数据卷 —— 危险）
docker compose -f docker-compose.all.yml down -v
```
