import { useEffect, useState } from 'react'
import { Table, Button, Modal, Form, Input, InputNumber, Select, Space, message, Tag, Switch, Alert } from 'antd'
import { PlusOutlined, EditOutlined, DeleteOutlined, ApiOutlined } from '@ant-design/icons'
import { getMailConfigs, addMailConfig, updateMailConfig, toggleMailConfig, deleteMailConfig, testMailConfig } from '../api'

// 后端元信息集中在一处 —— 新增后端只改这个表，不用去翻散落各处的三元判断
const BACKENDS = {
  cf: { label: 'CF Worker', color: 'blue', hint: '建邮箱免费，收信走收件箱索引' },
  remail: { label: 'Remail', color: 'orange', hint: '每建一个邮箱真实下单扣积分' },
  moemail: { label: 'MoeMail', color: 'green', hint: '自建服务，建邮箱免费' }
}

// 服务端只接受这几个枚举值，填别的会被直接拒
const EXPIRY_OPTIONS = [
  { value: 3600000, label: '1 小时' },
  { value: 86400000, label: '1 天（推荐）' },
  { value: 604800000, label: '7 天' },
  { value: 0, label: '永久' }
]

export default function MailConfig() {
  const [data, setData] = useState([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [testingId, setTestingId] = useState(null)
  const [form] = Form.useForm()
  const [backend, setBackend] = useState('cf')

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setLoading(true)
    try {
      const res = await getMailConfigs({ page: 1, page_size: 100 })
      setData(res.items)
    } catch (error) {
      console.error('加载失败:', error)
    } finally {
      setLoading(false)
    }
  }

  const handleAdd = () => {
    form.resetFields()
    setEditingId(null)
    setBackend('cf')
    setModalOpen(true)
  }

  const handleEdit = (record) => {
    setEditingId(record.id)
    setBackend(record.backend)
    form.setFieldsValue(record)
    setModalOpen(true)
  }

  const handleSubmit = async (values) => {
    try {
      if (editingId) {
        await updateMailConfig(editingId, values)
        message.success('更新成功')
      } else {
        await addMailConfig(values)
        message.success('添加成功')
      }
      setModalOpen(false)
      loadData()
    } catch (error) {
      console.error('操作失败:', error)
    }
  }

  const handleToggle = async (id, isActive) => {
    try {
      await toggleMailConfig(id)
      message.success(isActive ? '已禁用' : '已启用')
      loadData()
    } catch (error) {
      console.error('操作失败:', error)
    }
  }

  const handleTest = async (record) => {
    setTestingId(record.id)
    try {
      const res = await testMailConfig(record.id)

      if (res.ok) {
        const extra = res.domains?.length
          ? `可用域名：${res.domains.join('、')}`
          : ''
        Modal.success({
          title: '连接正常',
          content: (
            <div>
              <div>{BACKENDS[record.backend]?.label} 服务可达，API Key 有效。</div>
              {extra && <div style={{ marginTop: 8, color: '#888' }}>{extra}</div>}
            </div>
          )
        })
      } else {
        Modal.error({
          title: '连接失败',
          content: <div style={{ wordBreak: 'break-all' }}>{res.error}</div>
        })
      }
    } catch (error) {
      console.error('测试失败:', error)
    } finally {
      setTestingId(null)
    }
  }

  const handleDelete = (id) => {
    Modal.confirm({
      title: '确认删除',
      content: '删除后无法恢复，确定要删除吗？',
      onOk: async () => {
        try {
          await deleteMailConfig(id)
          message.success('删除成功')
          loadData()
        } catch (error) {
          console.error('删除失败:', error)
        }
      }
    })
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', width: 60 },
    { title: '名称', dataIndex: 'name' },
    { 
      title: '后端类型', 
      dataIndex: 'backend',
      render: (b) => (
        <Tag color={BACKENDS[b]?.color || 'default'}>
          {BACKENDS[b]?.label || b}
        </Tag>
      )
    },
    { 
      title: '状态', 
      dataIndex: 'is_active',
      render: (isActive, record) => (
        <Switch
          checked={isActive}
          onChange={() => handleToggle(record.id, isActive)}
        />
      )
    },
    {
      title: '操作',
      width: 220,
      render: (_, record) => (
        <Space size={0}>
          <Button
            type="link"
            size="small"
            icon={<ApiOutlined />}
            loading={testingId === record.id}
            onClick={() => handleTest(record)}
          >
            测试连接
          </Button>
          <Button type="link" size="small" icon={<EditOutlined />} onClick={() => handleEdit(record)}>
            编辑
          </Button>
          <Button type="link" size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(record.id)}>
            删除
          </Button>
        </Space>
      )
    }
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={handleAdd}>
          添加配置
        </Button>
      </div>

      <Table
        columns={columns}
        dataSource={data}
        rowKey="id"
        loading={loading}
        pagination={false}
      />

      <Modal
        title={editingId ? '编辑配置' : '添加配置'}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
        width={600}
      >
        <Form form={form} onFinish={handleSubmit} layout="vertical">
          <Form.Item label="后端类型" name="backend" rules={[{ required: true }]}>
            <Select onChange={setBackend} disabled={!!editingId}>
              {Object.entries(BACKENDS).map(([value, cfg]) => (
                <Select.Option key={value} value={value}>
                  {cfg.label} — {cfg.hint}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>

          <Form.Item label="配置名称" name="name" rules={[{ required: true, message: '请输入配置名称' }]}>
            <Input placeholder="如：CF-邮箱1" />
          </Form.Item>

          {backend === 'cf' && (
            <>
              <Form.Item label="TempMail Base URL" name="tempmail_base" rules={[{ required: true }]}>
                <Input placeholder="https://your-worker.workers.dev" />
              </Form.Item>
              <Form.Item label="Admin Key" name="tempmail_admin_key" rules={[{ required: true }]}>
                <Input.Password placeholder="admin_key" />
              </Form.Item>
              <Form.Item label="域名" name="tempmail_domain" rules={[{ required: true }]}>
                <Input placeholder="example.com" />
              </Form.Item>
            </>
          )}

          {backend === 'remail' && (
            <>
              <Form.Item label="Remail Base URL" name="remail_base" rules={[{ required: true }]}>
                <Input placeholder="https://api.remail.cloud" />
              </Form.Item>
              <Form.Item label="API Key" name="remail_api_key" rules={[{ required: true }]}>
                <Input.Password />
              </Form.Item>
              <Form.Item label="Project ID" name="remail_project_id">
                <InputNumber min={1} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item label="邮箱后缀" name="remail_email_suffix">
                <Input placeholder="@example.com" />
              </Form.Item>
              <Form.Item label="服务模式" name="remail_service_mode">
                <Select>
                  <Select.Option value="code">验证码模式</Select.Option>
                  <Select.Option value="link">链接模式</Select.Option>
                </Select>
              </Form.Item>
            </>
          )}

          {backend === 'moemail' && (
            <>
              <Alert
                type="info"
                showIcon
                message="认证头是 X-API-Key，不是 Bearer"
                description="填错的表现是全局 401，容易被误诊成 key 失效。可用域名由服务端 /api/config 提供。"
                style={{ marginBottom: 16 }}
              />

              <Form.Item
                label="服务地址"
                name="moemail_base"
                rules={[{ required: true, message: '请输入 MoeMail 服务地址' }]}
                extra="不要带尾部斜杠，保存时会自动去掉"
              >
                <Input placeholder="https://your-moemail.example.com" />
              </Form.Item>

              <Form.Item
                label="API Key"
                name="moemail_api_key"
                rules={[{ required: true, message: '请输入 API Key' }]}
              >
                <Input.Password placeholder="在 MoeMail 后台生成" />
              </Form.Item>

              <Form.Item
                label="邮箱域名"
                name="moemail_domain"
                extra="留空则自动取 /api/config 返回的第一个可用域名。不要凭印象填，服务端对无效域名只回 400"
              >
                <Input placeholder="留空自动选择，如 moemail.app" />
              </Form.Item>

              <Form.Item
                label="邮箱有效期"
                name="moemail_expiry_ms"
                initialValue={86400000}
                extra="邮箱过期后收不到信，而台账里只会显示「等不到邮件」。补跑场景建议留足余量"
              >
                <Select>
                  {EXPIRY_OPTIONS.map(o => (
                    <Select.Option key={o.value} value={o.value}>{o.label}</Select.Option>
                  ))}
                </Select>
              </Form.Item>

              <Form.Item
                label="收信轮询间隔（秒）"
                name="moemail_poll_interval"
                initialValue={3}
                rules={[
                  { required: true, message: '请输入轮询间隔' },
                  { type: 'number', min: 1, message: '不能小于 1 秒' }
                ]}
                extra="MoeMail 多跑在 Cloudflare Workers 免费额度上。间隔太小 × 并发数会触发 Error 1102（Worker 超资源限制），届时整个邮箱服务都返回错误页。并发 5 以上建议调到 4-5 秒"
              >
                <InputNumber min={1} max={30} step={0.5} style={{ width: '100%' }} />
              </Form.Item>
            </>
          )}

          <Form.Item>
            <Space>
              <Button type="primary" htmlType="submit">保存</Button>
              <Button onClick={() => setModalOpen(false)}>取消</Button>
            </Space>
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
