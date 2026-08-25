# 部署到 Vercel — 操作步骤

> 目标：把 MCP Gateway 部署为 Vercel Function，拿到可点击的公网 demo 链接。
> 依据 Vercel 官方文档（2026-07 更新）：Python 运行时默认 3.12、支持 pyproject.toml + uv.lock 装依赖、
> 支持 FastAPI lifespan 启动事件、自动识别 `app/main.py` 里的 `app` 实例——**本仓库已满足全部约定，零代码改动**。

## ✅ 二阶段重部署记录（2026-08-25，已完成并验证）

二阶段（阶段 0–7 全部收官）已重新部署上线：push `1d739e9`（main 领先 origin 5 个提交：阶段 6 完整版 + 阶段 7 全部）→
Vercel Git 集成自动构建 → GitHub commit status `success / Deployment has completed`。线上实测全部通过：

| 检查项 | 结果 |
|---|---|
| `/health` | 200 `{"status":"ok","app_name":"mcp-gateway","version":"0.1.0"}` |
| `/tools` 带 Key | 200，恰好 4 个 `demo_sql__*` 工具 |
| `/tools` 无 Key | 401 |
| `/servers` | 200：`transport:"inprocess" connected:true tool_count:4`，**含阶段 7 新字段 `retry_attempts`/`next_retry_at`**（旧版没有，可作版本判别特征） |
| `/ui` 工作台 | 200，返回 MCP Agent Workbench HTML；`/` 重定向到 `/ui` |
| `POST /agent/run` | 200（mock 模式）：answer + steps，NL2SQL 闭环 `SELECT COUNT(*)` + `\| 5 \|` |
| `POST /tools/{name}/call/stream` | 200 `text/event-stream`，事件序列 `start → result → done` 完整（**SSE 在 Vercel 上实测未被缓冲**，此前担心的缓冲问题未出现） |

验证方式说明：本沙箱 curl.exe / PowerShell TLS 均被环境拦截（WinError 10060 / SSL EOF），最终用 `.venv` python
（忽略证书校验）验证；大陆 DNS 污染仍存在（偶发连接超时，重试即成功）。海外网络验收仍推荐
Actions → demo-health → Run workflow（默认参数即可）。

## ⚠️ 大陆访问限制（2026-08-17 实测）

`*.vercel.app` 在大陆被 DNS 污染（实测解析到 Twitter/Facebook 的假 IP，TCP 443 不通，curl HTTP 000），
且对 Vercel 真实 IP 的 SNI 连接也会被重置。**部署本身没问题**（污染生效前曾成功返回 /health 与
鉴权 401），但国内面试官直接点开链接大概率失败。应对方案：

- **绑自定义域名**（推荐，域名几十元/年）：Vercel → Domains → 加自己的域名，国内解析即正常
- **国内云服务器**：用仓库已验证的 `docker-compose.yml` 双容器部署（阿里云/腾讯云轻量）
- 暂时只要 Vercel 也可以：适合海外/会科学上网的观众

**验收技巧**：本机被污染时，可用仓库里的 `demo-health` GitHub Actions 工作流
（Actions → demo-health → Run workflow）从海外 Runner 跑第 4 节「部署后验收清单」。

## 仓库侧已备好的文件

| 文件 | 作用 |
|---|---|
| `app/main.py` | Vercel 自动识别的入口（`app/` 内的 `main.py` + 顶层 `app` 变量） |
| `pyproject.toml` + `uv.lock` | 依赖声明，Vercel 直接用它安装 |
| `.python-version` | 固定 Python 3.12（Vercel 默认即 3.12） |
| `config/gateway.vercel.yaml` | Vercel 专用配置：SQLite 路径改到 `/tmp`（函数文件系统只读，/tmp 除外）；demo server 走 `inprocess` 进程内加载 |
| `vercel.json` | 把函数 maxDuration 提到 60s（预留冷启动余量） |

## 部署步骤（约 5 分钟）

1. **推送代码**：确认本仓库最新 main 已 push 到 GitHub。
2. **导入项目**：Vercel Dashboard → Add New → Project → 选 `MCP_Gateway_Demo` 仓库。
   - 注意：仓库已从 `MCP-Gateway-Demo` 改名（旧名 301 跳转）；若 Vercel 的 Git 集成失联
     （Settings → Git 显示旧名/报错），重新连接改名后的仓库，否则 push 不会触发自动部署。
   - Framework Preset 会自动识别为 **FastAPI**，无需手选；Root Directory 保持仓库根目录。
   - 若构建报 `Invalid config ... tool.uv.index.0.name: Required`：仓库不是最新 main
     （新版 uv 要求 index 带 name，已在 `823bdae` 修复）。
