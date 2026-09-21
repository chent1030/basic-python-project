import type { AuditEvent, Inspection, InspectionDetail, JsonObject } from '../domain/cps'

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}
function errorMessage(body: unknown, status: number) {
  if (status === 401) return '会话已失效，请断开后重新连接。'
  if (status === 403) return '当前身份无权访问该资源，请检查租户和 CPS 角色。'
  if (status === 409) return '数据版本已变化或当前状态不允许此操作。请刷新后核对并重新提交。'
  const detail = body && typeof body === 'object' ? (body as JsonObject).detail ?? (body as JsonObject).message : body
  if (Array.isArray(detail)) return detail.map(item => `${item.loc?.slice(1).join('.') || '请求'}：${item.msg}`).join('；')
  return typeof detail === 'string' ? detail : `请求失败（${status}），请检查后端服务。`
}
export class CPSApi {
  constructor(private token: string, private base = import.meta.env.VITE_API_BASE_URL || '/api/v1') {}
  async request<T>(path: string, options: RequestInit = {}): Promise<T> {
    let response: Response
    try {
      response = await fetch(`${this.base.replace(/\/$/, '')}/cps${path}`, {
        ...options,
        cache: 'no-store',
        headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...options.headers, Authorization: `Bearer ${this.token}` },
      })
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') throw error
      throw new ApiError(0, '无法连接 CPS 后端，请检查服务是否启动以及代理地址配置。')
    }
    if (!response.ok) {
      const body = await response.json().catch(() => null)
      throw new ApiError(response.status, errorMessage(body, response.status))
    }
    if (response.headers.get('content-type')?.includes('application/json')) return response.json()
    if (path.endsWith('/report.html')) return await response.text() as T
    throw new ApiError(502, '后端没有返回 JSON 数据，请检查 API 地址和反向代理配置。')
  }
  async all<T>(path: string, signal?: AbortSignal): Promise<T[]> {
    const records: T[] = []
    for (let offset = 0; ; offset += 200) {
      const page = await this.request<T[]>(`${path}?offset=${offset}&limit=200`, { signal })
      records.push(...page)
      if (page.length < 200) return records
    }
  }
  inspections(signal?: AbortSignal) { return this.all<Inspection>('/inspections', signal) }
  detail(id: string, signal?: AbortSignal) { return this.request<InspectionDetail>(`/inspections/${encodeURIComponent(id)}`, { signal }) }
  async events(id: string, signal?: AbortSignal): Promise<AuditEvent[]> {
    const records: AuditEvent[] = []
    let cursor = 0
    for (;;) {
      const batch = await this.request<AuditEvent[]>(`/inspections/${encodeURIComponent(id)}/events?after=${cursor}`, { signal })
      records.push(...batch)
      if (batch.length < 200) return records
      const next = batch[batch.length - 1].cursor
      if (next <= cursor) throw new ApiError(502, '事件游标没有前进，请检查后端事件接口。')
      cursor = next
    }
  }
  post<T>(path: string, body: unknown) { return this.request<T>(path, { method: 'POST', body: JSON.stringify(body) }) }
  patch<T>(path: string, body: unknown) { return this.request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }) }
  async evidence(caseId: string, evidenceId: string, signal?: AbortSignal) {
    const response = await fetch(`${this.base.replace(/\/$/, '')}/cps/inspections/${encodeURIComponent(caseId)}/evidence/${encodeURIComponent(evidenceId)}`, {
      signal, cache: 'no-store', headers: { Authorization: `Bearer ${this.token}` },
    })
    if (!response.ok) throw new ApiError(response.status, errorMessage(null, response.status))
    return response.blob()
  }
}
