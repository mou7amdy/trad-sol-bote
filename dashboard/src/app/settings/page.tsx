'use client'

import { useState } from 'react'
import { motion } from 'framer-motion'
import StatusBar from '@/components/StatusBar'
import { useBotStatus, apiControl } from '@/hooks/usePortfolio'
import { useApp } from '@/app/ClientLayout'

export default function SettingsPage() {
  const { status, refetch } = useBotStatus()
  const { addAlert } = useApp()
  const [posSize, setPosSize] = useState(0.1)
  const [minScore, setMinScore] = useState(0.72)

  const handleAction = async (action: string, value?: any) => {
    try {
      const res = await apiControl(action, value)
      if (res.success) {
        addAlert(res.message || 'Success', 'success')
        refetch()
      } else {
        addAlert(res.message || 'Failed', 'error')
      }
    } catch {
      addAlert('Network error', 'error')
    }
  }

  return (
    <div className="space-y-5 max-w-3xl">
      {/* Trading Settings */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-4">Trading Settings</h3>
        <div className="space-y-5">
          <div>
            <label className="text-xs text-text-muted block mb-2">Max Position Size: <span className="font-mono text-text-primary">{posSize.toFixed(2)} SOL</span></label>
            <input type="range" min="0.01" max="0.5" step="0.01" value={posSize} onChange={e => setPosSize(parseFloat(e.target.value))}
              onMouseUp={() => handleAction('set_position_size', posSize)}
              className="w-full"
            />
            <div className="flex justify-between text-[10px] text-text-muted mt-1"><span>0.01</span><span>0.50</span></div>
          </div>
          <div>
            <label className="text-xs text-text-muted block mb-2">Min Signal Score: <span className="font-mono text-text-primary">{minScore.toFixed(2)}</span></label>
            <input type="range" min="0.1" max="1.0" step="0.01" value={minScore} onChange={e => setMinScore(parseFloat(e.target.value))}
              onMouseUp={() => handleAction('set_min_score', minScore)}
              className="w-full"
            />
            <div className="flex justify-between text-[10px] text-text-muted mt-1"><span>0.10</span><span>1.00</span></div>
          </div>
          <div className="flex gap-3 pt-2">
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[#1E1E2A]">
              <span className="text-xs text-text-muted">Auto-Buy</span>
              <button onClick={() => handleAction(status?.auto_buy_enabled ? 'disable_autobuy' : 'enable_autobuy')}
                className={`w-10 h-5 rounded-full transition-colors relative ${status?.auto_buy_enabled ? 'bg-green' : 'bg-text-muted'}`}
              >
                <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${status?.auto_buy_enabled ? 'left-5' : 'left-0.5'}`} />
              </button>
            </div>
            <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-[#1E1E2A]">
              <span className="text-xs text-text-muted">Auto-Sell</span>
              <button onClick={() => handleAction(status?.auto_sell_enabled ? 'disable_autosell' : 'enable_autosell')}
                className={`w-10 h-5 rounded-full transition-colors relative ${status?.auto_sell_enabled ? 'bg-green' : 'bg-text-muted'}`}
              >
                <span className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-all ${status?.auto_sell_enabled ? 'left-5' : 'left-0.5'}`} />
              </button>
            </div>
          </div>
        </div>
      </motion.div>

      {/* Circuit Breaker */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-4">Circuit Breaker</h3>
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div className="bg-[#0A0A0F] rounded-lg p-3 border border-card-border">
            <div className="text-xs text-text-muted">Status</div>
            <StatusBar label="" status={status?.circuit_breaker_active ? 'error' : 'active'} value={status?.circuit_breaker_active ? 'ACTIVE' : 'INACTIVE'} />
          </div>
          <div className="bg-[#0A0A0F] rounded-lg p-3 border border-card-border">
            <div className="text-xs text-text-muted">Level</div>
            <div className="text-sm font-mono text-text-primary mt-1">{status?.circuit_breaker_level || 'OK'}</div>
          </div>
        </div>
        <div className="flex gap-2">
          <button onClick={() => handleAction('pause_minutes', 5)} className="px-3 py-1.5 rounded-lg bg-yellow/10 text-yellow border border-yellow/20 text-xs font-medium hover:bg-yellow/20 transition-colors">Pause 5m</button>
          <button onClick={() => handleAction('pause_minutes', 30)} className="px-3 py-1.5 rounded-lg bg-yellow/10 text-yellow border border-yellow/20 text-xs font-medium hover:bg-yellow/20 transition-colors">Pause 30m</button>
          <button onClick={() => handleAction('resume')} className="px-3 py-1.5 rounded-lg bg-green/10 text-green border border-green/20 text-xs font-medium hover:bg-green/20 transition-colors">Resume</button>
          <button onClick={() => handleAction('emergency_stop_all')} className="px-3 py-1.5 rounded-lg bg-red/10 text-red border border-red/20 text-xs font-medium hover:bg-red/20 transition-colors">Emergency Stop</button>
        </div>
      </motion.div>

      {/* API Status */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-4">API Status</h3>
        <div className="grid grid-cols-2 gap-3">
          {[
            { name: 'Helius RPC', status: 'active' as const },
            { name: 'Birdeye', status: 'active' as const },
            { name: 'GoPlus', status: 'active' as const },
            { name: 'Twitter / X', status: 'inactive' as const },
            { name: 'Telegram', status: 'active' as const },
            { name: 'Dashboard API', status: 'active' as const },
          ].map(api => (
            <div key={api.name} className="flex items-center justify-between bg-[#0A0A0F] rounded-lg px-3 py-2 border border-card-border">
              <span className="text-xs text-text-primary">{api.name}</span>
              <StatusBar label="" status={api.status} value={api.status === 'active' ? 'Connected' : 'Not configured'} />
            </div>
          ))}
        </div>
      </motion.div>

      {/* Bot Info */}
      <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-5">
        <h3 className="text-sm font-semibold text-text-primary mb-4">Bot Info</h3>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <div className="bg-[#0A0A0F] rounded-lg p-3 border border-card-border">
            <span className="text-text-muted">Version</span>
            <div className="font-mono text-text-primary mt-1">{status?.version || '1.0.0'}</div>
          </div>
          <div className="bg-[#0A0A0F] rounded-lg p-3 border border-card-border">
            <span className="text-text-muted">Uptime</span>
            <div className="font-mono text-text-primary mt-1">{Math.floor((status?.uptime_seconds || 0) / 3600)}h {Math.floor(((status?.uptime_seconds || 0) % 3600) / 60)}m</div>
          </div>
        </div>
      </motion.div>
    </div>
  )
}
