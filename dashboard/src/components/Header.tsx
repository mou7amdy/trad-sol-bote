'use client'

import { useApp } from '@/app/ClientLayout'
import { useBotStatus } from '@/hooks/usePortfolio'
import { format } from 'date-fns'
import { Circle, Wifi, WifiOff, OctagonAlert } from 'lucide-react'
import { apiControl } from '@/hooks/usePortfolio'

export default function Header({ title = 'Dashboard' }: { title?: string }) {
  const { wsConnected, addAlert } = useApp()
  const { status } = useBotStatus()

  const handleEmergencyStop = async () => {
    try {
      await apiControl('emergency_stop_all')
      addAlert('Emergency stop activated', 'error')
    } catch { addAlert('Failed to stop', 'error') }
  }

  return (
    <header className="h-14 border-b border-[#1E1E2A] flex items-center justify-between px-6 bg-[#0A0A0F]/80 backdrop-blur-sm shrink-0">
      <div className="flex items-center gap-3">
        <h2 className="text-base font-semibold text-text-primary">{title}</h2>
      </div>
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-1.5 text-xs">
          {wsConnected
            ? <Wifi className="w-3.5 h-3.5 text-green" />
            : <WifiOff className="w-3.5 h-3.5 text-red-dim animate-pulse" />
          }
          <span className={wsConnected ? 'text-green' : 'text-red-dim'}>
            {wsConnected ? 'Live' : 'Offline'}
          </span>
        </div>
        <span className="text-xs text-text-muted font-mono">
          {format(new Date(), 'HH:mm:ss')}
        </span>
        <button
          onClick={handleEmergencyStop}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-red/10 text-red border border-red/20 text-xs font-semibold hover:bg-red/20 transition-colors"
        >
          <OctagonAlert className="w-3.5 h-3.5" />
          Emergency Stop
        </button>
      </div>
    </header>
  )
}
