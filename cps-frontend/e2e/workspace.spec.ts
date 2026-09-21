import { expect, test, type Page } from '@playwright/test'
import type { Agent, InspectionDetail, JsonObject, MemoryRecord, WorkTask } from '../src/domain/cps'

const token = `header.${Buffer.from(JSON.stringify({ type: 'access', sub: 'inspection-admin', tenant_id: 'assembly-workshop', roles: ['cps_admin'], exp: Math.floor(Date.now() / 1000) + 3600 })).toString('base64url')}.test-signature`
const now = Date.now() / 1000
const proposal = { action: 'propose_agent', agent_name: 'issue_identification', reason: '现场证据已经齐备，应先确认设备防护与作业通道问题，再生成报告。', expected_output: '问题清单与证据引用', prerequisites: ['已上传整改前图片'], missing_inputs: [], source_refs: ['inspection'], requires_human_confirmation: true }
function inspection(id = 'inspection-001', goal = '总装一线 · 设备安全与现场规范巡检'): InspectionDetail {
  return { id, goal, owner: 'inspection-admin', line_info: { line_id: 'LINE-01', area: '总装车间', supervisor_id: 'supervisor-01', modifications: '新增物料周转工位', expected_scenarios: ['设备防护', '作业通道', '物料规范'] }, required_outputs: ['issues', 'report', 'work_plan'],
    status: 'active', version: 3, evidence_version: 2, evidence: [{ id: 'evidence-1', kind: 'before', note: '总装一线现场照片' }], results: {}, confirmations: {}, history: [], notes: [], jobs: ['job-main'], active_job: 'job-main', rounds: 1, archived_report: null, published_plan: null, created_at: now - 3600, updated_at: now - 120,
    phase: 'waiting_dispatch_confirmation', can_finish: false, outbox_error: null, approvals: [{ id: 'approval-main', kind: 'after', payload: proposal, roles: ['cps_admin'], version: 1 }], active_run: { id: 'run-main', workflow: 'cps_main', status: 'waiting_approval', tokens_used: 1240, error_type: null, created: now - 180 },
  }
}
const agentNames = ['main', 'issue_identification', 'rectification_judgement', 'history_analysis', 'coverage_analysis', 'report', 'work_plan', 'observation']
const agents: Agent[] = agentNames.map(name => ({ name, version: '2.0.0', dispatchable: !['main', 'observation'].includes(name), output_schema: { type: 'object', required: ['status', 'summary'] }, review_roles: ['cps_admin'] }))
async function backend(page: Page, options: { collecting?: boolean; result?: boolean; conflict?: boolean } = {}) {
  const first = inspection()
  if (options.collecting) Object.assign(first, { status: 'collecting', phase: 'collecting', active_job: null, active_run: null, approvals: [], evidence: [] })
  if (options.result) Object.assign(first, { phase: 'waiting_result_confirmation', active_run: { ...first.active_run, workflow: 'cps_report' }, approvals: [{ id: 'report-approval', kind: 'after', roles: ['cps_admin'], version: 1, payload: { status: 'completed', summary: '待审核报告', report: { title: '现场巡检报告', summary: '待人工确认' } } }] })
  const cases = [first, ...['焊装区域 · 工位防护复查', '仓储二线 · 整改效果检查', '包装产线 · 月度覆盖度巡检', '总装二线 · 通道规范检查'].map((goal, index) => ({ ...inspection(`inspection-00${index + 2}`, goal), status: (['active', 'needs_input', 'completed', 'collecting'] as const)[index], active_job: null, approvals: [], active_run: null }))]
  const memory: MemoryRecord = { id: 'memory-1', version: 1, state: 'proposed', content: { knowledge: '产线设备调整后，先执行覆盖度分析，再安排问题识别。', scope: 'LINE-01', condition: '设备布局发生变化', source_refs: ['inspection'], limitations: ['仅适用于已确认现场变化的任务'] } }
  const task: WorkTask = { id: 'task-1', inspection_id: first.id, objective: '复核设备防护罩并补充整改证据', scope: 'LINE-01', priority: 'high', acceptance_criteria: ['防护罩安装完成'], owner_id: 'employee-01', due_at: '2026-09-15T09:00:00+08:00', dependencies: [], status: 'pending', version: 1, updates: [] }
  const writes: { path: string; body: JsonObject }[] = []
  await page.route('**/api/v1/cps/**', async route => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname.replace('/api/v1/cps', '')
    const reply = (value: unknown, status = 200) => route.fulfill({ status, json: value })
    if (!request.headers().authorization) return reply({ detail: 'Unauthorized' }, 401)
    if (request.method() !== 'GET') {
      const body = request.postDataJSON() as JsonObject
      writes.push({ path, body })
      if (options.conflict) return reply({ detail: 'Version changed' }, 409)
      if (path === '/inspections') {
        const created = { ...inspection('created-case', String(body.goal)), status: 'collecting' as const, phase: 'collecting', active_job: null, active_run: null, approvals: [], evidence: [], version: 1 }
        cases.unshift(created)
        return reply(created, 201)
      }
      if (path.endsWith('/dispatch') || path.endsWith('/results/review')) { first.approvals = []; first.phase = 'running'; first.version += 1 }
      if (path.endsWith('/evidence')) { first.evidence.push({ id: 'uploaded', kind: 'before', note: String(body.note) }); first.version += 1 }
      if (path === '/memories/memory-1/review') { memory.state = body.action === 'accept' ? 'accepted' : 'disabled'; memory.version += 1 }
      if (path === '/tasks/task-1') { task.status = String(body.status); task.version += 1 }
      return reply(first)
    }
    if (path === '/inspections') return reply(cases.slice(Number(url.searchParams.get('offset') || 0), Number(url.searchParams.get('offset') || 0) + Number(url.searchParams.get('limit') || 200)))
    if (path === '/agents') return reply(agents)
    if (path === '/memories') return reply([memory])
    if (path.endsWith('/versions')) return reply([memory])
    if (path === '/tasks') return reply([task])
    if (path === '/history') return reply([])
    if (path === '/statistics' || path === '/metrics') return reply({ direct_approval_rate: null, limitations: ['当前没有足够样本。'] })
    if (path.endsWith('/events')) return reply([{ cursor: 1, kind: 'cps.inspection_created', payload: { actor: 'inspection-admin' } }, { cursor: 2, kind: 'approval.created', payload: { agent: 'main' } }])
    if (path.endsWith('/report.html')) return route.fulfill({ contentType: 'text/html', body: '<h1>现场巡检报告</h1><p>待人工确认</p><script>window.parent.__unsafe=true</script>' })
    if (path.startsWith('/inspections/')) return reply(cases.find(item => item.id === path.split('/')[2]) ?? first)
    return reply({ detail: 'unhandled route' }, 404)
  })
  return { writes, first }
}
async function connect(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: '连接工作空间', exact: true }).click()
  await page.getByLabel('访问令牌', { exact: false }).fill(token)
  await page.getByRole('button', { name: '验证并连接' }).click()
  await expect(page.getByRole('heading', { name: '巡检工作台', exact: true })).toBeVisible()
}
test('disconnected page and mobile navigation are usable without invented data', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.getByRole('heading', { name: /每一次巡检/ })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
  await page.getByRole('button', { name: '展开导航' }).click()
  await expect(page.getByRole('link', { name: '巡检任务', exact: true })).toBeVisible()
  await page.getByRole('link', { name: '巡检任务', exact: true }).click()
  await expect(page.getByRole('button', { name: '连接工作空间', exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/mobile-welcome.png', fullPage: true })
})
test('dashboard reads API records, filters tasks, and clears credentials on disconnect', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1050 })
  await backend(page)
  await connect(page)
  await expect(page.getByRole('link', { name: '总装一线 · 设备安全与现场规范巡检', exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/dashboard-fixture.png', fullPage: true })
  await page.getByRole('link', { name: '查看全部', exact: true }).click()
  await page.getByLabel('搜索巡检').fill('焊装')
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await page.getByRole('button', { name: '后端已连接' }).click()
  await page.getByRole('button', { name: '断开并清除会话' }).click()
  await expect(page.getByRole('button', { name: '连接工作空间', exact: true })).toBeVisible()
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0])
})
test('creates an inspection with the actual backend contract', async ({ page }) => {
  const { writes } = await backend(page)
  await connect(page)
  await page.getByRole('button', { name: '新建巡检', exact: true }).click()
  await page.locator('#quick-photo').setInputFiles({ name: '问题照片.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ioAAAAASUVORK5CYII=', 'base64') })
  await page.getByLabel('现场问题描述').fill('包装区设备防护罩未关闭')
  await page.getByRole('button', { name: /下一步：填写位置/ }).click()
  await page.getByLabel('问题标题').fill('包装区设备安全巡检')
  await page.getByRole('textbox', { name: '区域', exact: true }).fill('包装车间')
  await page.getByLabel('产线 / 工位').fill('PACK-02')
  await page.getByLabel('负责人 ID').fill('supervisor-02')
  await page.getByRole('button', { name: /下一步：确认提交/ }).click()
  await page.getByRole('button', { name: '提交并派发', exact: true }).click()
  await page.getByRole('button', { name: '查看处理进度', exact: true }).click()
  await expect(page).toHaveURL(/inspections\/created-case/)
  expect(writes[0].body).toMatchObject({ goal: '包装区设备安全巡检\n现场描述：包装区设备防护罩未关闭', line_info: { line_id: 'PACK-02', area: '包装车间', supervisor_id: 'supervisor-02' }, required_outputs: ['issues'] })
  expect(writes[0].body.idempotency_key).toBeTruthy()
})
test('human can change the next agent, with reason and optimistic version', async ({ page }) => {
  const { writes } = await backend(page)
  await connect(page)
  await page.getByRole('link', { name: '人工确认', exact: true }).click()
  await page.getByRole('link', { name: /查看并确认/ }).click()
  await page.getByRole('button', { name: '审核调度建议' }).click()
  await page.getByLabel('调度决定').selectOption('modify')
  await page.getByLabel('下一步执行 Agent').selectOption('coverage_analysis')
  await page.getByLabel('操作理由（必填）').fill('现场布局变化，先确认覆盖范围再分析问题。')
  await page.getByLabel('补充执行指令').fill('重点核对新增工位')
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(writes.at(-1)).toMatchObject({ path: '/inspections/inspection-001/dispatch', body: { action: 'modify', selected_agent: 'coverage_analysis', expected_version: 3, reason: '现场布局变化，先确认覆盖范围再分析问题。', instruction: '重点核对新增工位' } })
})
test('conflict is shown without silently approving a newer version', async ({ page }) => {
  const { writes } = await backend(page, { conflict: true })
  await connect(page)
  await page.getByRole('link', { name: '总装一线 · 设备安全与现场规范巡检', exact: true }).click()
  await page.getByRole('button', { name: '人工确认 (1)' }).click()
  await page.getByRole('button', { name: '审核调度建议' }).click()
  await page.getByLabel('操作理由（必填）').fill('同意本次调度')
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('alert')).toContainText('版本已变化')
  await expect(page.getByRole('dialog')).toBeVisible()
  expect(writes).toHaveLength(1)
})
test('report review stays independent and sandbox preview cannot execute scripts', async ({ page }) => {
  const { writes } = await backend(page, { result: true })
  await connect(page)
  await page.getByRole('link', { name: '总装一线 · 设备安全与现场规范巡检', exact: true }).click()
  await page.getByRole('button', { name: '报告与成果', exact: true }).click()
  await page.getByRole('button', { name: '预览报告' }).click()
  await expect(page.frameLocator('iframe').getByRole('heading', { name: '现场巡检报告' })).toBeVisible()
  expect(await page.evaluate(() => '__unsafe' in window)).toBe(false)
  await page.getByRole('button', { name: '关闭弹窗' }).click()
  await page.getByRole('button', { name: '人工确认 (1)' }).click()
  await page.getByRole('button', { name: '审核分析结果' }).click()
  await page.getByLabel('操作理由（必填）').fill('已核对现场证据和报告结论')
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(writes.at(-1)).toMatchObject({ path: '/inspections/inspection-001/results/review', body: { accept: true, edited: null, expected_version: 3 } })
})
test('uploads actual base64 evidence and does not send filesystem paths', async ({ page }) => {
  const { writes } = await backend(page, { collecting: true })
  await connect(page)
  await page.getByRole('link', { name: '总装一线 · 设备安全与现场规范巡检', exact: true }).click()
  await page.getByRole('button', { name: '上传证据' }).click()
  const content = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j1ioAAAAASUVORK5CYII='
  await page.locator('input[type="file"]').setInputFiles({ name: '现场照片.png', mimeType: 'image/png', buffer: Buffer.from(content, 'base64') })
  await page.getByLabel('证据说明').fill('设备防护检查')
  await page.getByRole('button', { name: '确认提交' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(writes.at(-1)?.body).toMatchObject({ content_base64: content, kind: 'before', expected_version: 3, note: '设备防护检查', issue_id: null })
})
test('reviews proposed memory and updates published work tasks', async ({ page }) => {
  const { writes } = await backend(page)
  await connect(page)
  await page.getByRole('link', { name: '经验记忆', exact: true }).click()
  await page.getByLabel('记忆状态', { exact: true }).selectOption('proposed')
  await page.getByRole('button', { name: '审核 / 管理' }).click()
  await page.getByLabel('审核理由').fill('已复核该规则的适用条件与来源')
  await page.getByRole('button', { name: '保存审核' }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(writes.at(-1)?.body).toMatchObject({ action: 'accept', expected_version: 1, scope: 'LINE-01' })
  await page.getByRole('link', { name: '计划任务', exact: true }).click()
  await expect(page.getByText('复核设备防护罩并补充整改证据', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '更新', exact: true }).click()
  await page.getByLabel('进展说明').fill('已安排现场人员开始处理')
  await page.getByRole('button', { name: '更新状态', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
  expect(writes.at(-1)).toMatchObject({ path: '/tasks/task-1', body: { status: 'in_progress', expected_version: 1 } })
})
