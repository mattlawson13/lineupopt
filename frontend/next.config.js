/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  async rewrites() {
    // Proxy /api/* server-side to the FastAPI backend so the browser only
    // ever talks to this app's own origin — the backend itself is never
    // exposed publicly (relevant when this is tunneled, e.g. via ngrok).
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
};

module.exports = nextConfig;
