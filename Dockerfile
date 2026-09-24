FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# 可选：构建时指定 pip 镜像源（国内网络加速）。默认官方源，不指定时不改变行为。
ARG PIP_INDEX_URL=https://pypi.org/simple

WORKDIR /app

# curl 供容器健康检查使用
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -i ${PIP_INDEX_URL} -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
