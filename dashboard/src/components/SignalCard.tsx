'use client'

import { motion } from 'framer-motion'
import { ArrowUpRight, ArrowDownRight } from 'lucide-react'

export default function SignalCard({ signal }: { signal: any }) {
  const score = signal.composite_score || signal.score || 0
  const isBuy = signal.decision === 'BUY' || score >= 70
  const isRug = !isBuy && (score < 40 || signal.decision === 'SKIP' && /rug/i.test(signal.reason || ''))

  let color = 'bg-yellow/10 text-yellow border-yellow/20'
  let label = 'SKIP'
  let icon = null
  if (isBuy) { color = 'bg-green/10 text-green border-green/20'; label = 'BUY'; icon = <ArrowUpRight className="w-3 h-3" /> }
  else if (isRug) { color = 'bg-red/10 text-red border-red/20'; label = 'RUG'; icon = <ArrowDownRight className="w-3 h-3" /> }

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: 'auto' }}
      className="flex items-center gap-3 px-3 py-2 rounded-lg bg-card border border-card-border hover:border-accent/20 transition-colors cursor-pointer"
    >
      <span className={`px-2 py-0.5 rounded text-[10px] font-bold font-mono border ${color} flex items-center gap-1`}>
        {icon}{label}
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium text-text-primary truncate">{signal.symbol || 'Unknown'}</div>
        <div className="text-[10px] text-text-muted font-mono">{signal.reason?.slice(0, 40) || ''}</div>
      </div>
      <span className="text-sm font-bold font-mono">{score.toFixed(0)}</span>
    </motion.div>
  )
}
