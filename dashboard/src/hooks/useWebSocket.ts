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
    let cancelled = false

    const connect = async () => {
      try {
        const resp = await fetch('/api/config')
        const config = await resp.json()
        if (cancelled) return

        const apiKey = config.apiKey
        const wsUrl = config.wsUrl

        const socket = new WebSocket(`${wsUrl}?api_key=${apiKey}`)
        socket.onopen = () => { setConnected(true); wsRef.current = socket }
        socket.onclose = () => { setConnected(false); if (!cancelled) setTimeout(connect, 3000) }
        socket.onmessage = (e) => {
          try {
            const msg = JSON.parse(e.data)
            const fns = listenersRef.current.get(msg.type)
            if (fns) fns.forEach(fn => fn(msg.data))
          } catch {}
        }
        socket.onerror = () => socket.close()
      } catch {
        if (!cancelled) setTimeout(connect, 3000)
      }
    }

    connect()
    return () => { cancelled = true; wsRef.current?.close() }
  }, [])

  return { connected, on }
}
