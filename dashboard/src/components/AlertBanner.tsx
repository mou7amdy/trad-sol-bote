'use client'

import { motion } from 'framer-motion'
import { AlertTriangle, CheckCircle, XCircle, Info } from 'lucide-react'

const icons = {
  success: CheckCircle,
  error: XCircle,
  warning: AlertTriangle,
  info: Info,
}
const colors = {
  success: 'border-green/20 bg-green/5 text-green',
  error: 'border-red/20 bg-red/5 text-red',
  warning: 'border-yellow/20 bg-yellow/5 text-yellow',
  info: 'border-accent/20 bg-accent/5 text-accent',
}

export default function AlertBanner({ text, type = 'info' }: { text: string; type?: keyof typeof icons }) {
  const Icon = icons[type]
  return (
    <motion.div className={`flex items-center gap-2 px-4 py-2.5 rounded-lg border text-sm ${colors[type]} backdrop-blur-sm shadow-lg`}>
      <Icon className="w-4 h-4 shrink-0" />
      <span className="text-xs">{text}</span>
    </motion.div>
  )
}
