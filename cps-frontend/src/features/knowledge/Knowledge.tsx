import { useState } from 'react'
import { BookOpen, ClockCounterClockwise, MagnifyingGlass, Sparkle } from '@phosphor-icons/react'
import { useCommand, useResource } from '../../application/queries'
import { useSession } from '../../application/session'
import { hasRole, objectText, type JsonObject, type MemoryRecord } from '../../domain/cps'
import { Badge, Empty, ErrorNotice, Field, Heading, JsonView, Loading, Modal, SubmitButton } from '../../shared/ui'

function MemoryReview({ memory, onClose }: { memory: MemoryRecord; onClose: () => void }) {
  const [action, setAction] = useState(memory.state === 'disabled' ? 'enable' : memory.state === 'accepted' ? 'disable' : 'accept')
  const [error, setError] = useState<unknown>(null)
  const versions = useResource(`memory-versions:${memory.id}`, api => api.request<MemoryRecord[]>(`/memories/${encodeURIComponent(memory.id)}/versions`), true, false)
  const command = useCommand<JsonObject>((api, body) => api.post(`/memories/${encodeURIComponent(memory.id)}/review`, body))
  return <Modal title="审核与管理长期记忆" onClose={onClose} wide><form className="form-stack" onSubmit={async event => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setError(null)
    try {
      await command.mutateAsync({ expected_version: memory.version, action, reason: data.get('reason'),
        ...(['accept', 'reject'].includes(action) ? { scope: data.get('scope'), knowledge: data.get('knowledge') } : {}),
        ...(action === 'rollback' ? { rollback_version: Number(data.get('rollback_version')) } : {}),
        ...(data.get('expires_at') ? { expires_at: new Date(String(data.get('expires_at'))).toISOString() } : {}),
      })
      onClose()
    } catch (failure) { setError(failure) }
  }}>
    <div className="notice"><Sparkle size={18} /><p>只有审核通过的记忆才能用于后续决策。停用与回滚不会抹除历史审核记录。</p></div>
    <details><summary>原始候选、来源与适用限制</summary><JsonView value={memory.content} /></details>
    <Field label="操作"><select value={action} onChange={event => setAction(event.target.value)}><option value="accept">审核采纳</option><option value="reject">审核拒绝</option><option value="disable">停用记忆</option><option value="enable">重新启用</option><option value="rollback">回滚到历史版本</option></select></Field>
    {['accept', 'reject'].includes(action) && <><Field label="适用范围"><input name="scope" required maxLength={100} defaultValue={String(memory.content.scope ?? '')} /></Field><Field label="记忆内容"><textarea name="knowledge" rows={5} required maxLength={10000} defaultValue={String(memory.content.knowledge ?? '')} /></Field></>}
    {action === 'rollback' && <><ErrorNotice error={versions.error} /><Field label="目标版本"><select name="rollback_version" required defaultValue=""><option value="" disabled>选择已审核的历史版本</option>{versions.data?.filter(item => item.version < memory.version && ['accepted', 'disabled'].includes(item.state)).map(item => <option key={item.version} value={item.version}>v{item.version} · {item.state}</option>)}</select></Field></>}
    <Field label="过期时间（选填）" hint="不填写则保持现有有效期。"><input name="expires_at" type="datetime-local" /></Field>
    <Field label="审核理由"><textarea name="reason" rows={3} required maxLength={5000} /></Field>
    <details><summary>版本历史</summary><ErrorNotice error={versions.error} /><JsonView value={versions.data ?? []} /></details>
    <ErrorNotice error={error} /><div className="form-footer"><button type="button" className="btn" onClick={onClose}>取消</button><SubmitButton pending={command.isPending}>保存审核</SubmitButton></div>
  </form></Modal>
}
export function Knowledge() {
  const { identity } = useSession()
  const allowed = hasRole(identity, 'cps_admin')
  const query = useResource('memories', (api, signal) => api.all<MemoryRecord>('/memories', signal), allowed)
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<MemoryRecord | null>(null)
  const records = (query.data ?? []).filter(item => (filter === 'all' || item.state === filter) && objectText(item.content).toLowerCase().includes(search.toLowerCase()))
  return <><Heading eyebrow="LONG-TERM MEMORY" title="经验记忆库" description="从人工决策中沉淀经验，不让未审核的建议影响下一次巡检。" />
    <div className="memory-explainer"><div><ClockCounterClockwise size={23} weight="duotone" /><h3>观察完整链路</h3><p>捕获调度、改派与结果确认</p></div><span>→</span><div><BookOpen size={23} weight="duotone" /><h3>审核候选记忆</h3><p>明确适用范围、来源与限制</p></div><span>→</span><div><Sparkle size={23} weight="duotone" /><h3>辅助后续决策</h3><p>按实际效果评估，不自动宣称优化</p></div></div>
    {!allowed ? <div className="panel"><Empty title="需要 CPS 管理员角色" description="记忆内容、审核与版本管理仅对 cps_admin 开放。" /></div> : <div className="panel"><div className="toolbar"><label className="search-input"><MagnifyingGlass size={18} /><input placeholder="搜索记忆内容或适用范围…" aria-label="搜索记忆" value={search} onChange={event => setSearch(event.target.value)} /></label><select aria-label="记忆状态" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部状态</option><option value="proposed">待审核</option><option value="accepted">已采纳</option><option value="rejected">已拒绝</option><option value="disabled">已停用</option></select></div><ErrorNotice error={query.error} />{query.isPending ? <Loading /> : records.length ? <div className="divide-y divide-line">{records.map(item => <article className="memory-row" key={item.id}><span className="memory-icon"><BookOpen size={22} weight="duotone" /></span><div className="min-w-0 flex-1"><div className="flex flex-wrap gap-3 mb-2"><Badge status={item.state} /><span className="text-xs text-muted">{objectText(item.content.scope)} · v{item.version}</span></div><h2>{objectText(item.content.knowledge)}</h2><p>适用条件：{objectText(item.content.condition)}</p><details><summary>来源与限制</summary><JsonView value={item.content} /></details></div><button className="btn" onClick={() => setSelected(item)}>审核 / 管理</button></article>)}</div> : <Empty title="暂无匹配的经验记忆" description="报告归档等事件触发旁路观察后，候选记忆将在这里等待人工审核。" />}</div>}
    {selected && <MemoryReview memory={selected} onClose={() => setSelected(null)} />}
  </>
}
