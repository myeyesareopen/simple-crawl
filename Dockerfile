# 使用官方 Python 基础镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 将 requirements.txt 复制到工作目录
COPY requirements.txt .

# 安装依赖
RUN python --version && \
    pip --version && \
    http_proxy=http://192.168.1.2:10809 \
    https_proxy=http://192.168.1.2:10809 \
    pip install --no-cache-dir -r requirements.txt

# 将当前目录复制到工作目录
COPY . .

# 暴露应用端口
EXPOSE 8000

# 启动 FastAPI 应用
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
