// dashboard/src/app/ClientLayout.tsx
'use client'

import { useState, useEffect, createContext, useContext, useCallback } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import Sidebar from '@/components/Sidebar'
import Header from '@/components/Header'
import AlertBanner from '@/components/AlertBanner'

interface Alert {
  id: string
  text: string
  type: 'success' | 'error' | 'warning' | 'info'
}

interface AppContextType {
  wsConnected: boolean
  alerts: Alert[]
  addAlert: (text: string, type?: Alert['type']) => void
  botStatus: any
  setBotStatus: (s: any) => void
}

export const AppContext = createContext<AppContextType>({
  wsConnected: false,
  alerts: [],
  addAlert: () => {},
  botStatus: null,
  setBotStatus: () => {},
})

export const useApp = () => useContext(AppContext)

export default function ClientLayout({ children }: { children: React.ReactNode }) {
  const [wsConnected, setWsConnected] = useState(false)
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [botStatus, setBotStatus] = useState<any>(null)

  const addAlert = useCallback((text: string, type: Alert['type'] = 'info') => {
    const id = Math.random().toString(36).slice(2)
    setAlerts(prev => [...prev.slice(-4), { id, text, type }])
    setTimeout(() => setAlerts(prev => prev.filter(a => a.id !== id)), 5000)
  }, [])

  useEffect(() => {
    let cancelled = false
    let socket: WebSocket | null = null
    let reconnectTimer: any

    const connect = async () => {
      try {
        const resp = await fetch('/api/config')
        const config = await resp.json()
        if (cancelled) return

        const apiKey = config.apiKey
        const wsUrl = config.wsUrl

        socket = new WebSocket(`${wsUrl}?api_key=${apiKey}`)
        socket.onopen = () => setWsConnected(true)
        socket.onclose = () => {
          setWsConnected(false)
          if (!cancelled) reconnectTimer = setTimeout(connect, 3000)
        }
        socket.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data)
            if (msg.type === 'alert') addAlert(msg.data.text || msg.data.message, 'warning')
          } catch {}
        }
        socket.onerror = () => socket?.close()
      } catch {
        if (!cancelled) reconnectTimer = setTimeout(connect, 3000)
      }
    }
    connect()
    return () => {
      cancelled = true
      socket?.close()
      clearTimeout(reconnectTimer)
    }
  }, [addAlert])

  return (
    <AppContext.Provider value={{ wsConnected, alerts, addAlert, botStatus, setBotStatus }}>
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          {children}
        </main>
      </div>
      <AnimatePresence>
        {alerts.map(a => (
          <motion.div
            key={a.id}
            initial={{ opacity: 0, y: 50 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            className="fixed bottom-4 right-4 z-50 space-y-2"
          >
            <AlertBanner text={a.text} type={a.type} />
          </motion.div>
        ))}
      </AnimatePresence>
    </AppContext.Provider>
  )
}
