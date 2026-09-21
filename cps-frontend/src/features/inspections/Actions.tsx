import { useState } from 'react'
import { useCommand, useResource } from '../../application/queries'
import { agentLabels, objectText, versioned, type Agent, type InspectionDetail, type JsonObject } from '../../domain/cps'
import { ErrorNotice, Field, JsonView, Modal, SubmitButton } from '../../shared/ui'

export type CaseAction = 'start' | 'dispatch' | 'review' | 'manual' | 'evidence' | 'amend' | 'stop' | 'archive' | 'publish'
const titles: Record<CaseAction, string> = { start: '启动巡检', dispatch: '确认下一步调度', review: '审核 Agent 结果', manual: '提交人工结果', evidence: '上传现场证据', amend: '补充任务信息', stop: '退回或取消巡检', archive: '确认归档报告', publish: '发布工作计划' }
const endpoints: Record<CaseAction, string> = { start: '/start', dispatch: '/dispatch', review: '/results/review', manual: '/results/manual', evidence: '/evidence', amend: '', stop: '/stop', archive: '/archive', publish: '/work-plan/publish' }

function parseOutput(value: FormDataEntryValue | null) {
  try {
    const result = JSON.parse(String(value))
    if (!result || Array.isArray(result) || typeof result !== 'object') throw new Error()
    return result as JsonObject
  } catch { throw new Error('结果必须是有效的 JSON 对象，并符合当前 Agent 的输出契约。') }
}
function fileBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = () => reject(new Error('无法读取图片，请重新选择。'))
    reader.readAsDataURL(file)
  })
}
export function CaseActionModal({ kind, inspection, onClose }: { kind: CaseAction; inspection: InspectionDetail; onClose: () => void }) {
  const [snapshot] = useState(inspection)
  const [error, setError] = useState<unknown>(null)
  const [choice, setChoice] = useState(kind === 'stop' ? 'return' : 'approve')
  const [agent, setAgent] = useState('issue_identification')
  const [editing, setEditing] = useState(false)
  const [evidenceKind, setEvidenceKind] = useState('before')
  const [reading, setReading] = useState(false)
  const agents = useResource('agents', (api, signal) => api.request<Agent[]>('/agents', { signal }), ['manual', 'dispatch'].includes(kind), false)
  const approval = snapshot.approvals[0]
  const issues = Array.isArray(snapshot.results.issues) ? snapshot.results.issues as JsonObject[] : []
  const command = useCommand<JsonObject>((api, body) => kind === 'amend'
    ? api.patch(`/inspections/${encodeURIComponent(snapshot.id)}`, body)
    : api.post(`/inspections/${encodeURIComponent(snapshot.id)}${endpoints[kind]}`, body))
  return <Modal title={titles[kind]} onClose={onClose} wide={['dispatch', 'review', 'manual'].includes(kind)}><form className="form-stack" onSubmit={async event => {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    setError(null)
    setReading(true)
    try {
      let body: JsonObject = versioned(snapshot.version)
      if (kind === 'dispatch') body = versioned(snapshot.version, { action: choice, reason: data.get('reason'), instruction: data.get('instruction'), ...(choice === 'modify' ? { selected_agent: data.get('selected_agent') } : {}) })
      if (kind === 'review') body = versioned(snapshot.version, { accept: choice !== 'reject', reason: data.get('reason'), edited: editing && choice !== 'reject' ? parseOutput(data.get('output')) : null })
      if (kind === 'manual') body = versioned(snapshot.version, { agent, reason: data.get('reason'), output: parseOutput(data.get('output')) })
      if (kind === 'stop') body = versioned(snapshot.version, { action: choice, reason: data.get('reason') })
      if (kind === 'evidence') {
        const file = data.get('file') as File
        if (!file?.size || file.size > 10_000_000) throw new Error('请选择不超过 10 MB 的图片。')
        if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) throw new Error('仅支持 JPEG、PNG、WebP 图片。')
        body = versioned(snapshot.version, { kind: evidenceKind, issue_id: data.get('issue_id') || null, note: data.get('note'), content_base64: await fileBase64(file) })
      }
      if (kind === 'amend') body = { expected_version: snapshot.version, goal: data.get('goal'), note: data.get('note'), line_info: {
        line_id: data.get('line_id'), area: data.get('area'), supervisor_id: data.get('supervisor_id'), modifications: data.get('modifications'),
        expected_scenarios: String(data.get('scenarios')).split('\n').map(value => value.trim()).filter(Boolean),
      } }
      await command.mutateAsync(body)
      onClose()
    } catch (failure) { setError(failure) } finally { setReading(false) }
  }}>
    <div className="notice text-xs">任务版本 v{snapshot.version} · 操作会被审计记录。若版本变化，请关闭窗口、刷新后重新核对。</div>
    {kind === 'dispatch' && <>
      <div className="proposal-summary"><span className="eyebrow">主 AGENT 建议</span><h3>{agentLabels[String(approval?.payload.agent_name)] || '处理当前巡检状态'}</h3><p>{objectText(approval?.payload.reason)}</p><p className="mt-2 text-muted">预期产出：{objectText(approval?.payload.expected_output)}</p></div>
      <details><summary>查看完整建议、前置条件与来源</summary><JsonView value={approval?.payload} /></details>
      <Field label="调度决定"><select value={choice} onChange={event => setChoice(event.target.value)}>{[['approve', '批准当前建议'], ['modify', '改派其他 Agent'], ['skip', '跳过本步'], ['retry', '重试建议 Agent'], ['return', '退回补充信息'], ['manual', '转人工处理']].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
      {choice === 'modify' && <><ErrorNotice error={agents.error} /><Field label="下一步执行 Agent"><select name="selected_agent" required disabled={agents.isPending || Boolean(agents.error)} defaultValue=""><option value="" disabled>请选择专家 Agent</option>{agents.data?.filter(item => item.dispatchable).map(item => <option key={item.name} value={item.name}>{agentLabels[item.name] || item.name}</option>)}</select></Field></>}
      <Field label="补充执行指令"><textarea name="instruction" maxLength={10000} rows={2} placeholder="选填：这一步需要重点关注什么" /></Field>
    </>}
    {kind === 'review' && <>
      <JsonView value={approval?.payload} />
      <Field label="审核决定"><select value={choice} onChange={event => setChoice(event.target.value)}><option value="approve">确认结果</option><option value="reject">拒绝结果</option></select></Field>
      {choice !== 'reject' && <label className="check-label"><input type="checkbox" checked={editing} onChange={event => setEditing(event.target.checked)} />修改结构化结果后确认</label>}
      {editing && choice !== 'reject' && <Field label="完整结果 JSON" hint="须保持 Agent 输出契约；后端将再次校验数据、证据引用与统计口径。"><textarea className="font-mono text-xs" name="output" rows={16} defaultValue={JSON.stringify(approval?.payload, null, 2)} required /></Field>}
    </>}
    {kind === 'manual' && <>
      <ErrorNotice error={agents.error} />
      <Field label="人工结果对应的 Agent"><select value={agent} onChange={event => setAgent(event.target.value)}>{Object.entries(agentLabels).filter(([name]) => !['main', 'observation'].includes(name)).map(([name, label]) => <option value={name} key={name}>{label}</option>)}</select></Field>
      <details><summary>查看该 Agent 的完整输出契约</summary><JsonView value={agents.data?.find(item => item.name === agent)?.output_schema ?? { message: '当前角色无法读取契约时，请参考后端领域契约。' }} /></details>
      <Field label="人工结果 JSON"><textarea name="output" required rows={12} className="font-mono text-xs" placeholder="输入符合输出契约的完整 JSON 对象" /></Field>
    </>}
    {['dispatch', 'review', 'manual', 'stop'].includes(kind) && <Field label="操作理由（必填）" hint={kind === 'dispatch' ? '改派理由会保留在审计链路中，由观察员提取为待审长期记忆候选。' : undefined}><textarea name="reason" required maxLength={5000} rows={3} placeholder="说明判断依据，便于追踪与后续复盘" /></Field>}
    {kind === 'stop' && <Field label="操作类型"><select value={choice} onChange={event => setChoice(event.target.value)}><option value="return">退回补充信息</option><option value="cancel">取消巡检（不可继续执行）</option></select></Field>}
    {kind === 'evidence' && <>
      <Field label="证据类型"><select value={evidenceKind} onChange={event => setEvidenceKind(event.target.value)}><option value="before">整改前 / 现场问题</option><option value="after">整改后</option><option value="context">环境上下文</option></select></Field>
      {evidenceKind === 'after' && <Field label="关联已确认问题"><select name="issue_id" required defaultValue=""><option disabled value="">请选择问题</option>{issues.map(issue => <option key={String(issue.issue_id)} value={String(issue.issue_id)}>{String(issue.issue_id)} · {String(issue.description)}</option>)}</select></Field>}
      <Field label="现场图片" hint="JPEG、PNG 或 WebP，单张不超过 10 MB；后端会验证实际图片内容。"><input type="file" name="file" accept="image/jpeg,image/png,image/webp" required /></Field>
      <Field label="证据说明"><textarea name="note" maxLength={5000} rows={3} placeholder="拍摄位置、时间和需要关注的细节" /></Field>
    </>}
    {kind === 'amend' && <>
      <Field label="巡检目标"><textarea name="goal" required maxLength={10000} defaultValue={snapshot.goal} rows={3} /></Field>
      <div className="grid grid-cols-2 gap-4"><Field label="所属区域"><input name="area" required maxLength={100} defaultValue={snapshot.line_info.area} /></Field><Field label="产线编号"><input name="line_id" required maxLength={100} defaultValue={snapshot.line_info.line_id} /></Field></div>
      <Field label="负责人 ID"><input name="supervisor_id" required maxLength={100} defaultValue={snapshot.line_info.supervisor_id} /></Field>
      <Field label="现场变化"><textarea name="modifications" maxLength={20000} rows={2} defaultValue={snapshot.line_info.modifications} /></Field>
      <Field label="覆盖场景（每行一个）"><textarea name="scenarios" rows={2} defaultValue={snapshot.line_info.expected_scenarios.join('\n')} /></Field>
      <Field label="修改说明"><textarea name="note" maxLength={20000} rows={2} /></Field>
    </>}
    {kind === 'start' && <p className="text-sm leading-7">启动后，主 Agent 将根据目标和证据提出下一步建议，不会自动越过人工确认。请确认现场资料已上传完整。</p>}
    {kind === 'archive' && <p className="text-sm leading-7">将已人工确认的报告生成归档记录。归档后会触发旁路观察，提取待审核的经验记忆。</p>}
    {kind === 'publish' && <p className="text-sm leading-7">将已确认的工作计划发布为实际任务，包含负责人、依赖关系与截止时间。请在发布前核对计划内容。</p>}
    <ErrorNotice error={error} /><div className="form-footer"><button type="button" className="btn" disabled={command.isPending || reading} onClick={onClose}>取消</button><SubmitButton pending={command.isPending || reading}>确认提交</SubmitButton></div>
  </form></Modal>
}
