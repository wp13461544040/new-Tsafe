import { useState, useEffect } from 'react'
import { Form, Input, Button, Card, message } from 'antd'
import { UserOutlined, LockOutlined } from '@ant-design/icons'
import { useNavigate, useLocation } from 'react-router-dom'
import { login } from '../api/auth'
import { setToken, setUser, getToken } from '../utils/auth'

export default function Login() {
  const [loading, setLoading] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()

  // 如果已登录，直接跳转到目标页面或首页
  useEffect(() => {
    if (getToken()) {
      const from = location.state?.from?.pathname || '/'
      navigate(from, { replace: true })
    }
  }, [])

  const onFinish = async (values) => {
    setLoading(true)
    try {
      console.log('Login attempt:', values.username)
      const res = await login(values)
      console.log('Login response:', res)
      
      setToken(res.access_token)
      setUser(res.user)
      message.success('登录成功')
      
      // 跳转到之前想访问的页面，或默认首页
      const from = location.state?.from?.pathname || '/'
      console.log('Redirecting to:', from)
      navigate(from, { replace: true })
    } catch (error) {
      console.error('登录失败:', error)
      message.error(error.response?.data?.error || '登录失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      minHeight: '100vh',
      background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
    }}>
      <Card
        title={<div style={{ textAlign: 'center', fontSize: 24 }}>TypeSafe 管理后台</div>}
        style={{ width: 400, boxShadow: '0 4px 20px rgba(0,0,0,0.1)' }}
      >
        <Form
          name="login"
          onFinish={onFinish}
          autoComplete="off"
          size="large"
        >
          <Form.Item
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input prefix={<UserOutlined />} placeholder="用户名" />
          </Form.Item>

          <Form.Item
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password prefix={<LockOutlined />} placeholder="密码" />
          </Form.Item>

          <Form.Item>
            <Button type="primary" htmlType="submit" loading={loading} block>
              登录
            </Button>
          </Form.Item>
        </Form>
        
        <div style={{ textAlign: 'center', color: '#999', fontSize: 12 }}>
          默认账号: admin / admin123
        </div>
      </Card>
    </div>
  )
}
