'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import { useTrades } from '@/hooks/usePortfolio'

export default function TradesPage() {
  const { trades, loading } = useTrades(200)
  const [filter, setFilter] = useState<'all' | 'won' | 'lost' | 'open'>('all')
  const [expanded, setExpanded] = useState<number | null>(null)

  const filtered = trades.filter(t => {
    if (filter === 'open') return t.status === 'open'
    if (filter === 'won') return t.realized_pnl_sol > 0
    if (filter === 'lost') return t.realized_pnl_sol <= 0 && t.status !== 'open'
    return true
  })

  const totalPnl = trades.reduce((s, t) => s + (t.realized_pnl_sol || 0), 0)
  const best = trades.reduce((b, t) => Math.max(b, t.realized_pnl_sol || 0), -Infinity)
  const worst = trades.reduce((w, t) => Math.min(w, t.realized_pnl_sol || 0), Infinity)

  return (
    <div className="space-y-4">
      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-3">
          <div className="text-xs text-text-muted">Total P&L</div>
          <div className={`text-lg font-bold font-mono ${totalPnl >= 0 ? 'text-green' : 'text-red'}`}>
            {totalPnl >= 0 ? '+' : ''}{totalPnl.toFixed(4)} SOL
          </div>
        </motion.div>
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-3">
          <div className="text-xs text-text-muted">Best Trade</div>
          <div className="text-lg font-bold font-mono text-green">{best > -Infinity ? `+${best.toFixed(4)}` : '--'} SOL</div>
        </motion.div>
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-3">
          <div className="text-xs text-text-muted">Worst Trade</div>
          <div className="text-lg font-bold font-mono text-red">{worst < Infinity ? worst.toFixed(4) : '--'} SOL</div>
        </motion.div>
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-3">
          <div className="text-xs text-text-muted">Total Trades</div>
          <div className="text-lg font-bold font-mono text-text-primary">{trades.length}</div>
        </motion.div>
      </div>

      <div className="flex items-center gap-2">
        {(['all', 'open', 'won', 'lost'] as const).map(f => (
          <button key={f} onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition-colors ${
              filter === f ? 'bg-accent text-white' : 'bg-[#1E1E2A] text-text-muted hover:text-text-primary'
            }`}
          >{f}</button>
        ))}
      </div>

      <div className="bg-card border border-card-border rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-card-border bg-[#0A0A0F]/50">
                {['Time', 'Token', 'Entry', 'Exit', 'P&L', 'Exit Reason', 'Duration', 'Status'].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-text-muted font-medium uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((t: any, i: number) => {
                const pnl = t.realized_pnl_sol || t.unrealized_pnl_sol || 0
                const isUp = pnl > 0
                return (
                  <tr key={t.id || i} onClick={() => setExpanded(expanded === t.id ? null : t.id)}
                    className="border-b border-card-border hover:bg-[#0A0A0F]/30 cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-3 font-mono text-text-muted">{t.entry_time?.slice(11, 19) || '--'}</td>
                    <td className="px-4 py-3">
                      <span className="font-medium text-text-primary">{t.symbol || t.mint?.slice(0, 8)}</span>
                    </td>
                    <td className="px-4 py-3 font-mono text-text-muted">${t.entry_price?.toFixed(8) || '0'}</td>
                    <td className="px-4 py-3 font-mono text-text-muted">${t.current_price?.toFixed(8) || t.exit_price?.toFixed(8) || '0'}</td>
                    <td className="px-4 py-3">
                      <span className={`font-bold font-mono ${isUp ? 'text-green' : 'text-red'}`}>
                        {pnl >= 0 ? '+' : ''}{pnl.toFixed(6)}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-text-muted">{t.exit_reason || '--'}</td>
                    <td className="px-4 py-3 font-mono text-text-muted">--</td>
                    <td className="px-4 py-3">
                      <span className={`px-2 py-0.5 rounded text-[10px] font-mono ${
                        t.status === 'open' ? 'bg-yellow/10 text-yellow' :
                        isUp ? 'bg-green/10 text-green' : 'bg-red/10 text-red'
                      }`}>{t.status === 'open' ? 'OPEN' : t.status}</span>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
        {filtered.length === 0 && !loading && (
          <div className="text-center py-12 text-text-muted text-sm">No trades found</div>
        )}
        {loading && <div className="text-center py-12 text-text-muted text-sm">Loading...</div>}
      </div>
    </div>
  )
}
