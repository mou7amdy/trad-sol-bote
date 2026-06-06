'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { Search, Filter, ChevronDown, ChevronUp, ExternalLink } from 'lucide-react'
import { useSignals } from '@/hooks/usePortfolio'

export default function SignalsPage() {
  const { signals, loading } = useSignals(100)
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'BUY' | 'SKIP'>('all')
  const [expanded, setExpanded] = useState<string | null>(null)

  const filtered = signals.filter(s => {
    if (filter === 'BUY' && (s.composite_score || 0) < 70) return false
    if (filter === 'SKIP' && (s.composite_score || 0) >= 70) return false
    if (search && !s.symbol?.toLowerCase().includes(search.toLowerCase()) && !s.token_address?.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
            <input
              type="text" placeholder="Search token..." value={search} onChange={e => setSearch(e.target.value)}
              className="w-56 bg-[#1E1E2A] border border-card-border rounded-lg pl-9 pr-3 py-2 text-xs text-text-primary placeholder:text-text-muted outline-none focus:border-accent/50 transition-colors"
            />
          </div>
          <div className="flex gap-1">
            {(['all', 'BUY', 'SKIP'] as const).map(f => (
              <button key={f} onClick={() => setFilter(f)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition-colors ${
                  filter === f ? 'bg-accent text-white' : 'bg-[#1E1E2A] text-text-muted hover:text-text-primary'
                }`}
              >{f}</button>
            ))}
          </div>
        </div>
        <span className="text-xs text-text-muted">{filtered.length} signals</span>
      </div>

      <div className="bg-card border border-card-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-card-border bg-[#0A0A0F]/50">
                {['Time', 'Token', 'Score', 'Confidence', 'Decision', 'Price', 'Source'].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-text-muted font-medium uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((s: any, i: number) => {
                const score = s.composite_score || 0
                const isBuy = score >= 70
                const open = expanded === s.token_address
                return (
                  <>
                    <tr key={s.token_address || i}
                      onClick={() => setExpanded(open ? null : s.token_address)}
                      className="border-b border-card-border hover:bg-[#0A0A0F]/30 cursor-pointer transition-colors"
                    >
                      <td className="px-4 py-3 font-mono text-text-muted">{s.sent_at?.slice(11, 19) || '--'}</td>
                      <td className="px-4 py-3">
                        <span className="font-medium text-text-primary">{s.symbol || '?'}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`font-bold font-mono ${isBuy ? 'text-green' : 'text-red'}`}>{score.toFixed(0)}</span>
                      </td>
                      <td className="px-4 py-3 font-mono text-text-muted">{s.confidence_score?.toFixed(1) || '0'}%</td>
                      <td className="px-4 py-3">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono ${
                          isBuy ? 'bg-green/10 text-green' : 'bg-red/10 text-red'
                        }`}>
                          {isBuy ? 'BUY' : 'SKIP'}
                        </span>
                      </td>
                      <td className="px-4 py-3 font-mono text-text-muted">${s.price_at_signal?.toFixed(8) || '0'}</td>
                      <td className="px-4 py-3 text-text-muted">{s.dex_source || s.signal_type || '--'}</td>
                    </tr>
                    {open && (
                      <tr key={`${s.token_address}-detail`}>
                        <td colSpan={7} className="px-4 py-4 bg-[#0A0A0F]/50">
                          <div className="grid grid-cols-3 gap-4 text-xs">
                            <div>
                              <span className="text-text-muted block mb-1">Token Address</span>
                              <span className="font-mono text-text-primary break-all">{s.token_address}</span>
                            </div>
                            <div>
                              <span className="text-text-muted block mb-1">Scores Breakdown</span>
                              {s.composite_score && <span className="font-mono text-text-primary">Composite: {s.composite_score.toFixed(1)}</span>}
                            </div>
                            <div>
                              <span className="text-text-muted block mb-1">Decision</span>
                              <span className="font-mono text-text-primary">{s.result || s.signal_type || '--'}</span>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
        {filtered.length === 0 && !loading && (
          <div className="text-center py-12 text-text-muted text-sm">No signals found</div>
        )}
        {loading && (
          <div className="text-center py-12 text-text-muted text-sm">Loading...</div>
        )}
      </div>
    </div>
  )
}
