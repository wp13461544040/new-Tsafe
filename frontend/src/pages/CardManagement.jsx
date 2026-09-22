import { useEffect, useState } from 'react'
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  InputNumber,
  Space,
  message,
  Tag,
  Select,
  Progress,
  Tooltip,
  Alert,
  Typography
} from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  EditOutlined,
  StopOutlined,
  CheckCircleOutlined,
  ImportOutlined
} from '@ant-design/icons'
import {
  getCards,
  generateCards,
  updateCard,
  deleteCard,
  getBatches,
  importCards
} from '../api'
import dayjs from 'dayjs'

const { Text } = Typography

const STATUS_MAP = {
  unused: { text: '未使用', color: 'green' },
  partial: { text: '部分提取', color: 'blue' },
  used: { text: '已用完', color: 'default' },
  disabled: { text: '已禁用', color: 'red' }
}

export default function CardManagement() {
  const [data, setData] = useState([])
  const [batches, setBatches] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState(null)
  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importing, setImporting] = useState(false)
  const [form] = Form.useForm()
  const [editForm] = Form.useForm()
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 })
  const [filters, setFilters] = useState({})

  useEffect(() => {
    loadData()
  }, [pagination.current, filters])

  useEffect(() => {
    loadBatches()
  }, [])

  const loadData = async () => {
    setLoading(true)
    try {
      const res = await getCards({
        page: pagination.current,
        page_size: pagination.pageSize,
        ...filters
      })
      setData(res.items)
      setPagination(prev => ({ ...prev, total: res.total }))
    } catch (error) {
      console.error('加载失败:', error)
    } finally {
      setLoading(false)
    }
  }

  const loadBatches = async () => {
    try {
      setBatches(await getBatches())
    } catch (error) {
      console.error('加载批次失败:', error)
    }
  }

  const handleGenerate = async (values) => {
    try {
      const res = await generateCards(values)
      message.success(`成功生成 ${res.count} 个卡密，每个可提取 ${res.quota} 个账号`)
      setModalOpen(false)
      form.resetFields()
      loadData()
      loadBatches()
    } catch (error) {
      console.error('生成失败:', error)
    }
  }

  const openEdit = (record) => {
    setEditing(record)
    editForm.setFieldsValue({
      quota: record.quota,
      remarks: record.remarks
    })
  }

  const handleEdit = async (values) => {
    try {
      await updateCard(editing.id, values)
      message.success('更新成功')
      setEditing(null)
      loadData()
    } catch (error) {
      console.error('更新失败:', error)
    }
  }

  const handleToggleDisable = async (record) => {
    const disabling = record.status !== 'disabled'

    try {
      await updateCard(record.id, {
        status: disabling
          ? 'disabled'
          : record.extracted_count <= 0
            ? 'unused'
            : record.remaining > 0
              ? 'partial'
              : 'used'
      })
      message.success(disabling ? '已禁用该卡密' : '已恢复该卡密')
      loadData()
    } catch (error) {
      console.error('操作失败:', error)
    }
  }

  const handleDelete = (record) => {
    Modal.confirm({
      title: '确认删除',
      content: `删除后无法恢复，确定要删除卡密 ${record.card_key} 吗？`,
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteCard(record.id)
          message.success('删除成功')
          loadData()
        } catch (error) {
          console.error('删除失败:', error)
        }
      }
    })
  }

  const handleImport = async () => {
    const text = importText.trim()
    if (!text) {
      message.warning('请粘贴要导入的卡密')
      return
    }

    setImporting(true)
    try {
      const res = await importCards(text)
      message.success(
        `导入完成：成功 ${res.success_count}，跳过 ${res.skipped_count}，失败 ${res.failed_count}`
      )

      if (res.errors?.length) {
        Modal.info({
          title: '部分记录未导入',
          content: (
            <div style={{ maxHeight: 300, overflow: 'auto' }}>
              {res.errors.map((e, i) => (
                <div key={i}>{e}</div>
              ))}
            </div>
          )
        })
      }

      setImportOpen(false)
      setImportText('')
      loadData()
      loadBatches()
    } catch (error) {
      console.error('导入失败:', error)
    } finally {
      setImporting(false)
    }
  }

  const handleFilterChange = (key, value) => {
    setFilters(prev => ({ ...prev, [key]: value }))
    setPagination(prev => ({ ...prev, current: 1 }))
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    {
      title: '卡密',
      dataIndex: 'card_key',
      width: 200,
      render: key => <Text copyable={{ text: key }} code style={{ fontSize: 12 }}>{key}</Text>
    },
    {
      title: '额度使用',
      width: 190,
      render: (_, r) => {
        const percent = r.quota ? Math.round((r.extracted_count / r.quota) * 100) : 0
        return (
          <Tooltip title={`已提取 ${r.extracted_count} / 总额度 ${r.quota}`}>
            <Progress
              percent={percent}
              size="small"
              status={r.remaining === 0 ? 'normal' : 'active'}
              format={() => `${r.extracted_count}/${r.quota}`}
            />
          </Tooltip>
        )
      }
    },
    {
      title: '剩余',
      dataIndex: 'remaining',
      width: 80,
      render: v => (
        <Text strong style={{ color: v > 0 ? '#52c41a' : '#ff4d4f' }}>{v}</Text>
      )
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: status => (
        <Tag color={STATUS_MAP[status]?.color}>{STATUS_MAP[status]?.text || status}</Tag>
      )
    },
    { title: '批次', dataIndex: 'batch_id', width: 170, ellipsis: true },
    {
      title: '有效期',
      dataIndex: 'expires_at',
      width: 130,
      render: v => (v ? dayjs(v).format('YYYY-MM-DD') : <Text type="secondary">永久</Text>)
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 150,
      render: time => dayjs(time).format('YYYY-MM-DD HH:mm')
    },
    {
      title: '操作',
      width: 190,
      fixed: 'right',
      render: (_, record) => (
        <Space size={0}>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>
            编辑
          </Button>

          <Button
            type="link"
            size="small"
            icon={record.status === 'disabled' ? <CheckCircleOutlined /> : <StopOutlined />}
            onClick={() => handleToggleDisable(record)}
          >
            {record.status === 'disabled' ? '恢复' : '禁用'}
          </Button>

          {record.extracted_count === 0 && (
            <Button
              type="link"
              size="small"
              danger
              icon={<DeleteOutlined />}
              onClick={() => handleDelete(record)}
            >
              删除
            </Button>
          )}
        </Space>
      )
    }
  ]

  return (
    <div>
      <Alert
        type="info"
        showIcon
        message="卡密用于对外提取账号，额度决定单个卡密可提取的账号数量"
        description="用户在 /extract 页面输入卡密即可自助提取，账号来源为「账号池」中状态为可分配的账号。"
        style={{ marginBottom: 16 }}
      />

      <div style={{ marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
          生成卡密
        </Button>

        <Button icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>
          导入卡密
        </Button>

        <Select
          placeholder="筛选批次"
          allowClear
          style={{ width: 200 }}
          onChange={value => handleFilterChange('batch_id', value)}
        >
          {batches.map(batch => (
            <Select.Option key={batch} value={batch}>{batch}</Select.Option>
          ))}
        </Select>

        <Select
          placeholder="筛选状态"
          allowClear
          style={{ width: 130 }}
          onChange={value => handleFilterChange('status', value)}
        >
          {Object.entries(STATUS_MAP).map(([value, cfg]) => (
            <Select.Option key={value} value={value}>{cfg.text}</Select.Option>
          ))}
        </Select>
      </div>

      <Table
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
        scroll={{ x: 1300 }}
        pagination={{
          ...pagination,
          showTotal: total => `共 ${total} 个卡密`,
          onChange: page => setPagination(prev => ({ ...prev, current: page }))
        }}
      />

      {/* 生成卡密 */}
      <Modal
        title="生成卡密"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Form form={form} onFinish={handleGenerate} layout="vertical" initialValues={{ quota: 1 }}>
          <Form.Item
            label="生成数量"
            name="count"
            rules={[{ required: true, message: '请输入生成数量' }]}
            extra="本次要生成多少个卡密"
          >
            <InputNumber min={1} max={1000} style={{ width: '100%' }} placeholder="如：10" />
          </Form.Item>

          <Form.Item
            label="单卡可提取账号数（额度）"
            name="quota"
            rules={[{ required: true, message: '请输入额度' }]}
            extra="每个卡密能提取多少个账号，例如填 5 表示一卡可换 5 个账号"
          >
            <InputNumber min={1} max={1000} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            label="批次ID"
            name="batch_id"
            extra="留空则自动生成，如 BATCH-20260922103000"
          >
            <Input placeholder="可留空自动生成" />
          </Form.Item>

          <Form.Item label="有效期（天）" name="expires_days" extra="留空表示永久有效">
            <InputNumber min={1} max={3650} style={{ width: '100%' }} placeholder="如：30" />
          </Form.Item>

          <Form.Item label="备注" name="remarks">
            <Input.TextArea rows={2} placeholder="可选" />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0 }}>
            <Space>
              <Button type="primary" htmlType="submit">生成</Button>
              <Button onClick={() => setModalOpen(false)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 编辑卡密 */}
      <Modal
        title={`编辑卡密 ${editing?.card_key || ''}`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        footer={null}
        destroyOnClose
      >
        <Form form={editForm} onFinish={handleEdit} layout="vertical">
          <Form.Item
            label="总额度"
            name="quota"
            rules={[{ required: true, message: '请输入额度' }]}
            extra={`该卡密已提取 ${editing?.extracted_count || 0} 个，额度不能小于此值`}
          >
            <InputNumber
              min={editing?.extracted_count || 1}
              max={1000}
              style={{ width: '100%' }}
            />
          </Form.Item>

          <Form.Item label="有效期（天）" name="expires_days" extra="填写后从现在开始重新计算，留空不修改">
            <InputNumber min={1} max={3650} style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item label="备注" name="remarks">
            <Input.TextArea rows={2} />
          </Form.Item>

          <Form.Item style={{ marginBottom: 0 }}>
            <Space>
              <Button type="primary" htmlType="submit">保存</Button>
              <Button onClick={() => setEditing(null)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>

      {/* 导入卡密 */}
      <Modal
        title="导入卡密"
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onOk={handleImport}
        confirmLoading={importing}
        okText="开始导入"
        width={600}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          message="格式：每行一条，卡密和额度用逗号或 Tab 分隔"
          description={
            <pre style={{ margin: '8px 0 0', fontSize: 12 }}>
{`TS-ABC123,5
TS-DEF456,10
TS-GHI789        （省略额度默认为 1）`}
            </pre>
          }
          style={{ marginBottom: 16 }}
        />

        <Input.TextArea
          rows={10}
          value={importText}
          onChange={e => setImportText(e.target.value)}
          placeholder={'TS-ABC123,5\nTS-DEF456,10'}
        />
      </Modal>
    </div>
  )
}
