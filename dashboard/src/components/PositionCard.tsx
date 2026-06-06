'use client'

import { motion } from 'framer-motion'
import { ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react'

export default function PositionCard({ position, onClose }: { position: any; onClose?: () => void }) {
  const pnl = position.pnl_percent || 0
  const isUp = pnl > 0
  const isFlat = pnl === 0

  return (
    <motion.div
      initial={{ opacity: 0, x: -10 }}
      animate={{ opacity: 1, x: 0 }}
      className="bg-card border border-card-border rounded-xl p-3 hover:border-accent/30 transition-colors"
    >
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`w-2 h-2 rounded-full ${isUp ? 'bg-green' : isFlat ? 'bg-yellow' : 'bg-red'}`} />
          <span className="text-sm font-semibold text-text-primary">{position.symbol || position.mint?.slice(0, 8)}</span>
        </div>
        <div className="flex items-center gap-1">
          {isUp ? <ArrowUpRight className="w-3.5 h-3.5 text-green" /> : isFlat ? <Minus className="w-3.5 h-3.5 text-yellow" /> : <ArrowDownRight className="w-3.5 h-3.5 text-red" />}
          <span className={`text-sm font-mono font-bold ${isUp ? 'text-green' : isFlat ? 'text-yellow' : 'text-red'}`}>
            {isUp ? '+' : ''}{pnl.toFixed(1)}%
          </span>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-1 text-xs text-text-muted mb-2">
        <div>Entry: <span className="font-mono text-text-primary">${position.entry_price?.toFixed(8) || '0'}</span></div>
        <div>Current: <span className="font-mono text-text-primary">${position.current_price?.toFixed(8) || '0'}</span></div>
        <div>Amount: <span className="font-mono text-text-primary">{position.amount_sol?.toFixed(4)} SOL</span></div>
        <div>Score: <span className="font-mono text-text-primary">{position.signal_score?.toFixed(0)}</span></div>
      </div>
      <div className="flex gap-1.5">
        {['2x', '5x', '10x'].map(tp => (
          <span key={tp} className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[#1E1E2A] text-text-muted">{tp}</span>
        ))}
      </div>
    </motion.div>
  )
}
