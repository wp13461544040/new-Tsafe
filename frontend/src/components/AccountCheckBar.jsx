import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Alert, Button, Descriptions, Modal, Space, Tag, Tooltip, Typography, message
} from 'antd'
import {
  SafetyCertificateOutlined, StopOutlined, ReloadOutlined
} from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  getAccountCheckStatus, startAccountCheck, stopAccountCheck
} from '../api'

const { Text } = Typography

// 跑动时高频刷新看进度，空闲时降到 20s 只为感知定时任务的启动。
// 不做成常量相同的值：2s 空闲轮询一整天等于给后端白加 43000 次请求。
const POLL_BUSY = 2000
const POLL_IDLE = 20000

/**
 * 巡检状态条：状态展示 + 手动触发 + 中止。
 *
 * 账号池页和系统设置页都要这块，抽成组件避免两份轮询逻辑各自演化
 * （那种重复最后一定会变成"一个页面修了另一个没修"）。
 *
 * @param {object}   props
 * @param {function} props.onFinished 一轮巡检结束时回调，用于刷新外部列表
 * @param {boolean}  props.compact    紧凑模式（账号池页用，不显示配置摘要）
 */
export default function AccountCheckBar({ onFinished, compact = false }) {
  const [status, setStatus] = useState(null)
  const [starting, setStarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [offline, setOffline] = useState(false)

  // 用 ref 存"上一次是否在跑"，用来识别 true -> false 的那一刻。
  // 放 state 会让 effect 依赖它而反复重建定时器。
  const wasRunning = useRef(false)
  const timer = useRef(null)
  // 组件卸载后不要再 setState —— 切页时定时器里的请求仍会返回，
  // 对已卸载组件 setState 在 React 18 下是静默内存泄漏。
  const alive = useRef(true)

  const poll = useCallback(async () => {
    try {
      const res = await getAccountCheckStatus()
      if (!alive.current) return
      setStatus(res)
      setOffline(false)

      if (wasRunning.current && !res.running) {
        // 刚刚跑完 —— 通知外部刷新
        const s = res.last || {}
        message.success(
          `巡检完成：检查 ${s.checked || 0}，正常 ${s.alive || 0}，` +
          `失效 ${s.dead || 0}，无法判定 ${s.unknown || 0}` +
          (s.newly_invalid ? `，新标失效 ${s.newly_invalid}` : '') +
          (s.recovered ? `，自动恢复 ${s.recovered}` : '')
        )
        onFinished?.(s)
      }
      wasRunning.current = res.running
    } catch {
      // silent 请求，这里只做本地降级提示，不弹 toast
      if (alive.current) setOffline(true)
    }
  }, [onFinished])

  useEffect(() => {
    alive.current = true
    poll()
    return () => {
      alive.current = false
      if (timer.current) clearTimeout(timer.current)
    }
  }, [poll])

  // 用 setTimeout 递归而不是 setInterval：请求本身可能比间隔还慢
  // （后端卡住时），setInterval 会把请求堆起来。
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    const gap = status?.running ? POLL_BUSY : POLL_IDLE
    timer.current = setTimeout(poll, gap)
    return () => {
      if (timer.current) clearTimeout(timer.current)
    }
  }, [status, poll])

  const handleStart = async () => {
    setStarting(true)
    try {
      const res = await startAccountCheck({})
      if (res.started) {
        message.success('巡检已开始')
        wasRunning.current = true
        setStatus(prev => ({ ...(prev || {}), running: true }))
        poll()
      }
    } catch {
      // 409（已有一轮在跑）等错误由拦截器提示
    } finally {
      setStarting(false)
    }
  }

  const handleStop = () => {
    Modal.confirm({
      title: '中止巡检',
      content: '已经发出的探测会跑完后停下，已检查的结果不会回滚。确定中止？',
      onOk: async () => {
        setStopping(true)
        try {
          await stopAccountCheck()
          message.success('已请求中止')
          poll()
        } catch {
          /* 拦截器已提示 */
        } finally {
          setStopping(false)
        }
      }
    })
  }

  const last = status?.last || {}
  const running = !!status?.running
  const fmt = t => (t ? dayjs(t).format('MM-DD HH:mm:ss') : '-')

  return (
    <div>
      {offline && (
        <Alert
          type="warning"
          showIcon
          message="暂时读不到巡检状态"
          description="后端可能在重启或网络不通。显示的是最后一次成功获取的数据，会自动重试。"
          style={{ marginBottom: 12 }}
        />
      )}

      {status && !status.scheduler_alive && (
        <Alert
          type="error"
          showIcon
          message="巡检调度线程不在运行"
          description="定时巡检不会触发（手动触发仍可用）。重启后端服务即可恢复。"
          style={{ marginBottom: 12 }}
        />
      )}

      <Space wrap style={{ marginBottom: compact ? 0 : 12 }}>
        <Button
          type="primary"
          icon={<SafetyCertificateOutlined />}
          loading={starting}
          disabled={running}
          onClick={handleStart}
        >
          {running ? '巡检进行中…' : '立即巡检'}
        </Button>

        {running && (
          <Button danger icon={<StopOutlined />} loading={stopping} onClick={handleStop}>
            中止
          </Button>
        )}

        <Button icon={<ReloadOutlined />} onClick={poll}>
          刷新状态
        </Button>

        {status && (
          <Space size={6} wrap>
            {running
              ? <Tag color="processing">运行中</Tag>
              : <Tag>空闲</Tag>}

            {status.enabled
              ? <Tag color="green">定时已开启</Tag>
              : <Tooltip title="定时巡检未开启，只能手动触发。可在系统设置 → 账号巡检里打开。">
                  <Tag color="default">定时未开启</Tag>
                </Tooltip>}

            {status.enabled && status.next_run_at && (
              <Text type="secondary" style={{ fontSize: 12 }}>
                下次：{fmt(status.next_run_at)}
              </Text>
            )}
          </Space>
        )}
      </Space>

      {!compact && last.finished_at && (
        <Descriptions
          size="small"
          column={{ xs: 1, sm: 2, md: 4 }}
          bordered
          items={[
            {
              key: 'when',
              label: '上轮完成',
              children: (
                <Space size={6}>
                  <span>{fmt(last.finished_at)}</span>
                  <Tag color={last.triggered_by === 'schedule' ? 'blue' : 'default'}>
                    {last.triggered_by === 'schedule' ? '定时' : '手动'}
                  </Tag>
                </Space>
              )
            },
            {
              key: 'mode',
              label: '探测方式',
              children: (
                <Tooltip title="只验证认证是否通过，不触发模型推理，不产生 token 消耗">
                  <Tag color="green" style={{ cursor: 'help' }}>零额度探测</Tag>
                </Tooltip>
              )
            },
            {
              key: 'result',
              label: '结果',
              children: (
                <Space size={6} wrap>
                  <Tag color="green">正常 {last.alive || 0}</Tag>
                  <Tag color="red">失效 {last.dead || 0}</Tag>
                  <Tag>无法判定 {last.unknown || 0}</Tag>
                </Space>
              )
            },
            {
              key: 'changed',
              label: '状态变更',
              children: (
                <Space size={6} wrap>
                  {last.newly_invalid
                    ? <Tag color="red">新标失效 {last.newly_invalid}</Tag>
                    : null}
                  {last.recovered
                    ? <Tag color="green">自动恢复 {last.recovered}</Tag>
                    : null}
                  {!last.newly_invalid && !last.recovered && <Text type="secondary">无</Text>}
                </Space>
              )
            },
            ...(last.aborted
              ? [{
                  key: 'aborted',
                  label: '提前结束',
                  span: 4,
                  children: <Text type="warning">{last.aborted}</Text>
                }]
              : []),
            ...(last.ok === false && last.error
              ? [{
                  key: 'error',
                  label: '未能执行',
                  span: 4,
                  children: <Text type="danger">{last.error}</Text>
                }]
              : [])
          ]}
        />
      )}
    </div>
  )
}
