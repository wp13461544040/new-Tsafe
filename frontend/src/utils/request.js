import axios from 'axios'
import { message } from 'antd'
import { getToken, clearAuth } from './auth'

const request = axios.create({
  baseURL: '/api',
  timeout: 30000
})

// 请求拦截器
request.interceptors.request.use(
  config => {
    const token = getToken()
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  error => {
    return Promise.reject(error)
  }
)

// 响应拦截器
request.interceptors.response.use(
  response => {
    return response.data
  },
  error => {
    // 轮询类请求（巡检状态每 2s 一次）失败时不该弹 toast：
    // 后端重启或网络抖一下，页面就会被几十个「请求失败」淹没，
    // 真正重要的提示反而被挤掉。调用方传 { silent: true } 自行处理。
    // 401 例外 —— 登录过期必须跳转，静默会让用户对着空页面发呆。
    const silent = error.config?.silent === true

    if (error.response) {
      const { status, data } = error.response

      if (silent && status !== 401) {
        return Promise.reject(error)
      }

      if (status === 401) {
        message.error('登录已过期，请重新登录')
        clearAuth()
        window.location.href = '/login'
      } else if (status === 403) {
        message.error(data.error || '权限不足')
      } else if (status === 404) {
        message.error(data.error || '资源不存在')
      } else if (status === 500) {
        message.error(data.error || '服务器错误')
      } else {
        message.error(data.error || '请求失败')
      }
    } else if (error.request) {
      if (!silent) message.error('网络错误，请检查网络连接')
    } else {
      if (!silent) message.error('请求配置错误')
    }
    
    return Promise.reject(error)
  }
)

export default request
