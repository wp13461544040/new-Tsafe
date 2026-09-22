import { useEffect, useState } from 'react'
import {
  Card, Form, Input, Button, message, Table, Tabs, Alert, Space, Tag, Typography,
  Switch, InputNumber, Select, Divider
} from 'antd'
import { ReloadOutlined } from '@ant-design/icons'
import {
  getSystemConfig, updateSystemConfig, getOperationLogs,
  getSiteConfig, updateSiteConfig,
  getAccountCheckConfig, updateAccountCheckConfig
} from '../api'
import AccountCheckBar from '../components/AccountCheckBar'
import dayjs from 'dayjs'

const { TextArea } = Input
const { Text } = Typography

// 巡检配置项的中文标签。后端 schema 里的 desc 是给开发看的，
// 这里给运维看 —— 两者受众不同，不要指望一份文案两头用。
const CHECK_LABELS = {
  enabled: '启用定时巡检',
  interval_hours: '巡检间隔（小时）',
  limit: '单轮检查上限',
  concurrency: '并发探测数',
  dead_threshold: '连续失败几次判失效',
  include_assigned: '同时检查已提取的账号',
  timeout: '单次超时（秒）',
  min_interval_hours: '跳过最近N小时内已检查的'
}

const CHECK_HINTS = {
  enabled: '关闭时不会自动跑，但仍可手动触发。',
  interval_hours: '改动最迟 60 秒生效，不需要重启。',
  limit: '账号多时分轮检查，按「最久没检查的优先」排序，轮到即可。',
  concurrency: '太高可能被目标站点限流，限流会被判成「无法判定」而不是失效。',
  dead_threshold: '设 1 会让一次网络抖动就把账号标失效，建议至少 2。',
  include_assigned: '已提取的账号即使探测失效也不会被改状态，只记录结论供排查。',
  timeout: '超时会被判成「无法判定」，不会误标失效。',
  min_interval_hours: '0 表示跟随巡检间隔。手动触发时此项始终按 0 处理。'
}

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

  const [checkForm] = Form.useForm()
  const [checkSchema, setCheckSchema] = useState({})
  const [checkLoading, setCheckLoading] = useState(false)
  const [checkSaving, setCheckSaving] = useState(false)

  useEffect(() => {
    loadAnnouncement()
    loadSiteConfig()
    loadCheckConfig()
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

  const loadCheckConfig = async () => {
    setCheckLoading(true)
    try {
      const res = await getAccountCheckConfig()
      setCheckSchema(res.schema || {})
      checkForm.setFieldsValue(res.config || {})
    } catch (error) {
      console.error('加载巡检配置失败:', error)
    } finally {
      setCheckLoading(false)
    }
  }

  const handleSaveCheckConfig = async (values) => {
    setCheckSaving(true)
    try {
      const res = await updateAccountCheckConfig(values)
      message.success(res.message || '已保存')
      // 用后端返回的配置回填，而不是信任表单里的值 ——
      // 后端会做类型归一（比如 min_interval_hours 的 0 语义），
      // 不回填会出现「页面显示的和实际生效的不一致」。
      checkForm.setFieldsValue(res.config || {})
    } catch (error) {
      console.error('保存巡检配置失败:', error)
    } finally {
      setCheckSaving(false)
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
      key: 'check',
      label: '账号巡检',
      // 🔴 forceRender 必须开。Tabs 默认懒渲染非激活面板，而配置是在
      //    mount 时异步加载并 setFieldsValue 的 —— Form 还没挂载，值直接丢，
      //    用户切过来看到的是一张空表单（antd 只会在 console 里警告
      //    "Instance created by useForm is not connected to any Form element"）。
      forceRender: true,
      children: (
        <Card loading={checkLoading}>
          <AccountCheckBar onFinished={loadCheckConfig} />

          <Divider style={{ margin: '20px 0 16px' }} />

          <Alert
            type="success"
            showIcon
            message="巡检不消耗账号额度"
            description={
              <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                巡检定期探测账号池里的 key 是否还能用，失效的会自动标记，
                避免用户提取到废号。
                <br />
                探测只发一个空请求来验证「认证是否通过」，不会触发模型推理，
                因此<Text strong>不产生任何 token 消耗</Text>。
                同时会识别额度耗尽、订阅过期这类响应并判为失效。
                <br />
                <Text type="secondary">
                  网络抖动、限流、服务端 5xx 都会被判成「无法判定」而不是失效，
                  且需要连续失败达到阈值才会真的标记失效 —— 这是为了避免一次网络故障
                  把整池账号误标废。
                </Text>
                <br />
                <Text type="secondary">
                  局限：探测确认的是「认证通过」，不等于「一定能跑出结果」。
                  若需确认推理能力，请人工挑一把 key 单独调用验证。
                </Text>
              </div>
            }
            style={{ marginBottom: 20 }}
          />

          <Form
            form={checkForm}
            onFinish={handleSaveCheckConfig}
            layout="vertical"
          >
            {Object.entries(checkSchema).map(([key, meta]) => {
              const label = (
                <Space size={6}>
                  <span>{CHECK_LABELS[key] || key}</span>
                  <Text type="secondary" style={{ fontSize: 12 }}>({key})</Text>
                </Space>
              )
              const extra = CHECK_HINTS[key] || meta.desc

              if (meta.type === 'bool') {
                return (
                  <Form.Item
                    key={key}
                    label={label}
                    name={key}
                    extra={extra}
                    valuePropName="checked"
                  >
                    <Switch />
                  </Form.Item>
                )
              }

              // 枚举型配置（后端 schema 带 choices）。目前没有这类项，
              // 分支留着是为了以后加配置时前端不用改。
              if (meta.choices) {
                return (
                  <Form.Item key={key} label={label} name={key} extra={extra}>
                    <Select
                      style={{ maxWidth: 280 }}
                      options={meta.choices.map(c => ({ value: c, label: c }))}
                    />
                  </Form.Item>
                )
              }

              // 数值项的下限跟后端校验对齐。前端不拦的话用户填 0 会拿到
              // 一个 400，而错误提示远不如输入框旁边的约束直观。
              const isInt = meta.type === 'int'
              const min = ['limit', 'concurrency', 'dead_threshold'].includes(key)
                ? 1
                : key === 'min_interval_hours' ? 0 : 0.1

              return (
                <Form.Item key={key} label={label} name={key} extra={extra}>
                  <InputNumber
                    style={{ width: 200 }}
                    min={min}
                    step={isInt ? 1 : 0.5}
                    precision={isInt ? 0 : 1}
                  />
                </Form.Item>
              )
            })}

            <Form.Item style={{ marginBottom: 0 }}>
              <Space>
                <Button type="primary" htmlType="submit" loading={checkSaving}>
                  保存配置
                </Button>
                <Button icon={<ReloadOutlined />} onClick={loadCheckConfig}>
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
      // 同上：loadAnnouncement 在 mount 时 setFieldsValue，不加 forceRender
      // 的话已保存的公告在这个 tab 里显示为空，看起来像公告丢了。
      forceRender: true,
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
