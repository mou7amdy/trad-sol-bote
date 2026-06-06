'use client'

import { useEffect, useRef, useState, useCallback } from 'react'

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null)
  const [connected, setConnected] = useState(false)
  const listenersRef = useRef<Map<string, Set<(data: any) => void>>>(new Map())

  const on = useCallback((type: string, fn: (data: any) => void) => {
    if (!listenersRef.current.has(type)) listenersRef.current.set(type, new Set())
    listenersRef.current.get(type)!.add(fn)
    return () => listenersRef.current.get(type)?.delete(fn)
  }, [])

  useEffect(() => {
    const apiKey = process.env.NEXT_PUBLIC_API_KEY || 'change-me-in-production'
    const wsUrl = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws'

    const connect = () => {
      const socket = new WebSocket(`${wsUrl}?api_key=${apiKey}`)
      socket.onopen = () => { setConnected(true); wsRef.current = socket }
      socket.onclose = () => { setConnected(false); setTimeout(connect, 3000) }
      socket.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data)
          const fns = listenersRef.current.get(msg.type)
          if (fns) fns.forEach(fn => fn(msg.data))
        } catch {}
      }
      socket.onerror = () => socket.close()
    }
    connect()
    return () => wsRef.current?.close()
  }, [])

  return { connected, on }
}
