import { useEffect, useState } from 'react'
import {
  Table,
  Button,
  Modal,
  Input,
  Space,
  message,
  Tag,
  Select,
  Card,
  Statistic,
  Row,
  Col,
  Alert,
  Typography,
  Tooltip,
  Upload,
  Dropdown
} from 'antd'
import {
  ImportOutlined,
  DeleteOutlined,
  DownloadOutlined,
  ReloadOutlined,
  StopOutlined,
  CheckCircleOutlined,
  SearchOutlined,
  UploadOutlined,
  DownOutlined
} from '@ant-design/icons'
import {
  getAccounts,
  getAccountStats,
  importAccounts,
  updateAccount,
  deleteAccount,
  batchDeleteAccounts,
  exportAccounts,
  getAccountBatches
} from '../api'
import dayjs from 'dayjs'

const { Text } = Typography

const STATUS_MAP = {
  available: { text: '可分配', color: 'green' },
  assigned: { text: '已提取', color: 'blue' },
  invalid: { text: '已失效', color: 'red' }
}

export default function AccountPool() {
  const [data, setData] = useState([])
  const [stats, setStats] = useState({})
  const [batches, setBatches] = useState([])
  const [loading, setLoading] = useState(false)
  const [selectedKeys, setSelectedKeys] = useState([])
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 })
  const [filters, setFilters] = useState({})
  const [keyword, setKeyword] = useState('')

  const [importOpen, setImportOpen] = useState(false)
  const [importText, setImportText] = useState('')
  const [importing, setImporting] = useState(false)

  useEffect(() => {
    loadData()
  }, [pagination.current, filters])

  useEffect(() => {
    loadStats()
    loadBatches()
  }, [])

  const loadData = async () => {
    setLoading(true)
    try {
      const res = await getAccounts({
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

  const loadStats = async () => {
    try {
      setStats(await getAccountStats())
    } catch (error) {
      console.error('加载统计失败:', error)
    }
  }

  const loadBatches = async () => {
    try {
      setBatches(await getAccountBatches())
    } catch (error) {
      console.error('加载批次失败:', error)
    }
  }

  const refreshAll = () => {
    loadData()
    loadStats()
    loadBatches()
  }

  const handleFileSelect = (file) => {
    const reader = new FileReader()

    reader.onload = e => {
      setImportText(e.target.result || '')
      message.success(`已读取 ${file.name}`)
    }
    reader.onerror = () => message.error('文件读取失败')
    reader.readAsText(file, 'utf-8')

    // 返回 false 阻止 antd 自动上传
    return false
  }

  const handleImport = async () => {
    const text = importText.trim()
    if (!text) {
      message.warning('请粘贴内容或选择文件')
      return
    }

    // JSON 数组走 application/json，其余（JSONL / 分隔符文本）走 text/plain
    let payload = text
    let contentType = 'text/plain'

    if (text.startsWith('[')) {
      try {
        payload = JSON.parse(text)
        contentType = 'application/json'
      } catch {
        message.error('JSON 格式有误，请检查内容是否完整')
        return
      }
    }

    setImporting(true)
    try {
      const res = await importAccounts(payload, contentType)
      message.success(
        `导入完成：成功 ${res.success_count}，跳过 ${res.skipped_count}，失败 ${res.failed_count}`
      )

      if (res.errors?.length) {
        Modal.info({
          title: '部分记录未导入',
          content: (
            <div style={{ maxHeight: 300, overflow: 'auto' }}>
              {res.errors.map((e, i) => <div key={i}>{e}</div>)}
            </div>
          )
        })
      }

      setImportOpen(false)
      setImportText('')
      refreshAll()
    } catch (error) {
      console.error('导入失败:', error)
    } finally {
      setImporting(false)
    }
  }

  const handleToggleStatus = async (record) => {
    const toInvalid = record.status !== 'invalid'

    try {
      await updateAccount(record.id, { status: toInvalid ? 'invalid' : 'available' })
      message.success(toInvalid ? '已标记为失效' : '已恢复为可分配')
      refreshAll()
    } catch (error) {
      console.error('操作失败:', error)
    }
  }

  const handleDelete = (record) => {
    Modal.confirm({
      title: '确认删除',
      content: `确定要删除账号 ${record.email || record.id} 吗？`,
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          await deleteAccount(record.id)
          message.success('删除成功')
          refreshAll()
        } catch (error) {
          console.error('删除失败:', error)
        }
      }
    })
  }

  const handleBatchDelete = () => {
    Modal.confirm({
      title: '批量删除',
      content: `已选中 ${selectedKeys.length} 个账号，已被卡密提取的账号会自动跳过。确定继续？`,
      okButtonProps: { danger: true },
      onOk: async () => {
        try {
          const res = await batchDeleteAccounts(selectedKeys)
          message.success(res.message)
          setSelectedKeys([])
          refreshAll()
        } catch (error) {
          console.error('批量删除失败:', error)
        }
      }
    })
  }

  const handleExport = async (withMeta = false) => {
    try {
      const blob = await exportAccounts({
        ...filters,
        format: 'json',
        ...(withMeta ? { with_meta: 1 } : {})
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `accounts_${Date.now()}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch (error) {
      console.error('导出失败:', error)
    }
  }

  const handleFilterChange = (key, value) => {
    setFilters(prev => ({ ...prev, [key]: value }))
    setPagination(prev => ({ ...prev, current: 1 }))
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    {
      title: '邮箱',
      dataIndex: 'email',
      width: 220,
      render: v => (v ? <Text copyable={{ text: v }}>{v}</Text> : <Text type="secondary">-</Text>)
    },
    {
      title: '密码',
      dataIndex: 'password',
      width: 140,
      render: v => (v ? <Text copyable={{ text: v }}>{v}</Text> : <Text type="secondary">-</Text>)
    },
    {
      title: 'API Key',
      dataIndex: 'api_key',
      width: 220,
      render: v =>
        v ? (
          <Tooltip title={v}>
            <Text copyable={{ text: v }} style={{ fontFamily: 'monospace', fontSize: 12 }}>
              {v.length > 22 ? `${v.slice(0, 22)}...` : v}
            </Text>
          </Tooltip>
        ) : <Text type="secondary">-</Text>
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 100,
      render: status => (
        <Tag color={STATUS_MAP[status]?.color}>{STATUS_MAP[status]?.text || status}</Tag>
      )
    },
    {
      title: '绑定卡密',
      dataIndex: 'card_key',
      width: 180,
      render: v => (v ? <Text code style={{ fontSize: 12 }}>{v}</Text> : <Text type="secondary">-</Text>)
    },
    { title: '批次', dataIndex: 'batch_id', width: 170, ellipsis: true },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      width: 150,
      render: time => (time ? dayjs(time).format('YYYY-MM-DD HH:mm') : '-')
    },
    {
      title: '操作',
      width: 150,
      fixed: 'right',
      render: (_, record) => (
        <Space size={0}>
          <Button
            type="link"
            size="small"
            icon={record.status === 'invalid' ? <CheckCircleOutlined /> : <StopOutlined />}
            onClick={() => handleToggleStatus(record)}
          >
            {record.status === 'invalid' ? '恢复' : '失效'}
          </Button>

          {record.status !== 'assigned' && (
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
      <Row gutter={16} style={{ marginBottom: 16 }}>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="账号总数" value={stats.total || 0} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic
              title="可分配库存"
              value={stats.available || 0}
              valueStyle={{ color: (stats.available || 0) > 0 ? '#52c41a' : '#ff4d4f' }}
            />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="已被提取" value={stats.assigned || 0} valueStyle={{ color: '#1677ff' }} />
          </Card>
        </Col>
        <Col xs={12} sm={6}>
          <Card size="small">
            <Statistic title="已失效" value={stats.invalid || 0} valueStyle={{ color: '#ff4d4f' }} />
          </Card>
        </Col>
      </Row>

      {(stats.available || 0) === 0 && (
        <Alert
          type="warning"
          showIcon
          message="账号池已空"
          description="当前没有可分配的账号，用户使用卡密提取时会失败，请尽快导入账号。"
          style={{ marginBottom: 16 }}
        />
      )}

      <div style={{ marginBottom: 16, display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <Button type="primary" icon={<ImportOutlined />} onClick={() => setImportOpen(true)}>
          导入账号
        </Button>

        <Dropdown
          menu={{
            items: [
              {
                key: 'plain',
                label: '导出 JSON（仅关键字段）',
                onClick: () => handleExport(false)
              },
              {
                key: 'meta',
                label: '导出 JSON（含状态与卡密）',
                onClick: () => handleExport(true)
              }
            ]
          }}
        >
          <Button icon={<DownloadOutlined />}>
            导出 <DownOutlined />
          </Button>
        </Dropdown>

        <Button icon={<ReloadOutlined />} onClick={refreshAll}>
          刷新
        </Button>

        {selectedKeys.length > 0 && (
          <Button danger icon={<DeleteOutlined />} onClick={handleBatchDelete}>
            批量删除 ({selectedKeys.length})
          </Button>
        )}

        <Input
          placeholder="搜索邮箱"
          allowClear
          style={{ width: 200 }}
          value={keyword}
          onChange={e => setKeyword(e.target.value)}
          onPressEnter={() => handleFilterChange('keyword', keyword || undefined)}
          suffix={
            <SearchOutlined
              style={{ cursor: 'pointer' }}
              onClick={() => handleFilterChange('keyword', keyword || undefined)}
            />
          }
        />

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

        <Select
          placeholder="筛选批次"
          allowClear
          style={{ width: 200 }}
          onChange={value => handleFilterChange('batch_id', value)}
        >
          {batches.map(b => (
            <Select.Option key={b} value={b}>{b}</Select.Option>
          ))}
        </Select>
      </div>

      <Table
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
        scroll={{ x: 1500 }}
        rowSelection={{
          selectedRowKeys: selectedKeys,
          onChange: setSelectedKeys,
          getCheckboxProps: r => ({ disabled: r.status === 'assigned' })
        }}
        pagination={{
          ...pagination,
          showTotal: total => `共 ${total} 个账号`,
          onChange: page => setPagination(prev => ({ ...prev, current: page }))
        }}
      />

      <Modal
        title="导入账号到账号池"
        open={importOpen}
        onCancel={() => setImportOpen(false)}
        onOk={handleImport}
        confirmLoading={importing}
        okText="开始导入"
        width={680}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          message="标准格式：JSON 数组（注册器 result/accounts.json）"
          description={
            <pre style={{ margin: '8px 0 0', fontSize: 12, lineHeight: 1.7 }}>
{`[
  {"email": "a@mail.com", "api_key": "apikey_xxx", "api_key_id": "key_xxx"},
  {"email": "b@mail.com", "api_key": "apikey_yyy", "api_key_id": "key_yyy"}
]

兼容旧格式（便于一次性迁移）：
· JSONL      每行一个 JSON（success.jsonl）
· 分隔符文本 逗号 / Tab / ---- 分隔，字段按内容识别
· 单列       仅 api_key（apikeys.txt）`}
            </pre>
          }
          style={{ marginBottom: 16 }}
        />

        <Space style={{ marginBottom: 12 }}>
          <Upload
            accept=".jsonl,.json,.txt,.csv"
            showUploadList={false}
            beforeUpload={handleFileSelect}
          >
            <Button icon={<UploadOutlined />}>选择文件</Button>
          </Upload>

          <Text type="secondary" style={{ fontSize: 12 }}>
            支持 .jsonl / .json / .txt / .csv，也可直接粘贴
          </Text>
        </Space>

        <Input.TextArea
          rows={12}
          value={importText}
          onChange={e => setImportText(e.target.value)}
          placeholder={'粘贴内容，或点击上方「选择文件」读取\n\n{"email":"a@mail.com","api_key":"apikey_xxx"}'}
          style={{ fontFamily: 'monospace', fontSize: 12 }}
        />

        <div style={{ marginTop: 8, display: 'flex', justifyContent: 'space-between' }}>
          <Text type="secondary" style={{ fontSize: 12 }}>
            相同 API Key 自动去重；JSONL 中 status 非 keyed 的记录会跳过
          </Text>
          {importText && (
            <Text type="secondary" style={{ fontSize: 12 }}>
              {importText.trim().split('\n').filter(l => l.trim() && !l.trim().startsWith('#')).length} 行待导入
            </Text>
          )}
        </div>
      </Modal>
    </div>
  )
}
