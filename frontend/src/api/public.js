import axios from 'axios'

/**
 * 公开接口专用实例
 *
 * 与 utils/request.js 的区别：
 * - 不携带 Authorization
 * - 不做 401 重定向（公开页面没有登录概念）
 * - 不弹全局 message，错误交由页面自行处理
 */
const publicRequest = axios.create({
  baseURL: '/api/public',
  timeout: 30000
})

publicRequest.interceptors.response.use(
  response => response.data,
  error => {
    const msg =
      error.response?.data?.error ||
      (error.request ? '网络错误，请检查网络连接' : '请求失败')
    return Promise.reject(new Error(msg))
  }
)

// 获取公告与提取说明
export const getPublicNotice = () => publicRequest.get('/announcement')

// 查询卡密额度（不消耗）
export const queryCard = (cardKey) =>
  publicRequest.post('/card/query', { card_key: cardKey })

// 提取账号
export const extractAccounts = (cardKey, count) =>
  publicRequest.post('/extract', { card_key: cardKey, count })

// 找回该卡密已提取的账号
export const getExtractHistory = (cardKey) =>
  publicRequest.post('/history', { card_key: cardKey })
