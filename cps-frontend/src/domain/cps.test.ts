import { describe, expect, it } from 'vitest'
import { dateTime, hasRole, parseIdentity, versioned } from './cps'

function token(claims: unknown) {
  return `header.${btoa(JSON.stringify(claims))}.signature`
}
const claims = { sub: 'inspector', tenant_id: 'factory', roles: ['cps_admin'], type: 'access', exp: Math.floor(Date.now() / 1000) + 3600 }
describe('CPS domain helpers', () => {
  it('reads identity for display without trusting it as authorization', () => {
    expect(parseIdentity(token(claims))).toEqual({ actor: 'inspector', tenant: 'factory', roles: ['cps_admin'], expires: claims.exp })
  })
  it('rejects expired and malformed identities', () => {
    expect(() => parseIdentity('not-a-token')).toThrow('访问令牌')
    expect(() => parseIdentity(token({ ...claims, exp: 1 }))).toThrow('过期')
    expect(() => parseIdentity(token({ ...claims, roles: 'cps_admin' }))).toThrow('roles')
    expect(() => parseIdentity(token({ username: 'demo' }))).toThrow('tenant_id')
  })
  it('constructs versioned commands with independent keys', () => {
    const first = versioned(3, { reason: 'confirmed' })
    expect(first).toMatchObject({ expected_version: 3, reason: 'confirmed' })
    expect(first.idempotency_key).not.toBe(versioned(3).idempotency_key)
  })
  it('matches roles and renders absent dates honestly', () => {
    expect(hasRole(null, 'cps_admin')).toBe(false)
    expect(hasRole(parseIdentity(token(claims)), 'cps_supervisor', 'cps_admin')).toBe(true)
    expect(dateTime(null)).toBe('—')
    expect(dateTime('not a date')).toBe('—')
  })
})
