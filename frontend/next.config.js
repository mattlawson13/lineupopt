/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Proxy /api/* server-side to the FastAPI backend so the browser only
    // ever talks to this app's own origin — the backend itself is never
    // exposed publicly (relevant whether this is tunneled locally, e.g.
    // via ngrok, or deployed for real). Set BACKEND_URL in the hosting
    // platform's env vars (e.g. Vercel project settings) to point at the
    // deployed backend (e.g. Render) — defaults to localhost for dev.
    // Strip any trailing slash — a stray one here would turn "/api/health"
    // into a "//api/health" double-slash request, which FastAPI 404s on
    // (path matching is exact, so it's an easy env-var typo to make).
    const backendUrl = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
