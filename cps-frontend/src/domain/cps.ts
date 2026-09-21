export type JsonObject = Record<string, unknown>
export type CaseStatus = 'collecting' | 'active' | 'needs_input' | 'needs_human' | 'completed' | 'cancelled'
export interface LineInfo {
  line_id: string
  area: string
  supervisor_id: string
  modifications: string
  expected_scenarios: string[]
}
export interface Evidence {
  id: string
  kind: 'before' | 'after' | 'context'
  issue_id?: string | null
  note: string
}
export interface Approval {
  id: string
  kind: string
  roles: string[]
  payload: JsonObject
  version: number
}
export interface Inspection {
  id: string
  owner: string
  goal: string
  line_info: LineInfo
  required_outputs: string[]
  status: CaseStatus
  version: number
  evidence_version: number
  evidence: Evidence[]
  results: JsonObject
  confirmations: JsonObject
  history: JsonObject[]
  notes: JsonObject[]
  jobs: string[]
  active_job: string | null
  rounds: number
  archived_report: string | null
  published_plan: string | null
  created_at: number
  updated_at: number
}
export interface InspectionDetail extends Inspection {
  phase: string
  can_finish: boolean
  outbox_error: JsonObject | null
  approvals: Approval[]
  active_run: { id: string; workflow: string; status: string; tokens_used: number; error_type: string | null; created: number } | null
}
export interface Agent {
  name: string
  version: string
  dispatchable: boolean
  output_schema: JsonObject
  review_roles: string[]
}
export interface MemoryRecord {
  id: string
  version: number
  state: string
  content: JsonObject
  expires?: number | null
}
export interface WorkTask {
  id: string
  inspection_id: string
  objective: string
  scope: string
  priority: string
  acceptance_criteria: string[]
  owner_id: string | null
  due_at: string | null
  dependencies: string[]
  status: string
  version: number
  updates: JsonObject[]
}
export interface AuditEvent {
  cursor: number
  kind: string
  [key: string]: unknown
}
export interface Identity {
  actor: string
  tenant: string
  roles: string[]
  expires: number
}
export const agentLabels: Record<string, string> = {
  main: '主决策 Agent', issue_identification: '问题识别', rectification_judgement: '整改判断',
  history_analysis: '历史分析', coverage_analysis: '覆盖度分析', report: '巡检报告',
  work_plan: '工作计划', observation: '记忆观察员',
}
export const outputLabels: Record<string, string> = {
  issues: '问题识别', rectification: '整改判断', history_analysis: '历史分析',
  coverage: '覆盖度分析', report: '巡检报告', work_plan: '工作计划',
}
export const statusLabels: Record<string, string> = {
  collecting: '待启动', active: '进行中', needs_input: '待补充资料', needs_human: '需人工介入',
  completed: '已完成', cancelled: '已取消', running: '执行中', queued: '排队中',
  waiting_dispatch_confirmation: '待调度确认', waiting_result_confirmation: '待结果确认',
  needs_operator_recovery: '投递异常', waiting_approval: '等待审批', succeeded: '执行成功',
  failed: '执行失败', pending: '待处理', accepted: '已采纳', rejected: '已拒绝', disabled: '已停用',
  proposed: '待审核', in_progress: '处理中', propose_agent: '调用下一位 Agent', request_input: '请求补充资料',
  request_human: '请求人工介入', finish: '结束巡检',
}
export const staffRoles = ['cps_dispatcher', 'cps_supervisor', 'cps_admin']
export function hasRole(identity: Identity | null, ...roles: string[]) {
  return Boolean(identity?.roles.some(role => roles.includes(role)))
}
export function parseIdentity(token: string): Identity {
  try {
    const encoded = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')
    const bytes = Uint8Array.from(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, '=')), character => character.charCodeAt(0))
    const claims = JSON.parse(new TextDecoder().decode(bytes))
    if (claims.type !== 'access' || !claims.sub || !claims.tenant_id || !Array.isArray(claims.roles)
      || !claims.roles.every((role: unknown) => typeof role === 'string') || !Number.isFinite(claims.exp)) throw new Error()
    if (claims.exp * 1000 <= Date.now()) throw new Error('访问令牌已过期，请重新获取。')
    return { actor: String(claims.sub), tenant: String(claims.tenant_id), roles: claims.roles, expires: claims.exp }
  } catch (error) {
    if (error instanceof Error && error.message.includes('过期')) throw error
    throw new Error('需要包含 sub、tenant_id、roles、exp 和 type=access 的 CPS 访问令牌。')
  }
}
export function versioned(version: number, payload: JsonObject = {}) {
  return { ...payload, expected_version: version, idempotency_key: crypto.randomUUID() }
}
export function dateTime(value: number | string | null | undefined) {
  if (!value) return '—'
  const date = new Date(typeof value === 'number' ? value * 1000 : value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}
export function objectText(value: unknown): string {
  if (value === null || value === undefined) return '—'
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2)
}
