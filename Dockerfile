# Docker 官方在 ECR Public 发布的基础镜像，避免 Docker Hub 认证连接超时。
FROM public.ecr.aws/docker/library/python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=Asia/Shanghai

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 tzdata \
    && rm -rf /var/lib/apt/lists/*

# CUDA 运行库由 PyTorch wheel 提供，宿主机提供 NVIDIA 驱动。
# 下载缓存留在构建器中，不进入运行镜像。
RUN --mount=type=cache,target=/root/.cache/pip python -m pip install "torch>=2,<3" \
    --index-url https://download.pytorch.org/whl/cu128

COPY pyproject.toml README.md ./
COPY src ./src
# 保留源码目录布局，让重排模型和法律资源仍从 /app/models、/app/laws 读取。
# 网络设置也传给安装 setuptools/wheel 的构建隔离子进程。
# 放在 CUDA 安装之后，调整这些参数不会使 CUDA 下载层失效。
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_DEFAULT_TIMEOUT=120
ARG PIP_RETRIES=10
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install -e .

EXPOSE 8001
CMD ["python", "-m", "ai_agent_learning.api", "--host", "0.0.0.0", "--port", "8001"]
