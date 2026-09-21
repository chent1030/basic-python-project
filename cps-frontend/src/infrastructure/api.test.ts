import { afterEach, describe, expect, it, vi } from 'vitest'
import { CPSApi } from './api'

afterEach(() => vi.unstubAllGlobals())
describe('CPS API adapter', () => {
  it('sends bearer auth only in headers and encodes IDs', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ id: 'case' }))
    vi.stubGlobal('fetch', fetcher)
    await new CPSApi('secret', '/api/v1/').detail('case/one')
    expect(fetcher).toHaveBeenCalledWith('/api/v1/cps/inspections/case%2Fone', expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer secret' }), cache: 'no-store' }))
    expect(fetcher.mock.calls[0][0]).not.toContain('secret')
  })
  it('loads every page rather than treating the first page as a total', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json(Array.from({ length: 200 }, (_, index) => ({ id: index })))).mockResolvedValueOnce(Response.json([{ id: 200 }]))
    vi.stubGlobal('fetch', fetcher)
    expect(await new CPSApi('secret').all('/inspections')).toHaveLength(201)
    expect(fetcher.mock.calls[1][0]).toContain('offset=200')
  })
  it.each([401, 403, 409, 422, 503])('surfaces HTTP %s without substituting mock data', async status => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ detail: 'backend reason' }, { status })))
    await expect(new CPSApi('secret').inspections()).rejects.toMatchObject({ status })
  })
  it('renders validation paths and network failures', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ detail: [{ loc: ['body', 'reason'], msg: 'Field required' }] }, { status: 422 })))
    await expect(new CPSApi('secret').post('/inspections', {})).rejects.toThrow('reason：Field required')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network')))
    await expect(new CPSApi('secret').inspections()).rejects.toThrow('无法连接')
  })
  it('does not retry writes and preserves command version and key', async () => {
    const fetcher = vi.fn().mockResolvedValue(Response.json({ detail: 'conflict' }, { status: 409 }))
    vi.stubGlobal('fetch', fetcher)
    await expect(new CPSApi('secret').post('/inspections/id/dispatch', { expected_version: 4, idempotency_key: 'same-key' })).rejects.toThrow('版本')
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({ expected_version: 4, idempotency_key: 'same-key' })
  })
  it('reads audit events after the first 200 records', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(Response.json(Array.from({ length: 200 }, (_, index) => ({ cursor: index + 1, kind: 'event' })))).mockResolvedValueOnce(Response.json([{ cursor: 201, kind: 'latest' }]))
    vi.stubGlobal('fetch', fetcher)
    const events = await new CPSApi('secret').events('case')
    expect(events).toHaveLength(201)
    expect(events.at(-1)?.kind).toBe('latest')
    expect(fetcher.mock.calls[1][0]).toContain('after=200')
  })
  it('rejects an HTML fallback from a misconfigured proxy', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('<html>SPA</html>', { headers: { 'content-type': 'text/html' } })))
    await expect(new CPSApi('secret').inspections()).rejects.toThrow('反向代理')
  })
})
