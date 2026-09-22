import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, InputNumber, Select, Space, message, Tag, Progress, Tooltip, Typography, Alert } from 'antd'
import { PlusOutlined, StopOutlined, ReloadOutlined } from '@ant-design/icons'
import { getTasks, createTask, cancelTask, getMailConfigs } from '../api'
import dayjs from 'dayjs'

const { Text } = Typography

export default function TaskManagement() {
  const [data, setData] = useState([])
  const [mailConfigs, setMailConfigs] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    loadData()
    loadMailConfigs()
  }, [])

  // 有任务在跑时自动轮询 —— 注册一个账号要十几秒，不轮询的话
  // 用户只能手动刷新才能看到进度，很容易以为卡住了
  useEffect(() => {
    const active = data.some(t => ['pending', 'running'].includes(t.status))
    if (!active) return

    const timer = setInterval(() => loadData(true), 3000)
    return () => clearInterval(timer)
  }, [data])

  const loadData = async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const res = await getTasks({ page: 1, page_size: 100 })
      setData(res.items)
    } catch (error) {
      console.error('加载失败:', error)
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const loadMailConfigs = async () => {
    try {
      const res = await getMailConfigs({ page: 1, page_size: 100 })
      setMailConfigs(res.items.filter(c => c.is_active))
    } catch (error) {
      console.error('加载邮箱配置失败:', error)
    }
  }

  const handleSubmit = async (values) => {
    try {
      await createTask(values)
      message.success('任务创建成功')
      setModalOpen(false)
      form.resetFields()
      loadData()
    } catch (error) {
      console.error('创建失败:', error)
    }
  }

  const statusMap = {
    pending: { text: '待执行', color: 'default' },
    running: { text: '运行中', color: 'processing' },
    completed: { text: '已完成', color: 'success' },
    cancelled: { text: '已取消', color: 'warning' },
    failed: { text: '失败', color: 'error' }
  }

  const handleCancel = (record) => {
    Modal.confirm({
      title: '取消任务',
      content: '当前正在注册的账号会跑完（可能还需十几秒），之后停止。确定取消？',
      onOk: async () => {
        try {
          const res = await cancelTask(record.id)
          message.success(res.message || '已取消')
          loadData()
        } catch (error) {
          console.error('取消失败:', error)
        }
      }
    })
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '任务名称', dataIndex: 'name', ellipsis: true },
    { title: '目标', dataIndex: 'count', width: 70 },
    { title: '并发', dataIndex: 'concurrency', width: 70 },
    {
      title: '邮箱配置',
      dataIndex: 'mail_config_name',
      width: 130,
      ellipsis: true,
      render: v => v || <Text type="secondary">-</Text>
    },
    { 
      title: '状态', 
      dataIndex: 'status',
      width: 100,
      render: (status) => (
        <Tag color={statusMap[status]?.color}>{statusMap[status]?.text || status}</Tag>
      )
    },
    {
      title: '进度',
      width: 170,
      render: (_, record) => (
        <Progress
          percent={record.count ? Math.round((record.progress / record.count) * 100) : 0}
          size="small"
          status={
            record.status === 'failed' ? 'exception'
              : record.status === 'completed' ? 'success'
                : record.status === 'running' ? 'active' : 'normal'
          }
          format={() => `${record.progress}/${record.count}`}
        />
      )
    },
    { 
      title: '成功/失败', 
      width: 90,
      render: (_, record) => (
        <span>
          <Text type="success">{record.success_count}</Text>
          {' / '}
          <Text type="danger">{record.failed_count}</Text>
        </span>
      )
    },
    {
      title: '错误信息',
      dataIndex: 'error_message',
      width: 180,
      ellipsis: true,
      render: v => v
        ? <Tooltip title={v}><Text type="danger" style={{ fontSize: 12 }}>{v}</Text></Tooltip>
        : <Text type="secondary">-</Text>
    },
    { 
      title: '创建时间', 
      dataIndex: 'created_at',
      width: 150,
      render: (time) => dayjs(time).format('MM-DD HH:mm')
    },
    {
      title: '操作',
      width: 90,
      fixed: 'right',
      render: (_, record) => (
        ['pending', 'running'].includes(record.status) && (
          <Button
            type="link"
            size="small"
            danger
            icon={<StopOutlined />}
            onClick={() => handleCancel(record)}
          >
            取消
          </Button>
        )
      )
    }
  ]

  const hasRunning = data.some(t => ['pending', 'running'].includes(t.status))

  return (
    <div>
      {mailConfigs.length === 0 && (
        <Alert
          type="warning"
          showIcon
          message="还没有可用的邮箱配置"
          description="任务执行使用「邮箱配置」页里的设置（不读 .env 文件），请先去添加并启用一个配置。"
          style={{ marginBottom: 16 }}
        />
      )}

      <div style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center' }}>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => setModalOpen(true)}
          disabled={mailConfigs.length === 0}
        >
          创建任务
        </Button>

        <Button icon={<ReloadOutlined />} onClick={() => loadData()}>
          刷新
        </Button>

        {hasRunning && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            有任务运行中，每 3 秒自动刷新进度
          </Text>
        )}
      </div>

      <Table
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
        scroll={{ x: 1300 }}
      />

      <Modal
        title="创建注册任务"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
      >
        <Form form={form} onFinish={handleSubmit} layout="vertical">
          <Form.Item label="任务名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="如：批量注册-20241220" />
          </Form.Item>

          {/* 用 InputNumber 而非 <Input type="number"> —— 后者 onChange 交上来的是
              **字符串**，后端 `count <= 0` 会抛 TypeError 变 500 */}
          <Form.Item
            label="注册数量"
            name="count"
            rules={[{ required: true, message: '请输入注册数量' }]}
          >
            <InputNumber min={1} max={1000} style={{ width: '100%' }} placeholder="如：10" />
          </Form.Item>

          <Form.Item
            label="并发数"
            name="concurrency"
            initialValue={2}
            rules={[{ required: true, message: '请输入并发数' }]}
            extra="可手动输入 1-20。并发越高越快，但邮箱服务和站点都可能限流，建议从 2-3 起步"
          >
            <InputNumber min={1} max={20} style={{ width: '100%' }} placeholder="如：2" />
          </Form.Item>

          <Form.Item label="邮箱配置" name="mail_config_id" rules={[{ required: true }]}>
            <Select placeholder="选择邮箱配置">
              {mailConfigs.map(c => (
                <Select.Option key={c.id} value={c.id}>{c.name}</Select.Option>
              ))}
            </Select>
          </Form.Item>

          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">创建</Button>
              <Button onClick={() => setModalOpen(false)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
