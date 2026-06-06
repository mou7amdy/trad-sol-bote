'use client'

import { motion } from 'framer-motion'

export default function StatusBar({ label, status, value }: { label: string; status: 'active' | 'inactive' | 'error'; value?: string }) {
  const colors = { active: 'bg-green text-green', inactive: 'bg-text-muted text-text-muted', error: 'bg-red text-red' }

  return (
    <div className="flex items-center gap-3 text-xs">
      <span className="text-text-muted">{label}</span>
      <div className="flex items-center gap-1.5">
        <span className={`w-1.5 h-1.5 rounded-full ${colors[status]}`} />
        <span className="font-mono font-medium text-text-primary">{value || status}</span>
      </div>
    </div>
  )
}
