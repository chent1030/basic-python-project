import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useCommand, useResource } from '../../application/queries'
import { dateTime, versioned, type JsonObject, type WorkTask } from '../../domain/cps'
import { Badge, Empty, ErrorNotice, Field, Heading, JsonView, Loading, Modal, SubmitButton } from '../../shared/ui'

function TaskUpdate({ task, onClose }: { task: WorkTask; onClose: () => void }) {
  const [error, setError] = useState<unknown>(null)
  const command = useCommand<JsonObject>((api, body) => api.patch(`/tasks/${encodeURIComponent(task.id)}`, body))
  return <Modal title="更新计划任务" onClose={onClose}><form className="form-stack" onSubmit={async event => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    try { await command.mutateAsync(versioned(task.version, { status: data.get('status'), note: data.get('note') })); onClose() } catch (failure) { setError(failure) }
  }}><p className="font-medium">{task.objective}</p><Field label="任务状态"><select name="status" defaultValue={task.status === 'in_progress' ? 'completed' : 'in_progress'}><option value="in_progress">开始处理</option><option value="completed">标记完成</option><option value="cancelled">取消任务</option></select></Field><Field label="进展说明"><textarea name="note" required rows={4} maxLength={5000} placeholder="记录处理过程或结果依据" /></Field><p className="text-xs text-muted">依赖任务未完成时，后端将拒绝不符合条件的状态变更。</p><ErrorNotice error={error} /><div className="form-footer"><button type="button" className="btn" onClick={onClose}>取消</button><SubmitButton pending={command.isPending}>更新状态</SubmitButton></div></form></Modal>
}
export function Tasks() {
  const query = useResource('tasks', (api, signal) => api.all<WorkTask>('/tasks', signal))
  const [selected, setSelected] = useState<WorkTask | null>(null)
  const [filter, setFilter] = useState('all')
  const records = (query.data ?? []).filter(item => filter === 'all' || item.status === filter)
  return <><Heading eyebrow="ACTION PLANS" title="计划任务" description="跟进已确认、已发布的工作计划，将巡检结论落实到人。" />
    <div className="panel"><div className="toolbar"><h2 className="font-semibold">任务清单</h2><select className="ml-auto" aria-label="计划任务状态" value={filter} onChange={event => setFilter(event.target.value)}><option value="all">全部状态</option><option value="pending">待处理</option><option value="in_progress">处理中</option><option value="completed">已完成</option><option value="cancelled">已取消</option></select></div><ErrorNotice error={query.error} />{query.isPending ? <Loading /> : records.length ? <div className="table-scroll"><table><thead><tr><th>工作内容</th><th>负责人</th><th>截止时间</th><th>状态</th><th>操作</th></tr></thead><tbody>{records.map(item => <tr key={item.id}><td><strong className="table-title">{item.objective}</strong><Link to={`/inspections/${item.inspection_id}?tab=results`} className="table-meta text-brand">巡检 {item.inspection_id.slice(0, 12)} · {item.dependencies.length} 项依赖</Link><details><summary>任务要求与更新记录</summary><JsonView value={item} /></details></td><td>{item.owner_id || '未分配'}</td><td className="whitespace-nowrap">{dateTime(item.due_at)}</td><td><Badge status={item.status} /></td><td><button className="btn" disabled={['completed', 'cancelled'].includes(item.status)} onClick={() => setSelected(item)}>更新</button></td></tr>)}</tbody></table></div> : <Empty title="没有待跟进的计划任务" description="在巡检详情中确认工作计划并发布后，任务将出现在这里。" />}</div>
    {selected && <TaskUpdate task={selected} onClose={() => setSelected(null)} />}
  </>
}
