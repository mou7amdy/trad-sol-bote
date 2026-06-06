import { NextRequest, NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

const BACKEND_BASE = process.env.BACKEND_API_URL || 'http://localhost:8000'

async function proxy(request: NextRequest, path: string[], method: string) {
  const apiKey = process.env.DASHBOARD_API_KEY || 'change-me-in-production'
  const query = request.nextUrl.searchParams.toString()
  const target = `${BACKEND_BASE}/${path.join('/')}${query ? '?' + query : ''}`

  const headers: Record<string, string> = {
    'X-API-Key': apiKey,
    'Content-Type': 'application/json',
  }

  let body: string | undefined
  if (method === 'POST' || method === 'PUT' || method === 'PATCH') {
    try {
      body = JSON.stringify(await request.json())
    } catch {
      body = undefined
    }
  }

  try {
    const res = await fetch(target, { method, headers, body })
    const data = await res.json()
    return NextResponse.json(data, { status: res.status })
  } catch {
    return NextResponse.json({ error: 'Proxy request failed' }, { status: 502 })
  }
}

export async function GET(request: NextRequest, { params }: { params: { path: string[] } }) {
  return proxy(request, params.path, 'GET')
}

export async function POST(request: NextRequest, { params }: { params: { path: string[] } }) {
  return proxy(request, params.path, 'POST')
}

export async function PUT(request: NextRequest, { params }: { params: { path: string[] } }) {
  return proxy(request, params.path, 'PUT')
}

export async function PATCH(request: NextRequest, { params }: { params: { path: string[] } }) {
  return proxy(request, params.path, 'PATCH')
}

export async function DELETE(request: NextRequest, { params }: { params: { path: string[] } }) {
  return proxy(request, params.path, 'DELETE')
}
