'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { motion } from 'framer-motion'
import { LayoutDashboard, Radio, DollarSign, BarChart3, Settings, Zap } from 'lucide-react'

const NAV = [
  { href: '/', label: 'Dashboard', icon: LayoutDashboard },
  { href: '/signals', label: 'Signals', icon: Radio },
  { href: '/trades', label: 'Trades', icon: DollarSign },
  { href: '/backtest', label: 'Backtest', icon: BarChart3 },
  { href: '/settings', label: 'Settings', icon: Settings },
]

export default function Sidebar() {
  const path = usePathname()

  return (
    <aside className="w-56 bg-[#0A0A0F] border-r border-[#1E1E2A] flex flex-col shrink-0">
      <div className="p-5 border-b border-[#1E1E2A]">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent to-purple-600 flex items-center justify-center">
            <Zap className="w-4 h-4 text-white" />
          </div>
          <div>
            <h1 className="text-sm font-bold text-white tracking-tight">SOL BOT</h1>
            <p className="text-[10px] text-text-muted font-mono">v1.0.0</p>
          </div>
        </div>
      </div>
      <nav className="flex-1 py-3 px-2 space-y-1">
        {NAV.map(item => {
          const active = path === item.href
          const Icon = item.icon
          return (
            <Link key={item.href} href={item.href}>
              <motion.div
                whileHover={{ x: 2 }}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-colors ${
                  active
                    ? 'bg-accent/10 text-accent font-medium'
                    : 'text-text-secondary hover:text-text-primary hover:bg-[#12121A]'
                }`}
              >
                <Icon className="w-4 h-4" />
                {item.label}
              </motion.div>
            </Link>
          )
        })}
      </nav>
      <div className="p-4 border-t border-[#1E1E2A]">
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <span className="w-2 h-2 rounded-full bg-green animate-pulse-slow" />
          Bot Online
        </div>
      </div>
    </aside>
  )
}
