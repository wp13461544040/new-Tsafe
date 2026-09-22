# 🔴 构建上下文是**项目根目录**，不是 backend/ ——
#    代码里有 `from src import config`（注册器核心在 src/），
#    只复制 backend/ 的镜像跑起来会直接 ModuleNotFoundError: No module named 'src'。
#    对应 compose 里的 `context: .` + `dockerfile: backend/Dockerfile`。
FROM python:3.12-slim

# 不写 .pyc、日志不缓冲（不设 PYTHONUNBUFFERED 的话容器日志会延迟甚至丢）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖再拷代码：改代码不会让这一层缓存失效
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# 业务代码。两个包都要 —— 见顶部说明
COPY backend/ ./backend/
COPY src/ ./src/

# 数据目录：SQLite 库与自动生成的 JWT 密钥都在这里。
# compose 会把它挂成 volume，容器重建后数据与密钥都不丢。
RUN mkdir -p /app/backend/data /app/result /app/exports

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/api/health',timeout=3).status==200 else 1)"

# 🔴 `-w 1 --threads 8`：单进程多线程。
#    任务执行器的状态（取消标记、运行中的线程、注入的站点配置）都在进程内，
#    多 worker 会让它们各自为政 —— 详见 backend/wsgi.py 的说明。
#    `--timeout 0` 关掉 worker 超时：注册一批账号可能跑好几分钟，
#    默认 30s 会让 gunicorn 误判 worker 卡死并重启它，任务直接被杀。
CMD ["gunicorn", "-w", "1", "--threads", "8", "--timeout", "0", \
     "-b", "0.0.0.0:5000", "backend.wsgi:app"]
