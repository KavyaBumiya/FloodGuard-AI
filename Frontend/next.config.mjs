/** @type {import('next').NextConfig} */
const nextConfig = {
  typescript: {
    ignoreBuildErrors: false,
  },
  images: {
    unoptimized: true,
  },
  async rewrites() {
    const backend =
      process.env.NEXT_PUBLIC_API_BASE ||
      'https://floodguard-backend.ashyplant-04472250.centralindia.azurecontainerapps.io'
    return [
      { source: '/healthz', destination: `${backend}/healthz` },
      { source: '/health', destination: `${backend}/health` },
      { source: '/api/:path*', destination: `${backend}/api/:path*` },
    ]
  },
}

export default nextConfig