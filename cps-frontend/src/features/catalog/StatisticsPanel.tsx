import type { JsonObject } from '../../domain/cps'
import { Empty, JsonView } from '../../shared/ui'

export function StatisticsPanel({ data }: { data: JsonObject }) {
  const categories = data.category_counts && typeof data.category_counts === 'object'
    ? Object.entries(data.category_counts).filter((entry): entry is [string, number] => typeof entry[1] === 'number') : []
  const maximum = Math.max(1, ...categories.map(([, count]) => count))
  return <div className="p-6">
    <div className="grid sm:grid-cols-3 gap-6 mb-7">
      {[
        ['归档记录', data.record_count],
        ['已确认问题', data.issue_count],
        ['重复出现问题', data.repeat_occurrences],
      ].map(([label, count]) => <div key={String(label)}>
        <span className="text-xs text-muted">{String(label)}</span>
        <strong className="block text-3xl font-medium mt-3 tabular-nums">{typeof count === 'number' ? count : '—'}</strong>
      </div>)}
    </div>
    <h3 className="section-title">问题分类分布</h3>
    {categories.length ? <div className="space-y-4 mb-7">{categories.map(([name, count]) => <div key={name}>
      <div className="flex items-center justify-between gap-4 text-xs mb-2"><span>{name}</span><span className="font-mono">{count}</span></div>
      <div className="h-2 rounded-sm bg-canvas"><div className="h-2 rounded-sm bg-[#8ca583]" style={{ width: `${count / maximum * 100}%` }} /></div>
    </div>)}</div> : <Empty title="尚无问题分类数据" description="归档已确认的巡检后，后端会按统计窗口汇总分类分布。" />}
    {Array.isArray(data.limitations) && <div className="notice text-xs">{data.limitations.join(' ')}</div>}
    <details><summary>完整统计口径、区域分布与来源</summary><JsonView value={data} /></details>
  </div>
}
