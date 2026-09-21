import { useEffect, useRef, type ReactNode } from 'react'
import { ArrowRight, CircleNotch, WarningCircle, X } from '@phosphor-icons/react'
import { Link } from 'react-router-dom'
import { statusLabels } from '../domain/cps'

export function Badge({ status }: { status: string }) {
  const tone = ['completed', 'accepted', 'succeeded'].includes(status) ? 'green'
    : ['failed', 'needs_operator_recovery', 'cancelled', 'rejected'].includes(status) ? 'red'
    : ['proposed', 'pending', 'needs_input', 'needs_human', 'waiting_dispatch_confirmation', 'waiting_result_confirmation', 'waiting_approval'].includes(status) ? 'amber' : 'neutral'
  return <span className={`badge badge-${tone}`}><span className="size-1.5 rounded-full bg-current" />{statusLabels[status] || status}</span>
}
export function ErrorNotice({ error }: { error: unknown }) {
  if (!error) return null
  return <div role="alert" className="notice notice-error"><WarningCircle size={19} className="shrink-0" /><span>{error instanceof Error ? error.message : typeof error === 'object' ? JSON.stringify(error) : String(error)}</span></div>
}
export function Empty({ title, description, children }: { title: string; description: string; children?: ReactNode }) {
  return <div className="empty"><div className="empty-mark"><span /><span /><span /></div><h3>{title}</h3><p>{description}</p>{children}</div>
}
export function Loading() { return <div role="status" className="flex items-center justify-center gap-2 py-14 text-muted"><CircleNotch size={20} className="animate-spin" />正在读取工作区…</div> }
export function Heading({ eyebrow, title, description, children }: { eyebrow: string; title: string; description: string; children?: ReactNode }) {
  return <header className="page-heading"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p className="text-muted mt-2 text-sm">{description}</p></div><div className="flex flex-wrap gap-2">{children}</div></header>
}
export function Panel({ title, extra, children, className = '' }: { title: string; extra?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`panel ${className}`}><div className="panel-heading"><h2>{title}</h2>{extra}</div>{children}</section>
}
export function TextLink({ to, children }: { to: string; children: ReactNode }) { return <Link to={to} className="text-link">{children}<ArrowRight size={14} /></Link> }
export function Modal({ title, children, onClose, wide = false }: { title: string; children: ReactNode; onClose: () => void; wide?: boolean }) {
  const dialog = useRef<HTMLDialogElement>(null)
  useEffect(() => { const element = dialog.current!; element.showModal(); return () => element.close() }, [])
  return <dialog ref={dialog} className={`modal ${wide ? 'modal-wide' : ''}`} onCancel={event => { event.preventDefault(); onClose() }}>
    <div className="modal-heading"><h2>{title}</h2><button className="icon-button" aria-label="关闭弹窗" onClick={onClose}><X size={20} /></button></div>
    <div className="p-6">{children}</div>
  </dialog>
}
export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) { return <label className="field"><span>{label}</span>{children}{hint && <small>{hint}</small>}</label> }
export function JsonView({ value }: { value: unknown }) { return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre> }
export function SubmitButton({ pending, children }: { pending: boolean; children: ReactNode }) { return <button type="submit" className="btn btn-primary" disabled={pending}>{pending && <CircleNotch size={16} className="animate-spin" />}{pending ? '正在提交…' : children}</button> }
