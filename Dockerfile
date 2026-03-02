# TrendRadar AI Pipeline - 基于 Python 3.11
FROM python:3.11-slim

WORKDIR /app

# 依赖
COPY pyproject.toml ./
COPY src ./src/
RUN pip install --no-cache-dir -e .

# 配置与日志目录
COPY config ./config/
RUN mkdir -p logs retry_queue

# 默认从 config/config.yaml 读配置，敏感项通过环境变量覆盖
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "pipeline.main"]
