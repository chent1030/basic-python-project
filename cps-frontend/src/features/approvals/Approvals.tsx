import { useQueries } from '@tanstack/react-query'
import { ArrowUpRight, ShieldCheck } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { useInspections } from '../../application/queries'
import { useSession } from '../../application/session'
import { agentLabels, dateTime } from '../../domain/cps'
import { Badge, Empty, ErrorNotice, Heading, Loading } from '../../shared/ui'

export function Approvals() {
  const { api, identity } = useSession()
  const inspections = useInspections()
  const candidates = (inspections.data ?? []).filter(item => item.active_job && !['completed', 'cancelled'].includes(item.status))
  const details = useQueries({ queries: candidates.map(item => ({
    queryKey: ['cps', identity?.tenant, identity?.actor, `inspection:${item.id}`],
    queryFn: ({ signal }: { signal: AbortSignal }) => api!.detail(item.id, signal), enabled: Boolean(api), refetchInterval: 5000, retry: false,
  })) })
  const waiting = details.flatMap(query => query.data?.approvals.length ? [query.data] : [])
  const loading = inspections.isPending || details.some(query => query.isPending)
  const failure = inspections.error || details.find(query => query.error)?.error
  return <><Heading eyebrow="HUMAN REVIEW" title="人工确认中心" description="每次调度都需要你来把关；改派理由将被记录，供后续记忆审核。" />
    <div className="notice"><ShieldCheck size={20} /><span>这里展示当前任务的真实待审节点。不同角色可执行的审批操作由后端校验。</span></div>
    <ErrorNotice error={failure} />{loading && <Loading />}
    <div className="grid gap-4">{waiting.map(item => <Link to={`/inspections/${item.id}?tab=review`} className="review-card" key={item.id}><div className="review-icon"><ShieldCheck size={24} weight="duotone" /></div><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-3"><h2>{item.goal}</h2><Badge status={item.phase} /></div><p>{item.line_info.area} / {item.line_info.line_id} · {agentLabels[item.active_run?.workflow.replace(/^cps_/, '') ?? 'main'] || 'Agent'} · 更新于 {dateTime(item.updated_at)}</p></div><span className="text-link">查看并确认<ArrowUpRight size={18} /></span></Link>)}</div>
    {!loading && !waiting.length && !failure && <div className="panel"><Empty title="当前没有待确认事项" description="Agent 产生调度建议或分析结果后，会自动出现在这里。" /></div>}
  </>
}
