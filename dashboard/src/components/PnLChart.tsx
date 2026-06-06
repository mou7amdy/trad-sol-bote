'use client'

import { useState, useEffect } from 'react'
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from 'recharts'
import { motion } from 'framer-motion'

const RANGES = ['24h', '7d', '30d', 'All'] as const
type Range = typeof RANGES[number]

const generateMockData = (range: Range) => {
  const pts = range === '24h' ? 24 : range === '7d' ? 7 : range === '30d' ? 30 : 90
  let val = 1.0
  return Array.from({ length: pts }, (_, i) => {
    val += (Math.random() - 0.48) * 0.05
    return { time: `${i}h`, value: Math.max(0.5, val) }
  })
}

export default function PnLChart({ data: externalData }: { data?: { time: string; value: number }[] }) {
  const [range, setRange] = useState<Range>('24h')
  const [data, setData] = useState(externalData || generateMockData('24h'))

  useEffect(() => {
    if (externalData) setData(externalData)
    else setData(generateMockData(range))
  }, [range, externalData])

  const startVal = data[0]?.value || 1
  const endVal = data[data.length - 1]?.value || 1
  const pct = ((endVal - startVal) / startVal * 100)
  const isUp = pct >= 0

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="bg-card border border-card-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-sm font-semibold text-text-primary">Portfolio Value</h3>
          <span className={`text-lg font-bold font-mono ${isUp ? 'text-green' : 'text-red'}`}>
            {isUp ? '+' : ''}{pct.toFixed(2)}%
          </span>
        </div>
        <div className="flex gap-1">
          {RANGES.map(r => (
            <button key={r} onClick={() => setRange(r)}
              className={`px-2.5 py-1 rounded text-xs font-medium transition-colors ${
                range === r ? 'bg-accent text-white' : 'bg-[#1E1E2A] text-text-muted hover:text-text-primary'
              }`}
            >{r}</button>
          ))}
        </div>
      </div>
      <div className="h-48">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1E1E2A" />
            <XAxis dataKey="time" axisLine={false} tickLine={false} tick={{ fill: '#555566', fontSize: 10 }} />
            <YAxis domain={['dataMin - 0.1', 'dataMax + 0.1']} axisLine={false} tickLine={false} tick={{ fill: '#555566', fontSize: 10 }} />
            <Tooltip
              contentStyle={{ background: '#12121A', border: '1px solid #1E1E2A', borderRadius: 8, fontSize: 12 }}
              labelStyle={{ color: '#888899' }}
              formatter={(v: number) => [`$${v.toFixed(4)}`, 'Value']}
            />
            <Line type="monotone" dataKey="value" stroke="#9945FF" strokeWidth={2} dot={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </motion.div>
  )
}
