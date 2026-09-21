import { useState, type ChangeEvent, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, ArrowUpRight, Camera, CheckCircle, Funnel, MagnifyingGlass, Plus, UploadSimple } from '@phosphor-icons/react'
import { useCommand, useInspections } from '../../application/queries'
import { useSession } from '../../application/session'
import { dateTime, statusLabels, type Inspection, type JsonObject } from '../../domain/cps'
import { Badge, Empty, ErrorNotice, Field, Heading, Loading, Modal, SubmitButton } from '../../shared/ui'

export function InspectionTable({ cases }: { cases: Inspection[] }) {
  return <div className="table-scroll"><table><thead><tr><th>巡检任务</th><th>所属区域 / 产线</th><th>当前状态</th><th>更新时间</th><th><span className="sr-only">查看详情</span></th></tr></thead>
    <tbody>{cases.map(item => <tr key={item.id}><td><Link className="table-title" to={`/inspections/${item.id}`}>{item.goal}</Link><span className="table-meta">{item.id.slice(0, 12).toUpperCase()} · {item.evidence.length} 份证据</span></td><td>{item.line_info.area}<span className="table-meta">{item.line_info.line_id}</span></td><td><Badge status={item.status} /></td><td className="whitespace-nowrap text-muted">{dateTime(item.updated_at)}</td><td><Link className="icon-button" to={`/inspections/${item.id}`} aria-label={`查看${item.goal}`}><ArrowUpRight size={18} /></Link></td></tr>)}</tbody>
  </table></div>
}
function encodeImage(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = () => reject(new Error('照片读取失败，请重新选择。'))
    reader.readAsDataURL(file)
  })
}

