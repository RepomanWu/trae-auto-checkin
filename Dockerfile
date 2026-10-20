# 基础镜像可用 build arg 换成国内镜像加速（默认 Docker Hub / 官方源）
ARG PYTHON_BASE=python:3.11-slim
FROM ${PYTHON_BASE}

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    TRAE_PANEL_DATA_DIR=/data \
    TZ=Asia/Shanghai

COPY requirements.txt ./
# PyPI 源可用 build arg 换国内镜像加速（默认官方源）
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --default-timeout=300 -i ${PIP_INDEX_URL} -r requirements.txt

COPY . .

VOLUME ["/data"]
EXPOSE 7860

CMD ["python", "-m", "trae_panel"]
