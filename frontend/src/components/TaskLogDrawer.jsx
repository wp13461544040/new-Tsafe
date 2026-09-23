import { useEffect, useRef, useState } from 'react'
import { Drawer, Descriptions, Tag, Progress, Space, Button, Switch, Typography, Alert, Empty, message } from 'antd'
import { CopyOutlined, DownloadOutlined } from '@ant-design/icons'
import { getTaskLogs } from '../api'

const { Text } = Typography

const STATUS_MAP = {
  pending: { text: '待执行', color: 'default' },
  running: { text: '运行中', color: 'processing' },
  completed: { text: '已完成', color: 'success' },
  cancelled: { text: '已取消', color: 'warning' },
  failed: { text: '失败', color: 'error' }
}

const TERMINAL = ['completed', 'cancelled', 'failed']

// 按内容给行上色：一眼能分出成功/失败/批次分隔，纯灰的日志墙很难扫读
function lineColor(text) {
  if (text.includes('✗')) return '#ff7875'
  if (text.includes('✓')) return '#95de64'
  if (text.startsWith('──') || text.startsWith('■')) return '#69c0ff'
  if (text.includes('⚠')) return '#ffc069'
  return '#d9d9d9'
}

export default function TaskLogDrawer({ open, task, onClose }) {
  const [lines, setLines] = useState([])
  const [meta, setMeta] = useState(null)
  const [truncated, setTruncated] = useState(false)
  const [live, setLive] = useState(true)
  const [follow, setFollow] = useState(true)

  // 用 ref 存游标：轮询回调是在 effect 里建的闭包，
  // 读 state 会永远拿到打开抽屉那一刻的旧值 ⇒ 每次都从 0 开始重复拉。
  const afterRef = useRef(0)
  const followRef = useRef(true)
  const bodyRef = useRef(null)

  useEffect(() => { followRef.current = follow }, [follow])

  useEffect(() => {
    if (!open || !task?.id) return

    // 换任务/重开抽屉都要清干净，否则会把上一个任务的日志接在前面
    afterRef.current = 0
    setLines([])
    setMeta(null)
    setTruncated(false)
    setLive(true)

    let timer = null
    let stopped = false

    const tick = async () => {
      try {
        const res = await getTaskLogs(task.id, afterRef.current)
        if (stopped) return

        setMeta(res)
        setLive(res.live)
        if (res.truncated) setTruncated(true)

        if (res.live) {
          if (res.lines?.length) {
            afterRef.current = res.next
            setLines(prev => [...prev, ...res.lines])
          }
        } else if (res.snapshot != null) {
          // 缓冲已丢（进程重启过 / 历史任务）⇒ 一次性拿整块快照
          const text = String(res.snapshot || '')
          setLines(text
            ? text.split('\n').map((raw, i) => ({ seq: i + 1, ts: '', text: raw }))
            : [])
        }

        // 终态且执行线程已退出 ⇒ 停止轮询。
        // 必须同时看 running：status 已落终态但线程还在收尾时，
        // 提前停会漏掉最后几行日志。
        if (TERMINAL.includes(res.status) && !res.running && timer) {
          clearInterval(timer)
          timer = null
        }
      } catch (e) {
        // 任务被删了（可能是另一个标签页操作的）⇒ 记录不存在，再轮询只会一直 404
        if (e?.response?.status === 404 && timer) {
          clearInterval(timer)
          timer = null
          return
        }
        // silent 请求，这里不弹 toast：后端重启时会连续失败，刷屏比没提示更糟
        console.error('拉取任务日志失败:', e)
      }
    }

    tick()
    timer = setInterval(tick, 1500)

    return () => {
      stopped = true
      if (timer) clearInterval(timer)
    }
  }, [open, task?.id])

  // 自动滚到底。关掉开关后不再抢用户的滚动位置（往上翻看历史时很烦）
  useEffect(() => {
    if (!followRef.current || !bodyRef.current) return
    bodyRef.current.scrollTop = bodyRef.current.scrollHeight
  }, [lines])

  const plainText = lines.map(l => (l.ts ? `[${l.ts}] ${l.text}` : l.text)).join('\n')

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(plainText)
      message.success('已复制日志')
    } catch {
      message.error('复制失败，请手动选中复制')
    }
  }

  const handleDownload = () => {
    const blob = new Blob([plainText], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `task-${task?.id}-${task?.name || 'log'}.log`
    a.click()
    URL.revokeObjectURL(url)
  }

  const status = meta?.status || task?.status
  const progress = meta?.progress ?? task?.progress ?? 0
  const count = meta?.count ?? task?.count ?? 0

  return (
    <Drawer
      title={`任务详情 #${task?.id} ${task?.name || ''}`}
      open={open}
      onClose={onClose}
      width={760}
      extra={
        <Space>
          <Switch size="small" checked={follow} onChange={setFollow} />
          <Text type="secondary" style={{ fontSize: 12 }}>自动滚动</Text>
          <Button size="small" icon={<CopyOutlined />} onClick={handleCopy} disabled={!lines.length}>
            复制
          </Button>
          <Button size="small" icon={<DownloadOutlined />} onClick={handleDownload} disabled={!lines.length}>
            下载
          </Button>
        </Space>
      }
    >
      <Descriptions size="small" column={2} bordered style={{ marginBottom: 12 }}>
        <Descriptions.Item label="状态">
          <Tag color={STATUS_MAP[status]?.color}>{STATUS_MAP[status]?.text || status}</Tag>
          {meta?.running && <Text type="secondary" style={{ fontSize: 12 }}>执行中</Text>}
        </Descriptions.Item>
        <Descriptions.Item label="成功/失败">
          <Text type="success">{meta?.success_count ?? task?.success_count ?? 0}</Text>
          {' / '}
          <Text type="danger">{meta?.failed_count ?? task?.failed_count ?? 0}</Text>
        </Descriptions.Item>
        <Descriptions.Item label="进度" span={2}>
          <Progress
            percent={count ? Math.round((progress / count) * 100) : 0}
            size="small"
            status={
              status === 'failed' ? 'exception'
                : status === 'completed' ? 'success'
                  : status === 'running' ? 'active' : 'normal'
            }
            format={() => `${progress}/${count}`}
          />
        </Descriptions.Item>
        {meta?.error_message && (
          <Descriptions.Item label="错误信息" span={2}>
            <Text type="danger" style={{ fontSize: 12 }}>{meta.error_message}</Text>
          </Descriptions.Item>
        )}
      </Descriptions>

      {!live && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message="这是历史日志快照"
          description="实时日志存在服务进程内存中，服务重启或任务过多后只保留落库的快照，不再增量更新。"
        />
      )}

      {truncated && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message="更早的日志已被截断（单任务最多保留最近 2000 行）"
        />
      )}

      <div
        ref={bodyRef}
        style={{
          background: '#141414',
          borderRadius: 6,
          padding: '10px 12px',
          height: 'calc(100vh - 380px)',
          minHeight: 240,
          overflowY: 'auto',
          fontFamily: 'Menlo, Consolas, "Courier New", monospace',
          fontSize: 12,
          lineHeight: 1.7
        }}
      >
        {lines.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<Text type="secondary">暂无日志</Text>}
            style={{ marginTop: 60 }}
          />
        ) : lines.map(l => (
          <div key={l.seq} style={{ color: lineColor(l.text), whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            {l.ts && <span style={{ color: '#595959' }}>{l.ts} </span>}
            {l.text}
          </div>
        ))}
      </div>
    </Drawer>
  )
}
