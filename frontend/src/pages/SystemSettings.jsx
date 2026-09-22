import { useEffect, useState } from 'react'
import { Card, Form, Input, Button, message, Table, Tabs, Alert, Space, Tag, Typography } from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import {
  getSystemConfig, updateSystemConfig, getOperationLogs,
  getSiteConfig, updateSiteConfig
} from '../api'
import dayjs from 'dayjs'

const { TextArea } = Input
const { Text } = Typography

export default function SystemSettings() {
  const [announcementForm] = Form.useForm()
  const [siteForm] = Form.useForm()
  const [siteFields, setSiteFields] = useState([])
  const [siteLoading, setSiteLoading] = useState(false)
  const [siteSaving, setSiteSaving] = useState(false)
  const [logs, setLogs] = useState([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [saveLoading, setSaveLoading] = useState(false)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 })

  useEffect(() => {
    loadAnnouncement()
    loadSiteConfig()
  }, [])

  useEffect(() => {
    loadLogs()
  }, [pagination.current])

  const loadSiteConfig = async () => {
    setSiteLoading(true)
    try {
      const res = await getSiteConfig()
      setSiteFields(res.fields || [])
      // 表单只回填数据库里存的值；留空意为"沿用 .env"，不要用 active 填满
      const init = {}
      for (const f of res.fields || []) init[f.key] = f.value
      siteForm.setFieldsValue(init)
    } catch (error) {
      console.error('加载站点配置失败:', error)
    } finally {
      setSiteLoading(false)
    }
  }

  const handleSaveSiteConfig = async (values) => {
    setSiteSaving(true)
    try {
      const res = await updateSiteConfig(values)
      message.success(res.message || '已保存并生效')
      loadSiteConfig()
    } catch (error) {
      console.error('保存站点配置失败:', error)
    } finally {
      setSiteSaving(false)
    }
  }

  const loadAnnouncement = async () => {
    try {
      const res = await getSystemConfig('announcement')
      announcementForm.setFieldsValue({ value: res.value || '' })
    } catch (error) {
      console.error('加载公告失败:', error)
    }
  }

  const loadLogs = async () => {
    setLogsLoading(true)
    try {
      const res = await getOperationLogs({
        page: pagination.current,
        page_size: pagination.pageSize
      })
      setLogs(res.items)
      setPagination({ ...pagination, total: res.total })
    } catch (error) {
      console.error('加载日志失败:', error)
    } finally {
      setLogsLoading(false)
    }
  }

  const handleSaveAnnouncement = async (values) => {
    setSaveLoading(true)
    try {
      await updateSystemConfig({
        key: 'announcement',
        value: values.value
      })
      message.success('保存成功')
    } catch (error) {
      console.error('保存失败:', error)
    } finally {
      setSaveLoading(false)
    }
  }

  const actionMap = {
    login: '登录',
    logout: '登出',
    create_task: '创建任务',
    cancel_task: '取消任务',
    generate_cards: '生成卡密',
    bind_card: '绑定卡密',
    delete_card: '删除卡密',
    add_mail_config: '添加邮箱配置',
    update_mail_config: '更新邮箱配置',
    toggle_mail_config: '切换邮箱状态',
    delete_mail_config: '删除邮箱配置',
    change_password: '修改密码',
    update_config: '更新系统配置',
    update_site_config: '更新站点配置',
    import_cards: '导入卡密',
    update_card: '更新卡密',
    import_accounts: '导入账号',
    delete_account: '删除账号',
    task_finished: '任务完成'
  }

  const logColumns = [
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 180,
      render: (time) => dayjs(time).format('YYYY-MM-DD HH:mm:ss')
    },
    {
      title: '用户',
      dataIndex: 'username',
      width: 120
    },
    {
      title: '操作',
      dataIndex: 'action',
      width: 150,
      render: (action) => actionMap[action] || action
    },
    {
      title: '详情',
      dataIndex: 'details',
      ellipsis: true
    },
    {
      title: 'IP地址',
      dataIndex: 'ip_address',
      width: 150
    }
  ]

  // 缺了必填项就提示 —— 跑批会被拦下，不如在这里先说清
  const missingRequired = siteFields.filter(f => f.required && !f.active)

  const items = [
    {
      key: 'site',
      label: '站点配置',
      children: (
        <Card loading={siteLoading}>
          <Alert
            type={missingRequired.length ? 'warning' : 'info'}
            showIcon
            message={
              missingRequired.length
                ? `还有 ${missingRequired.length} 项必填未配置，注册任务会被拦下`
                : '站点配置已就绪'
            }
            description={
              <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                这些是注册任务实际使用的目标站点参数，保存后**立即生效**，不需要重启服务。
                <br />
                留空的项会回落到 <Text code>.env</Text> 里的同名变量（命令行入口用的就是那份）。
              </div>
            }
            style={{ marginBottom: 20 }}
          />

          <Form form={siteForm} onFinish={handleSaveSiteConfig} layout="vertical">
            {siteFields.map(f => (
              <Form.Item
                key={f.key}
                label={
                  <Space size={6}>
                    <span>{f.label}</span>
                    <Text type="secondary" style={{ fontSize: 12 }}>({f.key})</Text>
                    {f.required && <Tag color="red" style={{ marginInlineEnd: 0 }}>必填</Tag>}
                  </Space>
                }
                name={f.key}
                extra={
                  <div style={{ fontSize: 12 }}>
                    <div>{f.hint}</div>
                    <div style={{ marginTop: 4 }}>
                      当前生效：
                      {f.active
                        ? <Text code>{f.active}</Text>
                        : <Text type="danger">未配置</Text>}
                      {!f.value && f.env && (
                        <Text type="secondary">（来自 .env）</Text>
                      )}
                    </div>
                  </div>
                }
              >
                <Input placeholder={f.placeholder} allowClear />
              </Form.Item>
            ))}

            <Form.Item style={{ marginBottom: 0 }}>
              <Space>
                <Button type="primary" htmlType="submit" loading={siteSaving}>
                  保存并生效
                </Button>
                <Button icon={<ReloadOutlined />} onClick={loadSiteConfig}>
                  重新加载
                </Button>
              </Space>
            </Form.Item>
          </Form>
        </Card>
      )
    },
    {
      key: 'announcement',
      label: '公告配置',
      children: (
        <Card>
          <Form
            form={announcementForm}
            onFinish={handleSaveAnnouncement}
            layout="vertical"
          >
            <Form.Item
              label="系统公告"
              name="value"
              extra="公告内容将在首页和卡密提取页面顶部显示"
            >
              <TextArea
                rows={4}
                placeholder="请输入公告内容..."
                maxLength={500}
                showCount
              />
            </Form.Item>

            <Form.Item>
              <Button type="primary" htmlType="submit" loading={saveLoading}>
                保存公告
              </Button>
            </Form.Item>
          </Form>
        </Card>
      )
    },
    {
      key: 'logs',
      label: '操作日志',
      children: (
        <Card>
          <Table
            columns={logColumns}
            dataSource={logs}
            rowKey="id"
            loading={logsLoading}
            pagination={{
              ...pagination,
              showSizeChanger: true,
              showTotal: (total) => `共 ${total} 条记录`,
              onChange: (page, pageSize) => {
                setPagination({ ...pagination, current: page, pageSize })
              }
            }}
          />
        </Card>
      )
    }
  ]

  return (
    <div>
      <Tabs items={items} />
    </div>
  )
}
