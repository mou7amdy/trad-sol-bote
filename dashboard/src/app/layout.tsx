// dashboard/src/app/layout.tsx
import type { Metadata } from 'next'
import '@/app/globals.css'
import ClientLayout from './ClientLayout'

export const metadata: Metadata = {
  title: 'SOL BOT Dashboard',
  description: 'Solana Meme Coin Trading Bot',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="flex h-screen overflow-hidden">
        <ClientLayout>{children}</ClientLayout>
      </body>
    </html>
  )
}
