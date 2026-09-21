import { Component, useState, type ReactNode } from 'react'
import { NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { ArrowRight, BookOpen, ChartBar, CheckSquare, CirclesFour, Cpu, Files, GearSix, List, ListChecks, PlugsConnected, ShieldCheck, SignOut, X } from '@phosphor-icons/react'
import { useSession } from './application/session'
import { dateTime, hasRole, staffRoles } from './domain/cps'
import { Empty, ErrorNotice, Field, Modal, SubmitButton } from './shared/ui'
import { Dashboard } from './features/dashboard/Dashboard'
import { Inspections } from './features/inspections/List'
import { InspectionDetailPage } from './features/inspections/Detail'
import { Approvals } from './features/approvals/Approvals'
import { Knowledge } from './features/knowledge/Knowledge'
import { Tasks } from './features/tasks/Tasks'
import { Agents, History, Statistics } from './features/catalog/Catalog'

const navigation = [
  { path: '/', name: '工作台', icon: CirclesFour, group: '工作空间' },
  { path: '/inspections', name: '巡检任务', icon: ListChecks },
  { path: '/approvals', name: '人工确认', icon: ShieldCheck, staff: true },
  { path: '/tasks', name: '计划任务', icon: CheckSquare, staff: true },
  { path: '/history', name: '报告档案', icon: Files, staff: true },
  { path: '/memories', name: '经验记忆', icon: BookOpen, group: '智能协作', admin: true },
  { path: '/agents', name: 'Agent 目录', icon: Cpu, staff: true },
  { path: '/statistics', name: '统计与效果', icon: ChartBar, staff: true },
]
function Connection({ onClose }: { onClose: () => void }) {
  const { identity, connect, disconnect } = useSession()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<unknown>(null)
  return <Modal title={identity ? '当前连接' : '连接 CPS 后端'} onClose={onClose}>{identity ? <div className="form-stack"><dl className="metadata"><div><dt>工作空间</dt><dd>{identity.tenant}</dd></div><div><dt>当前用户</dt><dd>{identity.actor}</dd></div><div><dt>会话到期</dt><dd>{dateTime(identity.expires)}</dd></div></dl><p className="text-sm text-muted break-words">角色：{identity.roles.join('、')}</p><p className="text-xs text-muted">令牌仅保存在当前页面内存中；刷新、断开或到期后需要重新连接。</p><button className="btn btn-danger" onClick={() => { disconnect(); onClose() }}><SignOut size={16} />断开并清除会话</button></div> : <form className="form-stack" onSubmit={async event => {
    event.preventDefault()
    const token = String(new FormData(event.currentTarget).get('token'))
    setError(null); setPending(true)
    try { await connect(token); onClose() } catch (failure) { setError(failure) } finally { setPending(false) }
  }}><p className="text-sm leading-7 text-muted">使用企业身份系统签发的 CPS 访问令牌连接。令牌必须包含租户、用户与 CPS 角色；服务端会验证签名和权限。</p><Field label="访问令牌" hint="令牌不写入本地存储，不进入 URL 或日志。"><textarea name="token" required rows={5} autoComplete="off" spellCheck={false} placeholder="粘贴 access token，可包含 Bearer 前缀" /></Field><div className="notice text-xs">根项目的示例 /auth/token 不包含 CPS 身份声明，不能用于此工作台。开发令牌的签发方式见前端 README。</div><ErrorNotice error={error} /><div className="form-footer"><button type="button" className="btn" onClick={onClose}>取消</button><SubmitButton pending={pending}>验证并连接</SubmitButton></div></form>}</Modal>
}
function Welcome({ connect }: { connect: () => void }) {
  return <div className="welcome"><div className="welcome-copy"><p className="eyebrow">CPS / INSPECTION INTELLIGENCE</p><h1>每一次巡检，<br />都有据可循。</h1><p>将现场证据、Agent 分析与人工决策<br className="hidden sm:block" />放进同一个可追溯的工作空间。</p><button className="btn btn-primary" onClick={connect}><PlugsConnected size={19} />连接工作空间<ArrowRight size={18} /></button><span className="welcome-footnote"><ShieldCheck size={15} />真实业务数据 · 任务隔离 · 人工确认</span></div><div className="welcome-diagram"><div className="diagram-top"><span className="status-dot" /><span>人机协同决策链路</span><span className="ml-auto font-mono text-xs">CPS</span></div><div className="diagram-node"><Cpu size={25} weight="duotone" /><div><strong>主 Agent</strong><span>理解目标，提出下一步建议</span></div><span className="node-index">01</span></div><div className="diagram-line" /><div className="diagram-node human"><ShieldCheck size={25} weight="duotone" /><div><strong>人工决策</strong><span>确认调用 · 修改下一位 Agent</span></div><span className="node-index">02</span></div><div className="diagram-line" /><div className="diagram-node"><CirclesFour size={25} weight="duotone" /><div><strong>专家协作</strong><span>执行分析，提交待确认成果</span></div><span className="node-index">03</span></div><div className="diagram-caption"><BookOpen size={18} />旁路观察 → 候选记忆 → 人工审核</div></div><div className="welcome-bottom"><span>FIELD EVIDENCE</span><span>AGENT COLLABORATION</span><span>HUMAN JUDGEMENT</span></div></div>
}
class ErrorBoundary extends Component<{ children: ReactNode }, { error: boolean }> {
  state = { error: false }
  static getDerivedStateFromError() { return { error: true } }
  render() { return this.state.error ? <Empty title="页面暂时无法显示" description="请重新加载页面；刷新后需要重新连接工作空间。"><button className="btn" onClick={() => window.location.reload()}>重新加载</button></Empty> : this.props.children }
}
export function App() {
  const { identity } = useSession()
  const [connection, setConnection] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const location = useLocation()
  const canStaff = hasRole(identity, ...staffRoles)
  const canAdmin = hasRole(identity, 'cps_admin')
  const visibleNavigation = navigation.filter(item => (!item.staff || canStaff) && (!item.admin || canAdmin))
  const current = visibleNavigation.find(item => item.path === location.pathname || (item.path !== '/' && location.pathname.startsWith(item.path)))
  return <ErrorBoundary><div className="app-shell"><a href="#main-content" className="skip-link">跳到主要内容</a>{mobileNav && <button className="nav-scrim" aria-label="关闭导航" onClick={() => setMobileNav(false)} />}
    <aside className={`sidebar ${mobileNav ? 'sidebar-open' : ''}`}><NavLink to="/" className="brand" onClick={() => setMobileNav(false)} aria-label="CPS 工作台"><span className="brand-mark"><CirclesFour size={24} weight="fill" /></span><span>CPS<span className="brand-subtitle">智能巡检</span></span></NavLink><button className="workspace-selector" onClick={() => setConnection(true)}><span className="workspace-icon">{identity?.tenant.slice(0, 1).toUpperCase() || 'W'}</span><span className="truncate">{identity?.tenant || '连接工作空间'}<small>{identity ? '巡检运营中心' : '尚未连接后端'}</small></span><GearSix size={16} className="ml-auto shrink-0" /></button>
      <nav className="sidebar-nav" aria-label="主导航">{visibleNavigation.map(({ path, name, icon: Icon, group }) => <div key={path}>{group && <p className="nav-group">{group}</p>}<NavLink to={path} end={path === '/'} onClick={() => setMobileNav(false)} className={({ isActive }) => `nav-item ${isActive ? 'selected' : ''}`}><Icon size={20} weight="duotone" />{name}</NavLink></div>)}</nav>
      <div className="sidebar-bottom"><div className="workspace-note"><span className="status-dot" /><span>Human-led. Agent-powered.</span></div><button className="user-button" onClick={() => setConnection(true)}><span className="user-avatar">{identity?.actor.slice(0, 1).toUpperCase() || 'U'}</span><span className="truncate">{identity?.actor || '未连接'}<small>{identity ? '已验证的 CPS 会话' : '使用访问令牌连接'}</small></span><GearSix size={18} className="ml-auto shrink-0" /></button></div></aside>
    <div className="main-shell"><header className="topbar"><button className="icon-button lg:hidden" aria-label={mobileNav ? '关闭导航' : '展开导航'} onClick={() => setMobileNav(!mobileNav)}>{mobileNav ? <X size={21} /> : <List size={21} />}</button><span className="text-muted">工作空间</span><span className="text-faint">/</span><span>{current?.name || '巡检详情'}</span><div className="ml-auto flex items-center gap-5"><span className="hidden sm:inline text-xs text-muted">{new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'short' })}</span><button className={`connection-pill ${identity ? 'connected' : ''}`} onClick={() => setConnection(true)}><span className="status-dot" />{identity ? '后端已连接' : '连接后端'}</button></div></header>
      <main id="main-content" className="main-content">{!identity ? <Welcome connect={() => setConnection(true)} /> : <Routes><Route path="/" element={<Dashboard />} /><Route path="/inspections" element={<Inspections />} /><Route path="/inspections/:id" element={<InspectionDetailPage />} /><Route path="/approvals" element={<Approvals />} /><Route path="/tasks" element={<Tasks />} /><Route path="/history" element={<History />} /><Route path="/memories" element={<Knowledge />} /><Route path="/agents" element={<Agents />} /><Route path="/statistics" element={<Statistics />} /><Route path="*" element={<Empty title="页面不存在" description="请从左侧导航选择工作页面。" />} /></Routes>}<footer className="page-footer"><span>CPS 智能巡检工作台</span><span>决策留痕 · 结果可核验</span></footer></main>
    </div>{connection && <Connection onClose={() => setConnection(false)} />}
  </div></ErrorBoundary>
}
