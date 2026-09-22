import { useEffect, useState } from 'react'
import { Card, Row, Col, Statistic, Table, Tag, Alert } from 'antd'
import {
  AppstoreOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  CreditCardOutlined,
  LineChartOutlined
} from '@ant-design/icons'
import { getOverview, getRecentActivities, getSystemConfig } from '../api'
import dayjs from 'dayjs'

export default function Dashboard() {
  const [loading, setLoading] = useState(true)
  const [overview, setOverview] = useState(null)
  const [activities, setActivities] = useState([])
  const [announcement, setAnnouncement] = useState('')

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    setLoading(true)
    try {
      const [overviewData, activitiesData, configData] = await Promise.all([
        getOverview(),
        getRecentActivities(10),
        getSystemConfig('announcement')
      ])
      
      setOverview(overviewData)
      setActivities(activitiesData)
      setAnnouncement(configData.value || '')
    } catch (error) {
      console.error('加载数据失败:', error)
    } finally {
      setLoading(false)
    }
  }

  const activityColumns = [
    {
      title: '操作',
      dataIndex: 'action',
      width: 120,
      render: (action) => {
        const actionMap = {
          login: '登录',
          logout: '登出',
          create_task: '创建任务',
          generate_cards: '生成卡密',
          bind_card: '绑定卡密',
          add_mail_config: '添加邮箱配置',
          update_mail_config: '更新邮箱配置',
          delete_mail_config: '删除邮箱配置'
        }
        return actionMap[action] || action
      }
    },
    {
      title: '用户',
      dataIndex: 'username',
      width: 100
    },
    {
      title: '详情',
      dataIndex: 'details',
      ellipsis: true
    },
    {
      title: '时间',
      dataIndex: 'created_at',
      width: 180,
      render: (time) => dayjs(time).format('YYYY-MM-DD HH:mm:ss')
    }
  ]

  if (!overview) return <div>加载中...</div>

  return (
    <div>
      {announcement && (
        <Alert
          message={announcement}
          type="info"
          closable
          style={{ marginBottom: 24 }}
        />
      )}

      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="总任务数"
              value={overview.tasks.total}
              prefix={<AppstoreOutlined />}
              valueStyle={{ color: '#1890ff' }}
            />
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              运行中: {overview.tasks.running} | 已完成: {overview.tasks.completed}
            </div>
          </Card>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="成功注册"
              value={overview.accounts.total_success}
              prefix={<CheckCircleOutlined />}
              suffix={`/ ${overview.accounts.total_success + overview.accounts.total_failed}`}
              valueStyle={{ color: '#52c41a' }}
            />
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              成功率: {overview.accounts.success_rate}%
            </div>
          </Card>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="失败数"
              value={overview.accounts.total_failed}
              prefix={<CloseCircleOutlined />}
              valueStyle={{ color: '#ff4d4f' }}
            />
          </Card>
        </Col>

        <Col xs={24} sm={12} lg={6}>
          <Card>
            <Statistic
              title="卡密总数"
              value={overview.cards.total}
              prefix={<CreditCardOutlined />}
              valueStyle={{ color: '#722ed1' }}
            />
            <div style={{ marginTop: 8, fontSize: 12, color: '#999' }}>
              已使用: {overview.cards.used} | 未使用: {overview.cards.unused}
            </div>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col xs={24} lg={24}>
          <Card title="最近7天趋势" extra={<LineChartOutlined />}>
            <div style={{ display: 'flex', justifyContent: 'space-around', textAlign: 'center' }}>
              {overview.trend.map((item) => (
                <div key={item.date}>
                  <div style={{ fontSize: 12, color: '#999' }}>
                    {dayjs(item.date).format('MM/DD')}
                  </div>
                  <div style={{ fontSize: 18, fontWeight: 'bold', color: '#52c41a', margin: '8px 0' }}>
                    {item.success}
                  </div>
                  <div style={{ fontSize: 12, color: '#ff4d4f' }}>
                    失败: {item.failed}
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </Col>
      </Row>

      <Card title="最近操作记录" style={{ marginTop: 24 }}>
        <Table
          columns={activityColumns}
          dataSource={activities}
          rowKey="id"
          pagination={false}
          size="small"
        />
      </Card>
    </div>
  )
}
