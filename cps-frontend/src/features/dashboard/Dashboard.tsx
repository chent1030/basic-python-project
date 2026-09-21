import { useState } from 'react'
import { ArrowRight, ArrowUpRight, Camera, CheckCircle, Clock, GitBranch, ListChecks, Plus, ShieldCheck, Sparkle } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { useInspections } from '../../application/queries'
import { useSession } from '../../application/session'
import { hasRole, staffRoles } from '../../domain/cps'
import { Empty, ErrorNotice, Heading, Loading, Panel, TextLink } from '../../shared/ui'
import { InspectionTable, NewInspection } from '../inspections/List'

export function Dashboard() {
  const query = useInspections()
  const { identity } = useSession()
  const staff = hasRole(identity, ...staffRoles)
  const [creating, setCreating] = useState(false)
  const cases = query.data ?? []
  const total = query.data ? cases.length : '—'
  const active = query.data ? cases.filter(item => item.status === 'active').length : '—'
  const human = query.data ? cases.filter(item => ['needs_human', 'needs_input'].includes(item.status)).length : '—'
  const completed = query.data ? cases.filter(item => item.status === 'completed').length : '—'
  return <><Heading eyebrow="WORKSPACE OVERVIEW" title="巡检工作台" description={staff ? '让 Agent 处理分析，让人掌握每一次关键决策。' : '拍下问题、写清位置，提交后系统会自动安排后续处理。'}><button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={17} />{staff ? '新建巡检' : '上报问题'}</button></Heading>
    <section className="overview-strip"><div><span className="tiny-label">当前工作空间</span><div className="mt-2 flex items-center gap-3"><span className="workspace-avatar">{identity?.tenant.slice(0, 1).toUpperCase()}</span><div><h2 className="font-semibold">{identity?.tenant}</h2><p className="text-xs text-muted mt-1">CPS 智能巡检 · 人机协同工作流</p></div></div></div><div className="overview-note"><ShieldCheck size={22} weight="duotone" /><div><strong>决策始终由人确认</strong><p>调度改派与确认意见可进入待审记忆</p></div></div><div className="overview-index">01<span>/ WORKSPACE</span></div></section>
    <div className="stat-grid">{[
      { label: '全部巡检', value: total, note: '当前身份可访问的任务', icon: ListChecks },
      { label: '进行中', value: active, note: '包含执行中与待审批任务', icon: GitBranch },
      { label: '需补充 / 人工介入', value: human, note: '优先处理缺失信息与异常', icon: Clock },
      { label: '已完成', value: completed, note: '已满足任务交付要求', icon: CheckCircle },
    ].map(({ label, value, note, icon: Icon }, index) => <div className="stat" key={label}><div className="flex items-center justify-between"><span>{label}</span><Icon size={19} weight="duotone" className={index === 2 ? 'text-amber-700' : 'text-brand'} /></div><strong>{value}</strong><small>{note}</small></div>)}</div>
    <ErrorNotice error={query.error} />
    <div className="dashboard-grid"><Panel title={staff ? '最近巡检' : '我的上报'} extra={<TextLink to="/inspections">查看全部</TextLink>}>{query.isPending ? <Loading /> : cases.length ? <InspectionTable cases={cases.slice(0, 5)} /> : <Empty title="还没有上报记录" description="拍下现场问题，系统会帮你创建并派发处理任务。"><button className="btn" onClick={() => setCreating(true)}><Plus size={16} />上报第一个问题</button></Empty>}</Panel>
      {staff ? <section className="focus-card"><p className="eyebrow">HUMAN IN THE LOOP</p><div className="focus-symbol"><ShieldCheck size={40} weight="duotone" /></div><h2>下一步，<br />由你来决定。</h2><p>查看主 Agent 的调用建议，确认或改派专家。报告与工作计划仍需独立审核。</p><Link to="/approvals" className="btn btn-primary justify-between">进入确认中心<ArrowUpRight size={18} /></Link><div className="focus-footer"><span className="status-dot" />人工审批不可被模型跳过</div></section> : <section className="focus-card employee-focus"><p className="eyebrow">QUICK REPORT</p><div className="focus-symbol"><Camera size={40} weight="duotone" /></div><h2>发现问题，<br />马上上报。</h2><p>拍照、描述、填写位置，三步完成。后续的分析、派发和提醒由系统处理。</p><button className="btn btn-primary justify-between" onClick={() => setCreating(true)}>开始上报<ArrowUpRight size={18} /></button><div className="focus-footer"><span className="status-dot" />不需要选择 Agent</div></section>}</div>
    {staff && <Panel title="一次巡检如何流转" extra={<span className="text-xs text-muted">动态决策 · 非固定 Agent 顺序</span>}><div className="flow-strip">{[
      ['01', '现场资料', '任务独立工作区'], ['02', '主 Agent 建议', '按上下文选择专家'], ['03', '人工确认', '批准、改派或退回'], ['04', '专家执行', '输出后再次审核'], ['05', '回到主 Agent', '继续决策或完成'],
    ].map(([number, title, description], index) => <div className="flow-step" key={number}><span className={`flow-number ${index === 2 ? 'highlight' : ''}`}>{number}</span><div><h3>{title}</h3><p>{description}</p></div>{index < 4 && <ArrowRight className="flow-arrow" size={17} />}</div>)}</div><div className="observer-note"><Sparkle size={17} weight="duotone" /><span>旁路记忆观察员监听链路，提取候选经验；审核通过后才能用于后续决策。</span><TextLink to="/memories">查看记忆库</TextLink></div></Panel>}
    {creating && <NewInspection onClose={() => setCreating(false)} />}</>
}
