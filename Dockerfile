# T3 Resolve Batch Service
# 构建：docker build -t t3-resolve:latest .
# 运行：docker run -d -p 5000:5000 --env-file .env --name t3-resolve t3-resolve:latest
#   （.env 参照 .env.example 填好 LLM_BASE_URL 等内网vLLM连接信息）

FROM hlyn3voy1ie4dwn74t.xuanyuan.run/python:3.12-slim

WORKDIR /app

# apt 源换成阿里云镜像——同时兼容新版Debian的deb822格式（/etc/apt/sources.list.d/debian.sources）
# 和旧版格式（/etc/apt/sources.list），哪个存在就改哪个，避免因为基础镜像版本变化导致这行失效
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i 's|deb.debian.org|mirrors.aliyun.com|g; s|security.debian.org|mirrors.aliyun.com|g' \
            /etc/apt/sources.list; \
    fi

# 系统依赖：几乎不需要额外的系统包，openai/fastapi都是纯Python依赖，curl只是给健康检查用
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# pip 源换成阿里云镜像
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ENV PIP_TRUSTED_HOST=mirrors.aliyun.com

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# 非root用户运行，降低容器逃逸风险
RUN useradd --create-home --shell /bin/bash appuser
USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# 生产环境不用 --reload，worker数量按实际负载调整
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "2"]
