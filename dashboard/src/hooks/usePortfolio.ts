'use client'

import { useState, useEffect } from 'react'

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || 'change-me-in-production'

async function apiFetch(path: string) {
  const res = await fetch(`${API}${path}`, {
    headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' },
  })
  if (!res.ok) throw new Error(`API ${res.status}`)
  return res.json()
}

export function usePortfolio() {
  const [portfolio, setPortfolio] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const fetchPortfolio = async () => {
    try {
      setLoading(true)
      const data = await apiFetch('/api/portfolio')
      setPortfolio(data)
    } catch (e) {
      console.error('Portfolio fetch error:', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchPortfolio(); const i = setInterval(fetchPortfolio, 10000); return () => clearInterval(i) }, [])

  return { portfolio, loading, refetch: fetchPortfolio }
}

export function useSignals(limit = 50) {
  const [signals, setSignals] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch(`/api/signals?limit=${limit}`).then(d => { setSignals(d.signals || []); setLoading(false) }).catch(() => setLoading(false))
  }, [limit])

  return { signals, loading }
}

export function useTrades(limit = 100) {
  const [trades, setTrades] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch(`/api/trades?limit=${limit}`).then(d => { setTrades(d.trades || []); setLoading(false) }).catch(() => setLoading(false))
  }, [limit])

  return { trades, loading }
}

export function useBotStatus() {
  const [status, setStatus] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const fetchStatus = async () => {
    try {
      const data = await apiFetch('/api/status')
      setStatus(data)
    } catch (e) {
      console.error('Status fetch error:', e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchStatus(); const i = setInterval(fetchStatus, 5000); return () => clearInterval(i) }, [])

  return { status, loading, refetch: fetchStatus }
}

export function useStats() {
  const [stats, setStats] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch('/api/stats').then(d => { setStats(d); setLoading(false) }).catch(() => setLoading(false))
  }, [])

  return { stats, loading }
}

export async function apiControl(action: string, value?: any) {
  return apiFetch('/api/control')
    .catch(() => {
      return fetch(`${API}/api/control`, {
        method: 'POST',
        headers: { 'X-API-Key': API_KEY, 'Content-Type': 'application/json' },
        body: JSON.stringify({ action, value }),
      }).then(r => r.json())
    })
}

export async function apiGet(path: string) {
  return apiFetch(path)
}
