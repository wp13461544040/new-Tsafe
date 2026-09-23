"""任务实时日志缓冲区。

为什么在内存里而不是每行落库：注册一个账号会产生十几条日志，一批 50 个就是
几百条。每行一次 INSERT + commit 会让 SQLite 写锁频繁抢占执行线程，
而日志只是观察手段，不值得付这个代价。

取舍与后果：
- 缓冲区**随进程生命周期**。进程重启即丢 ⇒ 所以执行器会在每批结束和终态时
  把整份快照写进 `register_tasks.log_text`（一次写一整块），
  历史任务和重启后仍能看到日志，只是不再"实时"。
- 单 worker 前提（gunicorn `-w 1`）。多 worker 下各进程缓冲区互不可见，
  轮询会打到没有缓冲的进程上 ⇒ 日志看起来时有时无。这与执行器本身的
  进程内线程模型是同一个约束，不是新引入的限制。

轮询协议：前端带 `after=<seq>` 来，只取增量。`seq` 从 1 开始单调递增，
被环形缓冲挤掉的旧行不会重放（前端据此可知中间有截断）。
"""

import threading
from collections import deque

#: 单任务最多保留的行数。一批 50 个账号约 700 行，2000 行够看完整个尾部。
MAX_LINES = 2000
#: 最多同时保留几个任务的缓冲。任务结束后**不立即丢**（用户往往刚跑完才去看日志，
#: 留着能继续走增量轮询），靠这个上限防止长期运行后内存无界增长。
MAX_TASKS = 20

_buffers: dict[int, deque] = {}
_seqs: dict[int, int] = {}
_lock = threading.Lock()


def append(task_id: int, text: str) -> None:
    """追加一行日志。线程安全（并发 worker 会同时调它）。"""
    from backend.timeutil import now

    line = {
        "seq": 0,
        "ts": now().strftime("%H:%M:%S"),
        "text": str(text).rstrip(),
    }
    with _lock:
        buf = _buffers.get(task_id)
        if buf is None:
            buf = _buffers[task_id] = deque(maxlen=MAX_LINES)
            _seqs[task_id] = 0
        _seqs[task_id] += 1
        line["seq"] = _seqs[task_id]
        buf.append(line)


def read(task_id: int, after: int = 0) -> tuple[list[dict], int]:
    """取 `seq > after` 的行，返回 `(行列表, 当前最大 seq)`。

    缓冲区不存在时返回 `([], 0)` —— 调用方据此回退到数据库里的持久化快照。
    """
    with _lock:
        buf = _buffers.get(task_id)
        if buf is None:
            return [], 0
        lines = [ln for ln in buf if ln["seq"] > after]
        return lines, _seqs.get(task_id, 0)


def has(task_id: int) -> bool:
    with _lock:
        return task_id in _buffers


def dump(task_id: int) -> str:
    """整份快照，用于落库持久化。"""
    with _lock:
        buf = _buffers.get(task_id)
        if not buf:
            return ""
        return "\n".join(f"[{ln['ts']}] {ln['text']}" for ln in buf)


def reset(task_id: int) -> None:
    """清空某任务的缓冲（任务开始时调用，避免重跑同 id 时混入上一轮的行）。"""
    with _lock:
        _buffers[task_id] = deque(maxlen=MAX_LINES)
        _seqs[task_id] = 0
        # 淘汰最早的任务缓冲（dict 保插入序）。已结束的任务日志都落过库，丢掉只损失"增量轮询"。
        while len(_buffers) > MAX_TASKS:
            oldest = next(iter(_buffers))
            if oldest == task_id:
                break
            _buffers.pop(oldest, None)
            _seqs.pop(oldest, None)


def drop(task_id: int) -> None:
    """丢弃缓冲。⚠️ 只在确认已落库后调用，否则日志会凭空消失。"""
    with _lock:
        _buffers.pop(task_id, None)
        _seqs.pop(task_id, None)