3. **配环境变量**（Settings → Environment Variables，或导入时展开 Environment Variables）：

   | Name | Value | 说明 |
   |---|---|---|
   | `GATEWAY_CONFIG_FILE` | `config/gateway.vercel.yaml` | 指向 Vercel 专用配置（SQLite 落 /tmp、demo server 进程内加载） |
   | `DEMO_SQL_DB_PATH` | `/tmp/demo_sql.db` | 可选：demo 库的 SQLite 路径；不配时默认路径不可写会自动回退 /tmp |

4. **Deploy**，等构建完成，拿到 `https://<项目名>.vercel.app`。

## 部署后验收清单

> 以下为 bash 语法。Windows PowerShell 里 `curl` 是 `Invoke-WebRequest` 的别名、语法不兼容，
> 请用 Git Bash 跑，或改用 `curl.exe`（Win10+ 自带，逐字把 `curl` 换成 `curl.exe` 即可）。

```bash
# 1. 健康检查（无需 Key）
curl https://<项目名>.vercel.app/health

# 2. 无 Key 应 401
curl -i https://<项目名>.vercel.app/tools

# 3. 列工具（应见 4 个 demo_sql__* 工具）
curl -H "X-API-Key: dev-key-please-change" https://<项目名>.vercel.app/tools

# 4. NL2SQL 完整闭环（面试官要看的就是这个）
curl -X POST https://<项目名>.vercel.app/tools/demo_sql__ask/call \
  -H "X-API-Key: dev-key-please-change" -H "Content-Type: application/json" \
  -d '{"arguments": {"question": "有多少客户？"}}'
```

> 首次请求是冷启动：lifespan 在进程内加载 demo server 并完成工具注册，等 1–3 秒属正常。

## 为什么 demo server 不再拉子进程（2026-08-19 修复）

原方案用 stdio 子进程拉起 `servers/demo_sql_server/server.py`，实测 Vercel 子进程的解释器
看不到构建期安装的 site-packages（`ModuleNotFoundError: No module named 'mcp'`），导致工具
注册数为 0。现改为 `inprocess` 传输：网关进程内直接 import 并复用 demo server 的 MCPServer
实例（`app/mcp/client.py` 的 `InProcessClient`），与网关共用同一份依赖，彻底消除解释器环境
差异，冷启动也更快。本地 / Docker 仍走 stdio / Streamable HTTP，不受影响。

## 已知注意事项（如实告知面试官也可）

- **仓库改名会断 Vercel Git 集成**（2026-08-20 实测）：GitHub 仓库改名（`MCP-Gateway-Demo` → `MCP_Gateway_Demo`）后，Vercel 项目需在 Settings → Git 重新连接新仓库名，否则 push 不触发自动部署（表现为：代码已推上 main、线上却仍是旧部署）。重连后记得重新部署一次。
- **冷启动**：Serverless 实例冷启动时才连 MCP Server（lifespan），首请求略慢；热实例毫秒级。
- **状态为实例级**：限流令牌桶、Prometheus 指标、/tmp 下的 SQLite 都是单实例内存/本地态，多实例不共享——MVP 演示无影响，企业生产应换外部存储（这正是 README「企业级拓展路径」里讲的）。
- **演示 Key 是公开的**：`dev-key-please-change` 就在仓库里，任何拿到链接的人都能调（60 次/分钟限流兜底）。想换 Key：改 `config/gateway.vercel.yaml` 的 key 值，但别把新 Key 提交进公开仓库。
- **构建源走的是阿里云 PyPI 镜像**（pyproject.toml 里的 `[[tool.uv.index]]`）：Vercel 海外构建机访问阿里云可能偏慢，一般只是慢不会失败。~~新版 uv 要求 index 条目带 `name`~~（已修复：条目已加 `name = "aliyun"`；若构建报 `tool.uv.index.0.name: Required` 说明仓库不是最新 main）。
- **stdio 子进程曾在 Vercel 环境受限**（子进程解释器看不到构建期依赖，已踩中）：已通过 `inprocess` 进程内传输解决（见上节）；若未来还有 Serverless 不适配场景，备选方案是用仓库里已验证过的 `Dockerfile` 部署到 Render / Railway（Docker 运行时，无 Serverless 限制），步骤见下节。

## 备选：Render（Docker，最稳）

本仓库 `Dockerfile` 已经过 `docker compose up --build` 全链路验证：

1. Render → New → Web Service → 连接 GitHub 仓库。
2. Runtime 选 **Docker**（自动用根目录 `Dockerfile`），实例选 Free。
3. 环境变量不用配（默认 `config/gateway.yaml`，stdio 拉起 demo server，同容器）。
4. 部署后同样跑上面「验收清单」的 4 条 curl。

> docker-compose.yml 的双容器形态（gateway + 独立 demo_sql 容器）适合云服务器/自有 K8s；
> Render 单容器形态用默认 stdio 配置即可，无需 compose。
