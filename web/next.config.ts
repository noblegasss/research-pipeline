import type { NextConfig } from "next";

const backendBase = process.env.BACKEND_API_BASE || "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  allowedDevOrigins: ["127.0.0.1"],
  // Pipeline SSE streams can take minutes; extend proxy timeout
  experimental: {
    proxyTimeout: 300_000, // 5 minutes
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendBase}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
