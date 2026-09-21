import { useEffect, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ArrowLeft, ArrowRight, ArrowSquareOut, ArrowsClockwise, CheckCircle, FileText, Image, Play, Plus, ShieldCheck } from '@phosphor-icons/react'
import { useCommand, useInspection, useResource } from '../../application/queries'
import { useSession } from '../../application/session'
import { agentLabels, dateTime, hasRole, outputLabels, staffRoles, type Evidence, type InspectionDetail } from '../../domain/cps'
import { Badge, Empty, ErrorNotice, JsonView, Loading, Modal, Panel } from '../../shared/ui'
import { CaseActionModal, type CaseAction } from './Actions'

function EvidenceImage({ inspectionId, evidence }: { inspectionId: string; evidence: Evidence }) {
  const { api } = useSession()
  const [url, setUrl] = useState('')
  const [error, setError] = useState<unknown>(null)
  useEffect(() => {
    if (!api) return
    const controller = new AbortController()
    let objectUrl = ''
    setError(null)
    api.evidence(inspectionId, evidence.id, controller.signal).then(blob => {
      if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl) }
    }).catch(failure => { if (!controller.signal.aborted) setError(failure) })
    return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [api, inspectionId, evidence.id])
  return <figure className="evidence-card">{url ? <a href={url} target="_blank" rel="noreferrer"><img src={url} alt={evidence.note || '现场巡检证据'} /></a> : <div className="evidence-placeholder"><Image size={28} /><ErrorNotice error={error} /></div>}<figcaption><span className="tiny-label">{{ before: '整改前', after: '整改后', context: '现场环境' }[evidence.kind]}</span><p>{evidence.note || '未填写证据说明'}</p>{evidence.issue_id && <small>关联问题 {evidence.issue_id}</small>}</figcaption></figure>
}
function ReportPreview({ inspectionId, onClose }: { inspectionId: string; onClose: () => void }) {
  const query = useResource(`report:${inspectionId}`, api => api.request<string>(`/inspections/${encodeURIComponent(inspectionId)}/report.html`), true, false)
  return <Modal title="巡检报告预览" onClose={onClose} wide><ErrorNotice error={query.error} />{query.isPending ? <Loading /> : <iframe title="巡检报告" sandbox="" srcDoc={query.data} className="report-frame" />}</Modal>
}
function ReviewPanel({ inspection, act }: { inspection: InspectionDetail; act: (action: CaseAction) => void }) {
  const { identity } = useSession()
  const dispatch = inspection.phase === 'waiting_dispatch_confirmation'
  const approval = inspection.approvals[0]
  const allowed = approval && hasRole(identity, ...approval.roles)
  if (!approval) return <Empty title="暂无待审内容" description="Agent 完成分析并提交结果后，可在这里确认或修改。" />
  return <div className="p-6"><div className="notice"><ShieldCheck size={22} /><div><strong>{dispatch ? '请确认下一步是否调用 Agent' : '请审核本次 Agent 输出'}</strong><p className="mt-1 text-xs">{dispatch ? '可以批准、改派、跳过、重试、退回或转人工。改派不会静默执行。' : '结果确认与调度确认相互独立；未经确认的报告不能归档。'}</p></div></div>
    <div className="my-5"><JsonView value={approval.payload} /></div>
    <button className="btn btn-primary" disabled={!allowed} onClick={() => act(dispatch ? 'dispatch' : 'review')}><ShieldCheck size={17} />{dispatch ? '审核调度建议' : '审核分析结果'}</button>
    {!allowed && <p className="text-sm text-muted mt-3">此节点需要角色：{approval.roles.join('、')}</p>}
  </div>
}
export function InspectionDetailPage() {
  const { id = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') || 'overview'
  const query = useInspection(id)
  const events = useResource(`events:${id}`, (api, signal) => api.events(id, signal), tab === 'timeline', 3000)
  const { identity } = useSession()
  const [action, setAction] = useState<CaseAction | null>(null)
  const [preview, setPreview] = useState(false)
  const sync = useCommand<void>(api => api.post(`/inspections/${encodeURIComponent(id)}/sync`, {}))
  if (query.isPending) return <Loading />
  if (!query.data) return <ErrorNotice error={query.error} />
  const item = query.data
  const staff = hasRole(identity, ...staffRoles)
  const reviewer = hasRole(identity, 'cps_supervisor', 'cps_admin')
  const admin = hasRole(identity, 'cps_admin')
  const inactive = !item.active_job && !['completed', 'cancelled'].includes(item.status)
  const currentAgent = item.active_run?.workflow.replace(/^cps_/, '')
  return <><Link to="/inspections" className="text-link mb-6 inline-flex"><ArrowLeft size={15} />返回巡检任务</Link>
    <div className="detail-heading"><div><div className="flex flex-wrap items-center gap-3 mb-3"><span className="eyebrow">INSPECTION / {item.id.slice(0, 12).toUpperCase()}</span><Badge status={item.phase} /></div><h1>{item.goal}</h1><p>{item.line_info.area} / {item.line_info.line_id} <span>·</span> 负责人 {item.line_info.supervisor_id} <span>·</span> v{item.version}</p></div><button className="btn" disabled={sync.isPending} onClick={() => sync.mutate()}><ArrowsClockwise size={16} className={sync.isPending ? 'animate-spin' : ''} />同步状态</button></div>
    <ErrorNotice error={query.error || sync.error || item.outbox_error} />
    <div className="detail-actions">{inactive && <><button className="btn btn-primary" onClick={() => setAction('start')}><Play size={16} />启动巡检</button><button className="btn" onClick={() => setAction('evidence')}><Plus size={16} />上传证据</button><button className="btn" onClick={() => setAction('amend')}>补充资料</button></>}{reviewer && inactive && <button className="btn" onClick={() => setAction('manual')}>提交人工结果</button>}{staff && !['completed', 'cancelled'].includes(item.status) && <button className="btn btn-danger ml-auto" onClick={() => setAction('stop')}>退回 / 取消</button>}</div>
    <div className="detail-grid"><div className="min-w-0"><nav className="tabs" aria-label="巡检详情标签">{[['overview', '任务概览'], ['review', `人工确认${item.approvals.length ? ` (${item.approvals.length})` : ''}`], ['evidence', '现场证据'], ['results', '报告与成果'], ['timeline', '运行监控']].map(([key, label]) => <button className={tab === key ? 'active' : ''} key={key} onClick={() => setParams({ tab: key })} aria-current={tab === key ? 'page' : undefined}>{label}</button>)}</nav>
      <div className="panel min-h-80">
        {tab === 'overview' && <div className="p-6 space-y-6"><div><h2 className="section-title">巡检目标</h2><p className="text-sm leading-7 whitespace-pre-wrap">{item.goal}</p></div><div><h2 className="section-title">预期交付</h2><div className="flex flex-wrap gap-2">{item.required_outputs.map(output => <span key={output} className={`output-chip ${item.confirmations[output] ? 'confirmed' : ''}`}><CheckCircle size={15} />{outputLabels[output] || output}{item.confirmations[output] ? ' · 已确认' : ''}</span>)}</div></div><div><h2 className="section-title">现场变化与覆盖场景</h2><p className="text-sm text-muted leading-7">{item.line_info.modifications || '暂无变化说明'}</p><div className="flex flex-wrap gap-2 mt-3">{item.line_info.expected_scenarios.map((scenario, index) => <span className="output-chip" key={`${scenario}-${index}`}>{scenario}</span>)}</div></div><div className="notice"><ShieldCheck size={20} /><p>专家输出需单独审核；确认报告后归档，确认工作计划后发布任务，最后由主 Agent 提议结束。</p></div></div>}
        {tab === 'review' && <ReviewPanel inspection={item} act={setAction} />}
        {tab === 'evidence' && (item.evidence.length ? <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4 p-6">{item.evidence.map(evidence => <EvidenceImage key={evidence.id} inspectionId={id} evidence={evidence} />)}</div> : <Empty title="还没有现场证据" description="请上传真实现场照片。问题识别需要整改前证据，整改判断需要关联问题的整改后照片。" />)}
        {tab === 'results' && <div className="p-6 space-y-5"><div className="flex flex-wrap gap-2"><button className="btn" disabled={!item.results.report && !(currentAgent === 'report' && item.approvals.length)} onClick={() => setPreview(true)}><FileText size={16} />预览报告</button><button className="btn" disabled={!admin || !item.confirmations.report || Boolean(item.archived_report) || item.status === 'cancelled'} onClick={() => setAction('archive')}>{item.archived_report ? '报告已归档' : '归档报告'}</button><button className="btn" disabled={!admin || !item.confirmations.work_plan || Boolean(item.published_plan) || item.status === 'cancelled'} onClick={() => setAction('publish')}>{item.published_plan ? '计划已发布' : '发布工作计划'}</button></div>{Object.entries(item.results).length ? Object.entries(item.results).map(([key, value]) => <details className="result-section" key={key} open><summary>{outputLabels[key] || key}<span className="text-xs text-muted ml-3">{item.confirmations[key] ? '已人工确认' : '未确认'}</span></summary><JsonView value={value} /></details>) : <Empty title="尚无确认成果" description="待审 Agent 输出请在「人工确认」中查看；确认后显示在这里。" />}</div>}
        {tab === 'timeline' && <div className="p-6"><div className="flex justify-between items-center mb-5"><h2 className="section-title mb-0">链路事件</h2><span className="text-xs text-muted">每 3 秒自动更新</span></div><ErrorNotice error={events.error} />{events.isPending ? <Loading /> : events.data?.length ? <ol className="timeline">{[...events.data].reverse().map(event => <li key={event.cursor}><span className="timeline-dot" /><div className="flex justify-between gap-3"><strong>{event.kind}</strong><span className="text-xs text-muted">#{event.cursor}</span></div><details><summary>查看事件详情</summary><JsonView value={event} /></details></li>)}</ol> : <Empty title="暂无链路事件" description="启动任务后将记录执行、审批、投递与观察事件。" />}</div>}
        {!['overview', 'review', 'evidence', 'results', 'timeline'].includes(tab) && <Empty title="未找到此标签" description="请选择上方的任务详情标签。" />}
      </div></div><aside className="space-y-5"><Panel title="当前执行节点" extra={<span className="status-dot" />}><div className="p-5"><span className="tiny-label">ACTIVE AGENT</span><h3 className="text-lg font-semibold mt-2">{currentAgent ? agentLabels[currentAgent] || currentAgent : '暂无执行节点'}</h3><div className="agent-path"><span>主 Agent</span><ArrowRight size={14} /><span>人工</span><ArrowRight size={14} /><span>专家</span></div><dl className="metadata"><div><dt>调度轮次</dt><dd>{item.rounds}</dd></div><div><dt>运行任务数</dt><dd>{item.jobs.length}</dd></div><div><dt>当前 Token</dt><dd>{item.active_run?.tokens_used ?? '—'}</dd></div><div><dt>证据版本</dt><dd>v{item.evidence_version}</dd></div><div><dt>创建时间</dt><dd>{dateTime(item.created_at)}</dd></div></dl>{item.active_run && <details className="mt-4"><summary>运行标识与异常</summary><JsonView value={item.active_run} /></details>}</div></Panel><div className="detail-tip"><ShieldCheck size={23} weight="duotone" /><h3>任务级隔离</h3><p>证据、执行记录和审批归属本次巡检。只有经审核的长期记忆可用于后续任务。</p><Link className="text-link mt-4" to="/memories">查看长期记忆<ArrowSquareOut size={14} /></Link></div><details><summary>任务审计历史</summary><JsonView value={{ history: item.history, notes: item.notes, confirmations: item.confirmations, workspace: item.id }} /></details></aside></div>
    {action && <CaseActionModal kind={action} inspection={item} onClose={() => setAction(null)} />}{preview && <ReportPreview inspectionId={id} onClose={() => setPreview(false)} />}
  </>
}
