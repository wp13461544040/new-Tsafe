import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { useEffect, useState } from 'react'
import Login from './pages/Login'
import Layout from './components/Layout'
import Dashboard from './pages/Dashboard'
import MailConfig from './pages/MailConfig'
import TaskManagement from './pages/TaskManagement'
import CardManagement from './pages/CardManagement'
import AccountPool from './pages/AccountPool'
import CardExtract from './pages/CardExtract'
import PublicExtract from './pages/PublicExtract'
import SystemSettings from './pages/SystemSettings'
import { getToken } from './utils/auth'

// 受保护路由组件
function ProtectedRoute({ children }) {
  const token = getToken()
  const location = useLocation()
  
  // 调试日志
  console.log('ProtectedRoute check:', { 
    hasToken: !!token, 
    token: token?.substring(0, 20) + '...', 
    path: location.pathname 
  })
  
  if (!token) {
    console.log('No token, redirecting to login')
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  
  return children
}

function App() {
  return (
    <Routes>
      {/* 公开页面：无需登录，供卡密持有者自助提取账号 */}
      <Route path="/extract" element={<PublicExtract />} />

      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="mail" element={<MailConfig />} />
        <Route path="tasks" element={<TaskManagement />} />
        <Route path="cards" element={<CardManagement />} />
        <Route path="accounts" element={<AccountPool />} />
        <Route path="export" element={<CardExtract />} />
        <Route path="settings" element={<SystemSettings />} />
      </Route>
    </Routes>
  )
}

export default App
