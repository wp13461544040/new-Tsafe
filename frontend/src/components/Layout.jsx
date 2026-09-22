import { useState } from 'react'
import { Layout as AntLayout, Menu, Avatar, Dropdown, Button, Modal, Form, Input, Space, message } from 'antd'
import {
  DashboardOutlined,
  MailOutlined,
  AppstoreOutlined,
  CreditCardOutlined,
  DatabaseOutlined,
  ExportOutlined,
  SettingOutlined,
  UserOutlined,
  LogoutOutlined,
  LockOutlined,
  LinkOutlined
} from '@ant-design/icons'
import { Outlet, useNavigate, useLocation } from 'react-router-dom'
import { clearAuth, getUser } from '../utils/auth'
import { changePassword } from '../api/auth'

const { Header, Sider, Content } = AntLayout

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false)
  const [passwordModalOpen, setPasswordModalOpen] = useState(false)
  const [form] = Form.useForm()
  const navigate = useNavigate()
  const location = useLocation()
  const user = getUser()

  const menuItems = [
    { key: '/', icon: <DashboardOutlined />, label: '统计概览' },
    { key: '/mail', icon: <MailOutlined />, label: '邮箱配置' },
    { key: '/tasks', icon: <AppstoreOutlined />, label: '注册任务' },
    { key: '/cards', icon: <CreditCardOutlined />, label: '卡密管理' },
    { key: '/accounts', icon: <DatabaseOutlined />, label: '账号池' },
    { key: '/export', icon: <ExportOutlined />, label: '数据导出' },
    { key: '/settings', icon: <SettingOutlined />, label: '系统设置' },
  ]

  const handleMenuClick = ({ key }) => {
    navigate(key)
  }

  const handleLogout = () => {
    Modal.confirm({
      title: '确认退出',
      content: '确定要退出登录吗？',
      onOk: () => {
        clearAuth()
        navigate('/login', { replace: true })
      }
    })
  }

  const handleChangePassword = async (values) => {
    try {
      await changePassword(values)
      message.success('密码修改成功，请重新登录')
      form.resetFields()
      setPasswordModalOpen(false)
      setTimeout(() => {
        clearAuth()
        navigate('/login', { replace: true })
      }, 1000)
    } catch (error) {
      console.error('修改密码失败:', error)
    }
  }

  const dropdownItems = [
    {
      key: 'change-password',
      icon: <LockOutlined />,
      label: '修改密码',
      onClick: () => setPasswordModalOpen(true)
    },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: handleLogout
    }
  ]

  return (
    <AntLayout style={{ minHeight: '100vh' }}>
      <Sider collapsible collapsed={collapsed} onCollapse={setCollapsed}>
        <div style={{
          height: 64,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#fff',
          fontSize: 18,
          fontWeight: 'bold'
        }}>
          {collapsed ? 'TS' : 'TypeSafe'}
        </div>
        <Menu
          theme="dark"
          selectedKeys={[location.pathname]}
          mode="inline"
          items={menuItems}
          onClick={handleMenuClick}
        />
      </Sider>

      <AntLayout>
        <Header style={{
          padding: '0 24px',
          background: '#fff',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          boxShadow: '0 1px 4px rgba(0,21,41,.08)'
        }}>
          <div style={{ fontSize: 18, fontWeight: 'bold' }}>
            管理后台
          </div>

          <Space size={16}>
            <Button
              type="link"
              icon={<LinkOutlined />}
              onClick={() => window.open('/extract', '_blank')}
            >
              对外提取页
            </Button>

            <Dropdown menu={{ items: dropdownItems }} placement="bottomRight">
            <div style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 8 }}>
              <Avatar icon={<UserOutlined />} />
              <span>{user?.username}</span>
            </div>
            </Dropdown>
          </Space>
        </Header>

        <Content style={{ margin: '24px', background: '#fff', padding: 24, minHeight: 280 }}>
          <Outlet />
        </Content>
      </AntLayout>

      <Modal
        title="修改密码"
        open={passwordModalOpen}
        onCancel={() => {
          form.resetFields()
          setPasswordModalOpen(false)
        }}
        footer={null}
      >
        <Form
          form={form}
          onFinish={handleChangePassword}
          layout="vertical"
        >
          <Form.Item
            label="原密码"
            name="old_password"
            rules={[{ required: true, message: '请输入原密码' }]}
          >
            <Input.Password />
          </Form.Item>

          <Form.Item
            label="新密码"
            name="new_password"
            rules={[
              { required: true, message: '请输入新密码' },
              { min: 6, message: '密码长度不能少于6位' }
            ]}
          >
            <Input.Password />
          </Form.Item>

          <Form.Item
            label="确认新密码"
            name="confirm_password"
            dependencies={['new_password']}
            rules={[
              { required: true, message: '请确认新密码' },
              ({ getFieldValue }) => ({
                validator(_, value) {
                  if (!value || getFieldValue('new_password') === value) {
                    return Promise.resolve()
                  }
                  return Promise.reject(new Error('两次输入的密码不一致'))
                }
              })
            ]}
          >
            <Input.Password />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" block>
              确认修改
            </Button>
          </Form.Item>
        </Form>
      </Modal>
    </AntLayout>
  )
}
