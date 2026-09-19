FROM python:3.12-slim

# 使用 uv 管理依赖（镜像内免装 pip 依赖）
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 先拷依赖清单，利用镜像层缓存
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY app ./app
COPY config ./config
COPY servers ./servers
# M3：resume_kb server 运行期只读加载该目录（Markdown 知识卡片）；缺它则检索结果为空
COPY knowledge ./knowledge

EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
