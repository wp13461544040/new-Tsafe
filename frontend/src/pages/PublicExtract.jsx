import { useEffect, useState } from 'react'
import {
  Card,
  Input,
  Button,
  Space,
  Alert,
  Table,
  Typography,
  Descriptions,
  Tag,
  InputNumber,
  Divider,
  message,
  Empty,
  Tooltip
} from 'antd'
import {
  KeyOutlined,
  SearchOutlined,
  CloudDownloadOutlined,
  CopyOutlined,
  HistoryOutlined,
  DownloadOutlined
} from '@ant-design/icons'
import {
  getPublicNotice,
  queryCard,
  extractAccounts,
  getExtractHistory
} from '../api/public'

const { Title, Text, Paragraph } = Typography

const STATUS_MAP = {
  unused: { color: 'green', text: '未使用' },
  partial: { color: 'blue', text: '部分提取' },
  used: { color: 'default', text: '已用完' },
  disabled: { color: 'red', text: '已禁用' }
}

export default function PublicExtract() {
  const [notice, setNotice] = useState({ announcement: '', extract_notice: '' })
  const [cardKey, setCardKey] = useState('')
  const [cardInfo, setCardInfo] = useState(null)
  const [count, setCount] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [queryLoading, setQueryLoading] = useState(false)
  const [extractLoading, setExtractLoading] = useState(false)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    getPublicNotice()
      .then(setNotice)
      .catch(() => {
        // 公告拉取失败不影响核心功能，静默处理
      })
  }, [])

  const trimmedKey = cardKey.trim()

  const handleQuery = async () => {
    if (!trimmedKey) {
      setError('请输入卡密')
      return
    }

    setQueryLoading(true)
    setError('')
    setAccounts([])

    try {
      const data = await queryCard(trimmedKey)
      setCardInfo(data)
      setCount(data.remaining > 0 ? data.remaining : null)

      if (!data.extractable) {
        setError(data.reason || '该卡密当前不可提取')
      }
    } catch (e) {
      setCardInfo(null)
      setError(e.message)
    } finally {
      setQueryLoading(false)
    }
  }

  const handleExtract = async () => {
    if (!trimmedKey) {
      setError('请输入卡密')
      return
    }

    setExtractLoading(true)
    setError('')

    try {
      const data = await extractAccounts(trimmedKey, count || undefined)
      setAccounts(data.accounts || [])
      setCardInfo({
        ...(cardInfo || {}),
        card_key: data.card_key,
        quota: data.quota,
        extracted_count: data.extracted_count,
        remaining: data.remaining,
        extractable: data.remaining > 0
      })
      setCount(data.remaining > 0 ? data.remaining : null)
      message.success(data.message || `成功提取 ${data.accounts?.length || 0} 个账号`)
    } catch (e) {
      setError(e.message)
    } finally {
      setExtractLoading(false)
    }
  }

  const handleHistory = async () => {
    if (!trimmedKey) {
      setError('请输入卡密')
      return
    }

    setHistoryLoading(true)
    setError('')

    try {
      const data = await getExtractHistory(trimmedKey)
      setAccounts(data.accounts || [])
      setCardInfo({
        ...(cardInfo || {}),
        card_key: data.card_key,
        quota: data.quota,
        extracted_count: data.extracted_count,
        remaining: data.remaining
      })

      if (!data.accounts?.length) {
        message.info('该卡密还没有提取过账号')
      } else {
        message.success(`已找回 ${data.accounts.length} 个账号`)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setHistoryLoading(false)
    }
  }

  const buildPlainText = () =>
    accounts
      .map(a => [a.email, a.password, a.api_key].filter(Boolean).join('----'))
      .join('\n')

  const handleCopyAll = async () => {
    const text = buildPlainText()

    try {
      await navigator.clipboard.writeText(text)
      message.success('已复制全部账号到剪贴板')
    } catch {
      // 剪贴板 API 在非 HTTPS 环境可能不可用，降级为选中提示
      message.warning('浏览器不支持自动复制，请手动选中下方内容复制')
    }
  }

  const handleDownload = () => {
    const blob = new Blob([buildPlainText()], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `accounts_${trimmedKey}_${Date.now()}.txt`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const columns = [
    {
      title: '#',
      width: 56,
      render: (_, __, index) => index + 1
    },
    {
      title: '邮箱',
      dataIndex: 'email',
      render: v => v || <Text type="secondary">-</Text>
    },
    {
      title: '密码',
      dataIndex: 'password',
      render: v => (v ? <Text copyable>{v}</Text> : <Text type="secondary">-</Text>)
    },
    {
      title: 'API Key',
      dataIndex: 'api_key',
      render: v =>
        v ? (
          <Text copyable={{ text: v }} style={{ fontFamily: 'monospace', fontSize: 12 }}>
            {v.length > 28 ? `${v.slice(0, 28)}...` : v}
          </Text>
        ) : (
          <Text type="secondary">-</Text>
        )
    }
  ]

  const statusTag = cardInfo?.status ? STATUS_MAP[cardInfo.status] : null

  return (
    <div
      style={{
        minHeight: '100vh',
        background: 'linear-gradient(135deg, #f5f7fa 0%, #e8edf5 100%)',
        padding: '48px 16px'
      }}
    >
      <div style={{ maxWidth: 880, margin: '0 auto' }}>
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <Title level={2} style={{ marginBottom: 8 }}>
            <KeyOutlined style={{ color: '#1677ff', marginRight: 12 }} />
            账号提取
          </Title>
          <Text type="secondary">输入卡密即可提取对应数量的账号，无需注册登录</Text>
        </div>

        {notice.announcement && (
          <Alert
            message="公告"
            description={notice.announcement}
            type="info"
            showIcon
            style={{ marginBottom: 24 }}
          />
        )}

        <Card style={{ marginBottom: 24, borderRadius: 12 }}>
          <Space.Compact style={{ width: '100%' }}>
            <Input
              size="large"
              prefix={<KeyOutlined style={{ color: '#bfbfbf' }} />}
              placeholder="请输入卡密，例如 TS-XXXXXXXX"
              value={cardKey}
              onChange={e => setCardKey(e.target.value)}
              onPressEnter={handleQuery}
              allowClear
            />
            <Button
              size="large"
              type="primary"
              icon={<SearchOutlined />}
              loading={queryLoading}
              onClick={handleQuery}
            >
              查询
            </Button>
          </Space.Compact>

          {error && (
            <Alert
              type="error"
              message={error}
              showIcon
              closable
              onClose={() => setError('')}
              style={{ marginTop: 16 }}
            />
          )}

          {cardInfo && (
            <>
              <Divider style={{ margin: '20px 0' }} />

              <Descriptions column={{ xs: 1, sm: 2, md: 4 }} size="small">
                <Descriptions.Item label="卡密">
                  <Text code>{cardInfo.card_key}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="总额度">
                  <Text strong>{cardInfo.quota}</Text>
                </Descriptions.Item>
                <Descriptions.Item label="已提取">
                  {cardInfo.extracted_count}
                </Descriptions.Item>
                <Descriptions.Item label="剩余可提取">
                  <Text strong style={{ color: cardInfo.remaining > 0 ? '#52c41a' : '#ff4d4f' }}>
                    {cardInfo.remaining}
                  </Text>
                </Descriptions.Item>
                {statusTag && (
                  <Descriptions.Item label="状态">
                    <Tag color={statusTag.color}>{statusTag.text}</Tag>
                  </Descriptions.Item>
                )}
                {cardInfo.expires_at && (
                  <Descriptions.Item label="有效期至">
                    {new Date(cardInfo.expires_at).toLocaleString()}
                  </Descriptions.Item>
                )}
              </Descriptions>

              <Space wrap style={{ marginTop: 20 }}>
                <Space>
                  <Text>提取数量</Text>
                  <InputNumber
                    min={1}
                    max={cardInfo.remaining || 1}
                    value={count}
                    onChange={setCount}
                    disabled={!cardInfo.remaining}
                    placeholder="数量"
                    style={{ width: 100 }}
                  />
                </Space>

                <Button
                  type="primary"
                  icon={<CloudDownloadOutlined />}
                  loading={extractLoading}
                  disabled={!cardInfo.remaining}
                  onClick={handleExtract}
                >
                  提取账号
                </Button>

                <Tooltip title="若之前提取的结果丢失，可凭卡密重新查看">
                  <Button
                    icon={<HistoryOutlined />}
                    loading={historyLoading}
                    onClick={handleHistory}
                  >
                    找回已提取
                  </Button>
                </Tooltip>
              </Space>
            </>
          )}
        </Card>

        {accounts.length > 0 && (
          <Card
            style={{ borderRadius: 12 }}
            title={`提取结果（${accounts.length} 个账号）`}
            extra={
              <Space>
                <Button size="small" icon={<CopyOutlined />} onClick={handleCopyAll}>
                  复制全部
                </Button>
                <Button size="small" icon={<DownloadOutlined />} onClick={handleDownload}>
                  下载 TXT
                </Button>
              </Space>
            }
          >
            <Alert
              type="warning"
              showIcon
              message="请立即保存以下账号信息"
              description={notice.extract_notice || '关闭页面后可凭卡密使用「找回已提取」重新查看。'}
              style={{ marginBottom: 16 }}
            />

            <Table
              rowKey={(r, i) => `${r.api_key}-${i}`}
              columns={columns}
              dataSource={accounts}
              size="small"
              pagination={accounts.length > 10 ? { pageSize: 10 } : false}
              scroll={{ x: 'max-content' }}
            />
          </Card>
        )}

        {!cardInfo && !error && (
          <Card style={{ borderRadius: 12 }}>
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="请先输入卡密并点击查询"
            />
          </Card>
        )}

        <Paragraph type="secondary" style={{ textAlign: 'center', marginTop: 32, fontSize: 12 }}>
          卡密额度用尽后将无法继续提取，请妥善保管账号信息
        </Paragraph>
      </div>
    </div>
  )
}
