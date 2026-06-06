'use client'

import { useState, useEffect } from 'react'
import { motion } from 'framer-motion'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, LineChart, Line } from 'recharts'
import { apiGet } from '@/hooks/usePortfolio'
import { useApp } from '@/app/ClientLayout'
import { RefreshCw } from 'lucide-react'

export default function BacktestPage() {
  const { addAlert } = useApp()
  const [report, setReport] = useState<any>(null)
  const [mlData, setMlData] = useState<any>(null)
  const [loading, setLoading] = useState(true)

  const fetchData = async () => {
    setLoading(true)
    try {
      const [r, m] = await Promise.all([
        apiGet('/api/backtest').catch(() => ({ error: 'No report' })),
        apiGet('/api/ml/accuracy').catch(() => ({ error: 'No ML data' })),
      ])
      setReport(r)
      setMlData(m)
    } catch {}
    setLoading(false)
  }

  useEffect(() => { fetchData() }, [])

  const monthlyData = [
    { month: 'Jan', pnl: 0.12, trades: 15 },
    { month: 'Feb', pnl: -0.05, trades: 12 },
    { month: 'Mar', pnl: 0.23, trades: 20 },
    { month: 'Apr', pnl: 0.08, trades: 18 },
  ]

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-primary">Backtest Results</h2>
        <button onClick={fetchData} className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent/10 text-accent text-xs font-medium hover:bg-accent/20 transition-colors">
          <RefreshCw className="w-3.5 h-3.5" /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="text-center py-16 text-text-muted text-sm">Loading backtest data...</div>
      ) : report?.error ? (
        <div className="text-center py-16 text-text-muted text-sm">
          Backtest report not yet available. Run the backtest engine first.
        </div>
      ) : (
        <>
          {/* Top Stats */}
          <div className="grid grid-cols-4 gap-4">
            {[
              { label: 'Tokens Analyzed', value: report?.tokens_analyzed || 0 },
              { label: 'Trades Simulated', value: report?.trades_simulated || 0 },
              { label: 'Win Rate', value: `${(report?.win_rate * 100 || 0).toFixed(1)}%`, up: (report?.win_rate || 0) > 0.5 },
              { label: 'Profit Factor', value: (report?.profit_factor || 0).toFixed(2), up: (report?.profit_factor || 0) > 1 },
            ].map((s, i) => (
              <motion.div key={i} initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0, transition: { delay: i * 0.05 } }}
                className="bg-card border border-card-border rounded-xl p-3"
              >
                <div className="text-xs text-text-muted">{s.label}</div>
                <div className={`text-lg font-bold font-mono mt-1 ${s.up === true ? 'text-green' : s.up === false ? 'text-red' : 'text-text-primary'}`}>{s.value}</div>
              </motion.div>
            ))}
          </div>

          {/* Charts */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-4">
              <h3 className="text-xs font-semibold text-text-primary mb-3">Monthly P&L</h3>
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={monthlyData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1E1E2A" />
                    <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fill: '#555566', fontSize: 10 }} />
                    <YAxis axisLine={false} tickLine={false} tick={{ fill: '#555566', fontSize: 10 }} />
                    <Tooltip contentStyle={{ background: '#12121A', border: '1px solid #1E1E2A', borderRadius: 8, fontSize: 12 }} />
                    <Bar dataKey="pnl" fill="#9945FF" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </motion.div>

            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-4">
              <h3 className="text-xs font-semibold text-text-primary mb-3">Trades by Month</h3>
              <div className="h-48">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={monthlyData}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1E1E2A" />
                    <XAxis dataKey="month" axisLine={false} tickLine={false} tick={{ fill: '#555566', fontSize: 10 }} />
                    <YAxis axisLine={false} tickLine={false} tick={{ fill: '#555566', fontSize: 10 }} />
                    <Tooltip contentStyle={{ background: '#12121A', border: '1px solid #1E1E2A', borderRadius: 8, fontSize: 12 }} />
                    <Line type="monotone" dataKey="trades" stroke="#00FFA3" strokeWidth={2} dot={false} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </motion.div>
          </div>

          {/* ML Models */}
          {mlData?.models && Object.keys(mlData.models).length > 0 && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-4">
              <h3 className="text-xs font-semibold text-text-primary mb-3">ML Model Metrics</h3>
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                {Object.entries(mlData.models).map(([name, m]: [string, any]) => (
                  <div key={name} className="bg-[#0A0A0F] rounded-lg p-3 border border-card-border">
                    <div className="text-xs font-medium text-text-primary mb-2">{name}</div>
                    <div className="space-y-1 text-xs">
                      <div className="flex justify-between"><span className="text-text-muted">F1</span><span className="font-mono text-text-primary">{(m.f1 || 0).toFixed(3)}</span></div>
                      <div className="flex justify-between"><span className="text-text-muted">AUC</span><span className="font-mono text-text-primary">{(m.roc_auc || 0).toFixed(3)}</span></div>
                      <div className="flex justify-between"><span className="text-text-muted">Precision</span><span className="font-mono text-text-primary">{(m.precision || 0).toFixed(3)}</span></div>
                      <div className="flex justify-between"><span className="text-text-muted">Recall</span><span className="font-mono text-text-primary">{(m.recall || 0).toFixed(3)}</span></div>
                    </div>
                  </div>
                ))}
              </div>
            </motion.div>
          )}

          {/* Optimal Parameters */}
          {report?.optimal_threshold && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="bg-card border border-card-border rounded-xl p-4">
              <h3 className="text-xs font-semibold text-text-primary mb-3">Optimal Parameters</h3>
              <div className="grid grid-cols-3 gap-4">
                <div className="bg-[#0A0A0F] rounded-lg p-3 border border-card-border">
                  <div className="text-xs text-text-muted">Min Signal Score</div>
                  <div className="text-lg font-bold font-mono text-accent">{report.optimal_threshold || '0.74'}</div>
                </div>
                <div className="bg-[#0A0A0F] rounded-lg p-3 border border-card-border">
                  <div className="text-xs text-text-muted">Max Drawdown</div>
                  <div className={`text-lg font-bold font-mono ${(report.max_drawdown || 0) < 20 ? 'text-green' : 'text-yellow'}`}>
                    {(report.max_drawdown || 0).toFixed(1)}%
                  </div>
                </div>
                <div className="bg-[#0A0A0F] rounded-lg p-3 border border-card-border">
                  <div className="text-xs text-text-muted">Sharpe Ratio</div>
                  <div className={`text-lg font-bold font-mono ${(report.sharpe_ratio || 0) > 1 ? 'text-green' : 'text-yellow'}`}>
                    {(report.sharpe_ratio || 0).toFixed(2)}
                  </div>
                </div>
              </div>
            </motion.div>
          )}
        </>
      )}
    </div>
  )
}
