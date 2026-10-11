# 复用此前构建使用的本地 Python 基础镜像及 CUDA 依赖层。
FROM python:3.11-slim

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
RUN python -m pip install --no-cache-dir -e .

EXPOSE 8001
CMD ["python", "-m", "chinese_legal_agent.api", "--host", "0.0.0.0", "--port", "8001"]
