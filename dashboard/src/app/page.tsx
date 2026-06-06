'use client'

import { useState, useCallback, useEffect } from 'react'
import { motion } from 'framer-motion'
import { Wallet, TrendingUp, Activity, Users, BarChart3 } from 'lucide-react'
import StatCard from '@/components/StatCard'
import PnLChart from '@/components/PnLChart'
import PositionCard from '@/components/PositionCard'
import SignalCard from '@/components/SignalCard'
import StatusBar from '@/components/StatusBar'
import { usePortfolio, useBotStatus, useStats, apiControl } from '@/hooks/usePortfolio'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useApp } from '@/app/ClientLayout'

export default function DashboardPage() {
  const { portfolio, loading: pfLoading } = usePortfolio()
  const { status } = useBotStatus()
  const { stats } = useStats()
  const { on } = useWebSocket()
  const { addAlert, botStatus, setBotStatus } = useApp()
  const [liveFeed, setLiveFeed] = useState<any[]>([])
  const [signalFeed, setSignalFeed] = useState<any[]>([])

  useEffect(() => {
    if (status) setBotStatus(status)
  }, [status])

  useEffect(() => {
    const unsub1 = on('token_detected', (d: any) => {
      setLiveFeed(prev => [{ ...d, _ts: Date.now() }, ...prev].slice(0, 20))
    })
    const unsub2 = on('signal', (d: any) => {
      setSignalFeed(prev => [{ ...d, _ts: Date.now() }, ...prev].slice(0, 20))
    })
    return () => { unsub1(); unsub2() }
  }, [on])

  const openPositions = portfolio?.open_positions || []
  const dailyPnl = portfolio?.daily_pnl_sol || 0

  const refresh = useCallback(() => window.location.reload(), [])

  return (
    <div className="space-y-5">
      {/* Stats Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="SOL Balance" value={`${status?.sol_balance?.toFixed(4) || '0.0000'} SOL`} icon={<Wallet className="w-4 h-4" />} />
        <StatCard
          label="Today P&L"
          value={`${dailyPnl >= 0 ? '+' : ''}${dailyPnl.toFixed(4)} SOL`}
          sub={portfolio?.daily_pnl_percent ? `${portfolio.daily_pnl_percent >= 0 ? '+' : ''}${portfolio.daily_pnl_percent.toFixed(2)}%` : undefined}
          trend={dailyPnl >= 0 ? 'up' : 'down'}
          icon={<TrendingUp className="w-4 h-4" />}
        />
        <StatCard label="Win Rate" value={`${stats?.win_rate?.toFixed(1) || '0'}%`} sub={`${stats?.total_trades || 0} trades`} icon={<Activity className="w-4 h-4" />} />
        <StatCard label="Open Positions" value={`${openPositions.length} / ${status?.max_open_positions || 3}`} sub={`$${(openPositions.reduce((s: number, p: any) => s + (p.amount_sol || 0), 0) * (status?.sol_balance || 0) / (portfolio?.total_sol || 1)).toFixed(2)} value`} icon={<Users className="w-4 h-4" />} />
      </div>

      {/* P&L Chart */}
      <PnLChart />

      {/* Two columns: Positions + Live Feed */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        {/* Open Positions */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
            <BarChart3 className="w-4 h-4 text-accent" />
            Open Positions
          </h3>
          <div className="space-y-2 max-h-96 overflow-y-auto pr-1">
            {openPositions.length === 0 ? (
              <div className="text-text-muted text-xs text-center py-8 bg-card rounded-xl border border-card-border">
                No open positions
              </div>
            ) : (
              openPositions.map((p: any, i: number) => <PositionCard key={p.mint || i} position={p} />)
            )}
          </div>
        </motion.div>

        {/* Live Feed */}
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          <h3 className="text-sm font-semibold text-text-primary mb-3 flex items-center gap-2">
            <Activity className="w-4 h-4 text-accent" />
            Live Token Feed
          </h3>
          <div className="space-y-1 max-h-96 overflow-y-auto pr-1">
            {signalFeed.length === 0 && liveFeed.length === 0 ? (
              <div className="text-text-muted text-xs text-center py-8 bg-card rounded-xl border border-card-border">
                Waiting for tokens...
              </div>
            ) : (
              [...signalFeed, ...liveFeed].slice(0, 20).map((s, i) => (
                <SignalCard key={`${s.address || s.symbol}-${i}`} signal={s} />
              ))
            )}
          </div>
        </motion.div>
      </div>

      {/* Bot Controls */}
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="bg-card border border-card-border rounded-xl p-4">
        <h3 className="text-sm font-semibold text-text-primary mb-4">Bot Controls</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatusBar label="Bot" status={status?.bot_running ? 'active' : 'inactive'} value={status?.bot_running ? 'RUNNING' : 'STOPPED'} />
          <StatusBar label="Auto-Buy" status={status?.auto_buy_enabled ? 'active' : 'inactive'} value={status?.auto_buy_enabled ? 'ON' : 'OFF'} />
          <StatusBar label="Auto-Sell" status={status?.auto_sell_enabled ? 'active' : 'inactive'} value={status?.auto_sell_enabled ? 'ON' : 'OFF'} />
          <StatusBar label="Circuit Breaker" status={status?.circuit_breaker_active ? 'error' : 'active'} value={status?.circuit_breaker_active ? `ACTIVE` : 'OK'} />
          <StatusBar label="Uptime" status="active" value={`${Math.floor((status?.uptime_seconds || 0) / 3600)}h ${Math.floor(((status?.uptime_seconds || 0) % 3600) / 60)}m`} />
          <StatusBar label="Version" status="active" value={status?.version || '1.0.0'} />
        </div>
      </motion.div>
    </div>
  )
}
