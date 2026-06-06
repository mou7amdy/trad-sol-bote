import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

export async function GET() {
  return NextResponse.json({
    wsUrl: process.env.WS_URL || 'ws://localhost:8000/ws',
    apiKey: process.env.DASHBOARD_API_KEY || 'change-me-in-production',
  })
}
