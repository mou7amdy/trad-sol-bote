'use client'

import { useState, useEffect } from 'react'

async function apiFetch(path: string) {
  const res = await fetch(`/api/proxy/api${path}`, {
    headers: { 'Content-Type': 'application/json' },
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
      const data = await apiFetch('/portfolio')
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
    apiFetch(`/signals?limit=${limit}`).then(d => { setSignals(d.signals || []); setLoading(false) }).catch(() => setLoading(false))
  }, [limit])

  return { signals, loading }
}

export function useTrades(limit = 100) {
  const [trades, setTrades] = useState<any[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    apiFetch(`/trades?limit=${limit}`).then(d => { setTrades(d.trades || []); setLoading(false) }).catch(() => setLoading(false))
  }, [limit])

  return { trades, loading }
}

export function useBotStatus() {
  const [status, setStatus] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const fetchStatus = async () => {
    try {
      const data = await apiFetch('/status')
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
    apiFetch('/stats').then(d => { setStats(d); setLoading(false) }).catch(() => setLoading(false))
  }, [])

  return { stats, loading }
}

export async function apiControl(action: string, value?: any) {
  const res = await fetch('/api/proxy/api/control', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action, value }),
  })
  return res.json()
}

export async function apiGet(path: string) {
  return apiFetch(path)
}
