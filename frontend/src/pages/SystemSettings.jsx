import { useEffect, useState } from 'react'
import { Card, Form, Input, Button, message, Table, Tabs } from 'antd'
import { getSystemConfig, updateSystemConfig, getOperationLogs } from '../api'
import dayjs from 'dayjs'

const { TextArea } = Input

export default function SystemSettings() {
  const [announcementForm] = Form.useForm()
  const [logs, setLogs] = useState([])
  const [logsLoading, setLogsLoading] = useState(false)
  const [saveLoading, setSaveLoading] = useState(false)
  const [pagination, setPagination] = useState({ current: 1, pageSize: 20, total: 0 })

  useEffect(() => {
    loadAnnouncement()
    loadLogs()
  }, [pagination.current])

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
    update_config: '更新系统配置'
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

  const items = [
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
