'use client'

import { motion } from 'framer-motion'

export default function StatCard({ label, value, sub, icon, trend }: {
  label: string
  value: string
  sub?: string
  icon?: React.ReactNode
  trend?: 'up' | 'down' | 'neutral'
}) {
  const color = trend === 'up' ? 'text-green' : trend === 'down' ? 'text-red' : 'text-text-primary'

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      className="bg-card border border-card-border rounded-xl p-4 hover:border-accent/30 transition-colors"
    >
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-text-muted font-medium uppercase tracking-wider">{label}</span>
        {icon && <span className="text-text-muted">{icon}</span>}
      </div>
      <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
      {sub && <div className="text-xs text-text-muted mt-1">{sub}</div>}
    </motion.div>
  )
}
