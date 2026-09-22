import request from '../utils/request'

// 统计数据
export const getOverview = () => request.get('/stats/overview')
export const getTaskStats = () => request.get('/stats/tasks')
export const getRecentActivities = (limit = 20) => request.get(`/stats/recent-activities?limit=${limit}`)

// 邮箱配置
export const getMailConfigs = (params) => request.get('/mail/list', { params })
export const getMailConfig = (id) => request.get(`/mail/${id}`)
export const addMailConfig = (data) => request.post('/mail/add', data)
export const updateMailConfig = (id, data) => request.put(`/mail/${id}`, data)
export const toggleMailConfig = (id) => request.post(`/mail/${id}/toggle`)
export const testMailConfig = (id) => request.post(`/mail/${id}/test`)
export const deleteMailConfig = (id) => request.delete(`/mail/${id}`)

// 任务管理
export const getTasks = (params) => request.get('/task/list', { params })
export const getTask = (id) => request.get(`/task/${id}`)
export const createTask = (data) => request.post('/task/create', data)
export const cancelTask = (id) => request.post(`/task/${id}/cancel`)

// 卡密管理
export const getCards = (params) => request.get('/card/list', { params })
export const generateCards = (data) => request.post('/card/generate', data)
export const updateCard = (id, data) => request.put(`/card/${id}`, data)
export const bindCard = (data) => request.post('/card/bind', data)
export const exportCards = (params) => request.get('/card/export', { params, responseType: 'blob' })
export const getBatches = () => request.get('/card/batches')
export const deleteCard = (id) => request.delete(`/card/${id}`)
export const importCards = (payload, contentType = 'text/plain') =>
  request.post('/card/import', payload, { headers: { 'Content-Type': contentType } })

// 账号池
export const getAccounts = (params) => request.get('/account/list', { params })
export const getAccountStats = () => request.get('/account/stats')
export const importAccounts = (payload, contentType = 'application/json') =>
  request.post('/account/import', payload, { headers: { 'Content-Type': contentType } })
export const updateAccount = (id, data) => request.put(`/account/${id}`, data)
export const deleteAccount = (id) => request.delete(`/account/${id}`)
export const batchDeleteAccounts = (ids) => request.post('/account/batch-delete', { ids })
export const exportAccounts = (params) =>
  request.get('/account/export', { params, responseType: 'blob' })
export const getAccountBatches = () => request.get('/account/batches')

// 系统配置
export const getSystemConfig = (key) => request.get('/system/config', { params: { key } })
export const updateSystemConfig = (data) => request.post('/system/config', data)
export const getSystemConfigs = () => request.get('/system/configs')
export const getOperationLogs = (params) => request.get('/system/logs', { params })

// 站点配置（目标站点地址、发件域等，跑批时用）
export const getSiteConfig = () => request.get('/system/site-config')
export const updateSiteConfig = (data) => request.post('/system/site-config', data)
