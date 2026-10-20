FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    TRAE_PANEL_DATA_DIR=/data \
    TZ=Asia/Shanghai

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

VOLUME ["/data"]
EXPOSE 7860

CMD ["python", "-m", "trae_panel"]
