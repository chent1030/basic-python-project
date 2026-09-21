import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { CPSApi } from '../infrastructure/api'
import { parseIdentity, type Identity } from '../domain/cps'

interface Session {
  api: CPSApi | null
  identity: Identity | null
  connect: (token: string) => Promise<void>
  disconnect: () => void
}
const Context = createContext<Session | null>(null)
export function SessionProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient()
  const [session, setSession] = useState<{ token: string; identity: Identity } | null>(null)
  const api = useMemo(() => session ? new CPSApi(session.token) : null, [session])
  const disconnect = () => {
    void client.cancelQueries()
    client.clear()
    setSession(null)
  }
  useEffect(() => {
    if (!session) return
    const timer = window.setInterval(() => {
      if (session.identity.expires * 1000 <= Date.now()) {
        void client.cancelQueries()
        client.clear()
        setSession(null)
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [session, client])
  return <Context.Provider value={{ api, identity: session?.identity ?? null, disconnect, connect: async raw => {
    const token = raw.trim().replace(/^Bearer\s+/i, '')
    const identity = parseIdentity(token)
    await new CPSApi(token).request('/inspections?limit=1')
    await client.cancelQueries()
    client.clear()
    setSession({ token, identity })
  } }}>{children}</Context.Provider>
}
export function useSession() {
  const context = useContext(Context)
  if (!context) throw new Error('SessionProvider is required')
  return context
}
