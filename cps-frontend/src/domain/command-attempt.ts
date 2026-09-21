export class CommandAttempt {
  private previous: { fingerprint: string; key: unknown } | null = null

  prepare<T>(payload: T): T {
    if (!payload || typeof payload !== 'object' || !('idempotency_key' in payload)) return payload
    const fingerprint = JSON.stringify({ ...payload, idempotency_key: undefined })
    if (this.previous?.fingerprint !== fingerprint) {
      this.previous = { fingerprint, key: payload.idempotency_key }
    }
    return { ...payload, idempotency_key: this.previous.key }
  }
}
