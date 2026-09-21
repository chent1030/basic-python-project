import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { App } from './App'
import { SessionProvider } from './application/session'
import './styles.css'

const client = new QueryClient({ defaultOptions: { queries: { staleTime: 3000, retry: false }, mutations: { retry: false } } })
createRoot(document.getElementById('root')!).render(<StrictMode><QueryClientProvider client={client}><SessionProvider><BrowserRouter><App /></BrowserRouter></SessionProvider></QueryClientProvider></StrictMode>)
