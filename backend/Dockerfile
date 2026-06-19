# AGV-TMS Backend — Production Dockerfile (Multi-stage Build)
#
# Phase 5: 工程化加固
#   - 多阶段构建: builder → runtime
#   - 非 root 用户运行 (安全)
#   - HEALTHCHECK 健康检查
#   - 最小化攻击面

# ==================== Stage 1: Builder ====================
FROM python:3.11-slim AS builder

WORKDIR /build

# 安装编译依赖 (仅在构建阶段需要)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 层缓存
COPY requirements.txt .

# 安装 Python 依赖到虚拟环境
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt || true

# ==================== Stage 2: Runtime ====================
FROM python:3.11-slim AS runtime

# 元数据
LABEL maintainer="AGV-TMS Team"
LABEL description="AGV Traffic Management System - Backend API"
LABEL version="1.8.0"

# 安装运行时依赖 (最小集)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl && \
    rm -rf /var/lib/apt/lists/* && \
    # 创建非 root 用户
    useradd -m -u 1000 agvtms && \
    mkdir -p /app /app/logs /tmp/agvtms && \
    chown -R agvtms:agvtms /app /tmp/agvtms

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# 切换工作目录
WORKDIR /app

# 复制应用代码
COPY --chown=agvtms:agvtms . .

# 环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV ENVIRONMENT=production
ENV HOST=0.0.0.0
ENV PORT=8000
LOG_LEVEL=INFO

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:${PORT}/health || exit 1

# 暴露端口
EXPOSE ${PORT}

# 非 root 用户启动
USER agvtms

# 使用 Gunicorn + Uvicorn workers (生产级多进程)
CMD ["gunicorn", "app.main:app", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--worker-tmp-dir", "/dev/shm", \
     "--timeout", "120", \
     "--keep-alive", "5", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
