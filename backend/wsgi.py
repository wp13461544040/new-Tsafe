"""WSGI 入口（gunicorn 用）。

🔴 **必须用单 worker 多线程，不能用多 worker**：
   任务执行器（`backend/executor.py`）把状态放在**进程内**——
     · `_cancel_flags` 是模块级 dict ⇒ 多 worker 下各持一份，
       取消请求可能落到没有该任务的 worker 上，表现是"点了取消没反应"；
     · 正在跑的任务是 `threading.Thread`，属于某个具体进程；
     · 站点配置由 `apply_site_config()` 改模块级变量，只在处理该请求的 worker 生效。
   所以 Dockerfile 里是 `-w 1 --threads 8`：并发靠线程，不靠进程。

   要横向扩容得先把这些状态外置（Redis / 数据库），那是另一档改动。
"""
import sys
from pathlib import Path

# 让 `from backend...` / `from src...` 都能解析 —— 容器里工作目录是 /app，
# 项目根就是它，但显式加一次更稳（gunicorn 的 chdir 行为依部署方式而异）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app import create_app  # noqa: E402

app = create_app()
