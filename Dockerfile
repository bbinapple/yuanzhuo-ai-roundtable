FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    YUANZHUO_EXPORT_DIR=/data/exports

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --shell /usr/sbin/nologin yuanzhuo \
    && mkdir -p /data/exports /home/yuanzhuo/.yuanzhuo \
    && chown -R yuanzhuo:yuanzhuo /data /home/yuanzhuo/.yuanzhuo /app

USER yuanzhuo

EXPOSE 8888

CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8888", "--log-level", "warning"]
