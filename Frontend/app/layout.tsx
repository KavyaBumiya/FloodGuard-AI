import type { Metadata, Viewport } from 'next'
import { Providers } from './providers'
import { ErrorBoundary } from '@/components/ErrorBoundary'
import { Shell } from '@/components/Shell'
import './globals.css'

export const metadata: Metadata = {
  title: 'FLOODGUARD AI · Command Centre',
  description: 'Predict the flood. Visualize the impact. Prepare before disaster strikes.',
  icons: {
    icon: [
      { url: '/icon-light-32x32.png', media: '(prefers-color-scheme: light)' },
      { url: '/icon-dark-32x32.png', media: '(prefers-color-scheme: dark)' },
      { url: '/icon.svg', type: 'image/svg+xml' },
    ],
    apple: '/apple-icon.png',
  },
}

export const viewport: Viewport = {
  colorScheme: 'light dark',
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: 'white' },
    { media: '(prefers-color-scheme: dark)', color: 'black' },
  ],
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="antialiased">
        <Providers>
          <ErrorBoundary>
            <Shell>{children}</Shell>
          </ErrorBoundary>
        </Providers>
      </body>
    </html>
  )
}
