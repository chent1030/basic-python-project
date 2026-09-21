import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Cpu, Files } from '@phosphor-icons/react'
import { useResource } from '../../application/queries'
import { useSession } from '../../application/session'
import { agentLabels, dateTime, hasRole, objectText, staffRoles, type Agent, type JsonObject } from '../../domain/cps'
import { Empty, ErrorNotice, Heading, JsonView, Loading, Modal, Panel } from '../../shared/ui'
import { StatisticsPanel } from './StatisticsPanel'

const descriptions: Record<string, string> = {
  main: '根据任务上下文与已审核记忆提出下一步建议；不能越过人工确认直接调用专家。',
  issue_identification: '依据真实现场照片识别问题，明确严重度、证据引用与判断限制。',
  rectification_judgement: '对照已确认问题及整改后证据，判断整改是否达标。',
  history_analysis: '分析已归档的巡检历史，不把缺失记录当作确定性事实。',
  coverage_analysis: '对照预期场景检查证据覆盖情况，标记未覆盖项。',
  report: '整合经确认的结论，形成需人工审核后归档的巡检报告。',
  work_plan: '将巡检结论转化为有负责人、时间与依赖关系的工作计划。',
  observation: '旁路观察调度与审核链路，只提出记忆候选，不直接激活长期记忆。',
}
export function Agents() {
  const { identity } = useSession()
  const allowed = hasRole(identity, ...staffRoles)
  const query = useResource('agents', (api, signal) => api.request<Agent[]>('/agents', { signal }), allowed, false)
  const [selected, setSelected] = useState<Agent | null>(null)
  return <><Heading eyebrow="AGENT DIRECTORY" title="Agent 能力目录" description="查看已注册的 Agent 职责与输出契约；模型、参数和组合方式由工程师在后端配置。" />
    <ErrorNotice error={query.error} />{!allowed ? <Empty title="需要调度员、主管或管理员角色" description="当前身份不能读取 Agent 目录。" /> : query.isPending ? <Loading /> : <div className="grid sm:grid-cols-2 xl:grid-cols-3 gap-5">{query.data?.map(agent => <article className="agent-card" key={agent.name}><div className="flex justify-between items-center"><span className="agent-icon"><Cpu size={25} weight="duotone" /></span><span className="tiny-label">v{agent.version}</span></div><h2>{agentLabels[agent.name] || agent.name}</h2><span className="font-mono text-xs text-muted">{agent.name}</span><p>{descriptions[agent.name]}</p><div className="flex items-center justify-between mt-auto pt-4 border-t border-line"><span className="text-xs text-muted">{agent.dispatchable ? '可调度专家' : agent.name === 'main' ? '主决策节点' : '旁路观察节点'}</span><button className="text-link" onClick={() => setSelected(agent)}>输出契约 →</button></div></article>)}</div>}
    {selected && <Modal title={`${agentLabels[selected.name]} · 输出契约`} onClose={() => setSelected(null)} wide><p className="text-sm mb-4">审核角色：{selected.review_roles.join('、')}</p><JsonView value={selected.output_schema} /></Modal>}
  </>
}
export function History() {
  const { identity } = useSession()
  const [selected, setSelected] = useState<JsonObject | null>(null)
  const allowed = hasRole(identity, 'cps_supervisor', 'cps_admin')
  const query = useResource('history', (api, signal) => api.all<JsonObject>('/history', signal), allowed)
  return <><Heading eyebrow="REPORT ARCHIVE" title="报告档案" description="只展示经过人工确认并正式归档的巡检记录。" />
    {!allowed ? <Empty title="需要主管或管理员角色" description="归档报告仅对 cps_supervisor 和 cps_admin 开放。" /> : <Panel title="已归档巡检"><ErrorNotice error={query.error} />{query.isPending ? <Loading /> : query.data?.length ? <div className="divide-y divide-line">{query.data.map(item => <article className="memory-row" key={String(item.id)}><Files size={26} weight="duotone" className="text-brand shrink-0" /><div className="flex-1 min-w-0"><h2>巡检 {String(item.inspection_id).slice(0, 12)}</h2><p>归档时间 {dateTime(Number(item.archived_at))}</p><details><summary>归档内容与来源</summary><JsonView value={item} /></details></div><div className="flex flex-col gap-2"><button className="btn" disabled={typeof item.html !== 'string'} onClick={() => setSelected(item)}>查看归档快照</button><Link className="text-link" to={`/inspections/${item.inspection_id}?tab=results`}>查看当前巡检 →</Link></div></article>)}</div> : <Empty title="还没有归档报告" description="请先确认报告内容，再在巡检详情中执行归档。" />}</Panel>}
    {selected && <Modal title="归档报告快照" onClose={() => setSelected(null)} wide><p className="text-xs text-muted mb-4">归档时间 {dateTime(Number(selected.archived_at))} · 此处展示归档时的确认版本，不是巡检的最新草稿。</p><iframe title="归档报告" sandbox="" srcDoc={String(selected.html)} className="report-frame" /></Modal>}
  </>
}
export function Statistics() {
  const { identity } = useSession()
  const allowed = hasRole(identity, 'cps_supervisor', 'cps_admin')
  const admin = hasRole(identity, 'cps_admin')
  const stats = useResource('statistics', (api, signal) => api.request<JsonObject>('/statistics', { signal }), allowed)
  const metrics = useResource('metrics', (api, signal) => api.request<JsonObject>('/metrics', { signal }), admin)
  const rate = (value: unknown) => typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : '—'
  return <><Heading eyebrow="QUALITY & LEARNING" title="统计与效果" description="使用后端计算的真实统计口径，不将记忆相关性误读为因果优化。" />
    {!allowed ? <Empty title="需要主管或管理员角色" description="当前身份不能读取统计数据。" /> : <><ErrorNotice error={stats.error || metrics.error} />
      {admin && <div className="stat-grid">{[['direct_approval_rate', '直接批准率'], ['redispatch_rate', '人工改派率'], ['result_edit_rate', '结果修改率'], ['memory_acceptance_rate', '记忆采纳率']].map(([key, label]) => <div className="stat" key={key}><span>{label}</span><strong>{rate(metrics.data?.[key])}</strong><small>无样本时显示 —，不计为 0</small></div>)}</div>}
      <Panel title="历史巡检统计">{stats.isPending ? <Loading /> : stats.data ? <StatisticsPanel data={stats.data} /> : null}</Panel>
      {admin && <Panel title="记忆使用与决策效果"><div className="p-6">{metrics.isPending ? <Loading /> : <><div className="notice">{Array.isArray(metrics.data?.limitations) ? metrics.data.limitations.map(objectText).join(' ') : '分组统计仅用于观察，需要结合样本量判断。'}</div><details className="mt-4"><summary>查看完整指标、样本数量与分组比较</summary><JsonView value={metrics.data} /></details></>}</div></Panel>}
    </>}
  </>
}
