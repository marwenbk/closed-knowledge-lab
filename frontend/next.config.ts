import type { NextConfig } from "next";

const backendOrigin = process.env.BACKEND_HOSTPORT
  ? `http://${process.env.BACKEND_HOSTPORT}`
  : undefined;
const frameAncestors = process.env.TOPMED_FRAME_ANCESTORS ?? "'self'";

const nextConfig: NextConfig = {
  agentRules: false,
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async rewrites() {
    if (!backendOrigin) return [];
    return [
      { source: "/api/:path*", destination: `${backendOrigin}/api/:path*` },
      { source: "/health", destination: `${backendOrigin}/health` },
      { source: "/ready", destination: `${backendOrigin}/ready` },
    ];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
        ],
      },
      {
        source: "/widget",
        headers: [
          {
            key: "Content-Security-Policy",
            value: `frame-ancestors ${frameAncestors}`,
          },
        ],
      },
    ];
  },
};

export default nextConfig;
