import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useRef } from 'react'
import { useSession } from './session'
import { CommandAttempt } from '../domain/command-attempt'
import type { CPSApi } from '../infrastructure/api'

export function useResource<T>(key: string, load: (api: CPSApi, signal: AbortSignal) => Promise<T>, enabled = true, interval: number | false = 10000) {
  const { api, identity } = useSession()
  return useQuery({ queryKey: ['cps', identity?.tenant, identity?.actor, key],
    queryFn: ({ signal }) => load(api!, signal), enabled: Boolean(api) && enabled,
    refetchInterval: interval, retry: false,
  })
}
export function useInspections() { return useResource('inspections', (api, signal) => api.inspections(signal)) }
export function useInspection(id: string) { return useResource(`inspection:${id}`, (api, signal) => api.detail(id, signal), Boolean(id), 3000) }
export function useCommand<T>(execute: (api: CPSApi, payload: T) => Promise<unknown>) {
  const { api } = useSession()
  const client = useQueryClient()
  const attempt = useRef(new CommandAttempt())
  return useMutation({
    mutationFn: (payload: T) => { if (!api) throw new Error('请先连接后端。'); return execute(api, attempt.current.prepare(payload)) },
    onSettled: () => client.invalidateQueries({ queryKey: ['cps'] }),
    retry: false,
  })
}
