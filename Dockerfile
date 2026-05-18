FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# 安装 ODBC 驱动
RUN apt-get update && \
    apt-get install -y unixodbc unixodbc-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先安装依赖（利用 Docker 层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

EXPOSE 5678

CMD ["python", "wiptrack_server.py"]
