import { useEffect, useState } from 'react'
import { Card, Form, Select, Button, Space, Alert, message } from 'antd'
import { DownloadOutlined } from '@ant-design/icons'
import { exportCards, getBatches, getSystemConfig } from '../api'

export default function CardExtract() {
  const [batches, setBatches] = useState([])
  const [announcement, setAnnouncement] = useState('')
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm()

  useEffect(() => {
    loadData()
  }, [])

  const loadData = async () => {
    try {
      const [batchesData, configData] = await Promise.all([
        getBatches(),
        getSystemConfig('announcement')
      ])
      setBatches(batchesData)
      setAnnouncement(configData.value || '')
    } catch (error) {
      console.error('加载失败:', error)
    }
  }

  const handleExport = async (values) => {
    if (!values.format && !values.batch_id && !values.status) {
      message.warning('请至少选择一个筛选条件')
      return
    }

    setLoading(true)
    try {
      const params = {}
      if (values.batch_id) params.batch_id = values.batch_id
      if (values.status) params.status = values.status
      if (values.format) params.format = values.format

      const blob = await exportCards(params)
      
      // 创建下载链接
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `cards_export_${Date.now()}.${values.format || 'csv'}`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      window.URL.revokeObjectURL(url)
      
      message.success('导出成功')
    } catch (error) {
      console.error('导出失败:', error)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div>
      {announcement && (
        <Alert
          message="系统公告"
          description={announcement}
          type="info"
          showIcon
          closable
          style={{ marginBottom: 24 }}
        />
      )}

      <Card title="卡密提取" style={{ maxWidth: 600 }}>
        <Form
          form={form}
          onFinish={handleExport}
          layout="vertical"
        >
          <Form.Item
            label="批次筛选"
            name="batch_id"
            extra="选择要导出的卡密批次"
          >
            <Select placeholder="全部批次" allowClear>
              {batches.map(batch => (
                <Select.Option key={batch} value={batch}>{batch}</Select.Option>
              ))}
            </Select>
          </Form.Item>

          <Form.Item
            label="状态筛选"
            name="status"
            extra="选择要导出的卡密状态"
          >
            <Select placeholder="全部状态" allowClear>
              <Select.Option value="unused">未使用</Select.Option>
              <Select.Option value="used">已使用</Select.Option>
            </Select>
          </Form.Item>

          <Form.Item
            label="导出格式"
            name="format"
            initialValue="csv"
            rules={[{ required: true, message: '请选择导出格式' }]}
          >
            <Select>
              <Select.Option value="csv">CSV (逗号分隔)</Select.Option>
              <Select.Option value="txt">TXT (纯文本，每行一个)</Select.Option>
              <Select.Option value="json">JSON (结构化数据)</Select.Option>
            </Select>
          </Form.Item>

          <Form.Item>
            <Space>
              <Button 
                type="primary" 
                htmlType="submit" 
                icon={<DownloadOutlined />}
                loading={loading}
              >
                导出卡密
              </Button>
              <Button onClick={() => form.resetFields()}>重置</Button>
            </Space>
          </Form.Item>
        </Form>

        <Alert
          message="导出说明"
          description={
            <ul style={{ margin: 0, paddingLeft: 20 }}>
              <li>CSV格式：包含ID、卡密、批次、状态、绑定账号、创建时间等完整信息</li>
              <li>TXT格式：仅包含卡密，每行一个，方便批量使用</li>
              <li>JSON格式：完整的结构化数据，适合程序处理</li>
              <li>默认导出所有符合条件的卡密，建议添加筛选条件</li>
            </ul>
          }
          type="info"
          style={{ marginTop: 24 }}
        />
      </Card>
    </div>
  )
}