export function QuickReport({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate()
  const { identity } = useSession()
  const [step, setStep] = useState(1)
  const [files, setFiles] = useState<File[]>([])
  const [error, setError] = useState<unknown>(null)
  const [submitting, setSubmitting] = useState(false)
  const [completed, setCompleted] = useState(false)
  const [createdId, setCreatedId] = useState('')
  const [description, setDescription] = useState('')
  const [goal, setGoal] = useState('')
  const [area, setArea] = useState('')
  const [lineId, setLineId] = useState('')
  const [supervisorId, setSupervisorId] = useState('')
  const create = useCommand<JsonObject>((client, body) => client.post('/inspections', body))
  const upload = useCommand<JsonObject>((client, body) => client.post(`/inspections/${String(body.case_id)}/evidence`, body))
  const start = useCommand<JsonObject>((client, body) => client.post(`/inspections/${String(body.case_id)}/start`, body))
  const chooseFiles = (event: ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files || [])
    const invalid = selected.find(file => !['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 10_000_000)
    if (invalid) { setError(new Error('请上传 JPEG、PNG 或 WebP 图片，单张不超过 10 MB。')); return }
    setError(null)
    setFiles(current => [...current, ...selected].slice(0, 5))
    event.target.value = ''
  }
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setError(null); setSubmitting(true)
    try {
      const result = await create.mutateAsync({ goal: `${goal}\n现场描述：${description}`, idempotency_key: crypto.randomUUID(), line_info: {
        line_id: lineId.trim(), area: area.trim(), supervisor_id: supervisorId.trim() || identity?.actor || '未指定', modifications: '', expected_scenarios: [],
      }, required_outputs: ['issues'], }) as Inspection
      setCreatedId(result.id)
      let version = result.version
      for (const [index, file] of files.entries()) {
        const evidence = await upload.mutateAsync({ case_id: result.id, expected_version: version, idempotency_key: crypto.randomUUID(), kind: 'before', issue_id: null, note: index === 0 ? description : `现场补充照片 ${index + 1}`, content_base64: await encodeImage(file) }) as Inspection
        version = evidence.version
      }
      await start.mutateAsync({ case_id: result.id, expected_version: version, idempotency_key: crypto.randomUUID() })
      setCompleted(true)
    } catch (failure) { setError(failure) } finally { setSubmitting(false) }
  }
  return <Modal title="现场问题上报" onClose={onClose} wide>{completed ? <div className="success-screen"><CheckCircle size={52} weight="duotone" /><h2>问题已提交</h2><p>照片和现场描述已经提交，系统正在安排下一步处理。</p><p className="text-xs text-muted">后续由负责人或审核人员在待办中处理，员工不需要选择 Agent。</p><button className="btn btn-primary" onClick={() => navigate(`/inspections/${createdId}`)}>查看处理进度<ArrowRight size={17} /></button></div> : <form className="form-stack" onSubmit={submit}>
    <div className="wizard-progress"><span className={step >= 1 ? 'active' : ''}>1 <b>拍照描述</b></span><i /><span className={step >= 2 ? 'active' : ''}>2 <b>填写位置</b></span><i /><span className={step >= 3 ? 'active' : ''}>3 <b>提交派发</b></span></div>
    {step === 1 && <><div className="quick-intro"><Camera size={30} weight="duotone" /><div><h3>先拍下现场问题</h3><p>最多 5 张照片，第一张用于智能识别。照片越清楚，后续判断越准确。</p></div></div><div className="quick-upload"><input id="quick-photo" type="file" accept="image/jpeg,image/png,image/webp" multiple onChange={chooseFiles} /><label htmlFor="quick-photo"><UploadSimple size={28} /><strong>{files.length ? '继续添加照片' : '选择或拍摄照片'}</strong><small>{files.length}/5 张 · 单张不超过 10 MB</small></label>{files.length > 0 && <div className="quick-previews">{files.map((file, index) => <div key={`${file.name}-${index}`}><img src={URL.createObjectURL(file)} alt={`现场照片 ${index + 1}`} /><button type="button" aria-label={`删除照片 ${index + 1}`} onClick={() => setFiles(current => current.filter((_, item) => item !== index))}>×</button></div>)}</div>}</div><Field label="现场问题描述"><textarea name="description" required value={description} onChange={event => setDescription(event.target.value)} maxLength={5000} rows={5} placeholder="例如：发现 1 号工位防护罩未关闭，现场存在安全风险" /></Field><div className="form-footer"><button type="button" className="btn" onClick={onClose}>取消</button><button type="button" className="btn btn-primary" disabled={!files.length || !description.trim()} onClick={() => setStep(2)}>下一步：填写位置<ArrowRight size={16} /></button></div></>}
    {step === 2 && <><div className="quick-intro"><CheckCircle size={30} weight="duotone" /><div><h3>告诉我们问题在哪里</h3><p>填写后系统会自动匹配后续处理人员，员工不需要手动派 Agent。</p></div></div><Field label="问题标题"><input name="goal" required value={goal} onChange={event => setGoal(event.target.value)} maxLength={300} placeholder="例如：1 号工位防护罩未关闭" /></Field><div className="grid grid-cols-2 gap-4"><Field label="区域"><input name="area" required value={area} onChange={event => setArea(event.target.value)} maxLength={100} placeholder="总装车间" /></Field><Field label="产线 / 工位"><input name="line_id" required value={lineId} onChange={event => setLineId(event.target.value)} maxLength={100} placeholder="LINE-01 / 工位 1" /></Field></div><Field label="负责人 ID" hint="不知道可以填写现场主管工号，管理员可在后续调整。"><input name="supervisor_id" value={supervisorId} onChange={event => setSupervisorId(event.target.value)} maxLength={100} placeholder={identity?.actor || '选填'} /></Field><div className="form-footer"><button type="button" className="btn" onClick={() => setStep(1)}><ArrowLeft size={16} />返回修改</button><button type="button" className="btn btn-primary" disabled={!goal.trim() || !area.trim() || !lineId.trim()} onClick={() => setStep(3)}>下一步：确认提交<ArrowRight size={16} /></button></div></>}
    {step === 3 && <><div className="quick-summary"><div><span>现场照片</span><strong>{files.length} 张</strong></div><div><span>问题描述</span><strong>已填写</strong></div><div><span>位置</span><strong>已填写</strong></div></div><div className="notice"><CheckCircle size={20} /><p>提交后会自动创建巡检任务、上传照片并启动处理流程。员工不需要理解 Agent、模型或审批版本。</p></div><div className="form-footer"><button type="button" className="btn" onClick={() => setStep(2)}><ArrowLeft size={16} />返回修改</button><SubmitButton pending={submitting}>提交并派发</SubmitButton></div></>}
    <ErrorNotice error={error} />
  </form>}</Modal>
}
export const NewInspection = QuickReport
export function Inspections() {
  const query = useInspections()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const [creating, setCreating] = useState(false)
  const cases = (query.data ?? []).filter(item => (status === 'all' || item.status === status) && `${item.goal} ${item.id} ${item.line_info.line_id} ${item.line_info.area}`.toLowerCase().includes(search.toLowerCase()))
  return <><Heading eyebrow="INSPECTIONS" title="巡检任务" description="拍照、描述、提交，后续处理由系统自动流转。"><button className="btn btn-primary" onClick={() => setCreating(true)}><Plus size={17} />上报问题</button></Heading>
    <div className="panel"><div className="toolbar"><label className="search-input"><MagnifyingGlass size={18} /><input aria-label="搜索巡检" placeholder="搜索任务、区域或产线…" value={search} onChange={event => setSearch(event.target.value)} /></label><label className="filter-input"><Funnel size={16} /><select aria-label="筛选巡检状态" value={status} onChange={event => setStatus(event.target.value)}><option value="all">全部状态</option>{['collecting', 'active', 'needs_input', 'needs_human', 'completed', 'cancelled'].map(value => <option key={value} value={value}>{statusLabels[value]}</option>)}</select></label><span className="text-xs text-muted ml-auto">{cases.length} 个任务</span></div>
      <ErrorNotice error={query.error} />{query.isPending ? <Loading /> : cases.length ? <InspectionTable cases={cases} /> : <Empty title={search || status !== 'all' ? '没有匹配的任务' : '还没有巡检任务'} description="创建任务并上传现场证据，开始第一轮巡检。" />}
    </div>{creating && <NewInspection onClose={() => setCreating(false)} />}</>
}
