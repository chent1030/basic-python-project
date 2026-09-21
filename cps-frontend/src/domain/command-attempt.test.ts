import { expect, it } from 'vitest'
import { CommandAttempt } from './command-attempt'

it('reuses a command key when the same form is resubmitted after an uncertain response', () => {
  const attempt = new CommandAttempt()
  expect(attempt.prepare({ expected_version: 2, reason: '同意', idempotency_key: 'first' }).idempotency_key).toBe('first')
  expect(attempt.prepare({ expected_version: 2, reason: '同意', idempotency_key: 'retry' }).idempotency_key).toBe('first')
  expect(attempt.prepare({ expected_version: 2, reason: '修改原因', idempotency_key: 'changed' }).idempotency_key).toBe('changed')
  expect(attempt.prepare({ expected_version: 3, reason: '修改原因', idempotency_key: 'new-version' }).idempotency_key).toBe('new-version')
})

it('does not add fields to commands without an idempotency key', () => {
  const attempt = new CommandAttempt()
  expect(attempt.prepare(undefined)).toBeUndefined()
  expect(attempt.prepare({ action: 'accept', expected_version: 1 })).toEqual({ action: 'accept', expected_version: 1 })
})
