/** @type {import('next').NextConfig} */
const nextConfig = {
  // Proxy /api/* → FastAPI backend so the frontend never hard-codes localhost:8000
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/:path*",
      },
    ];
  },
};

export default nextConfig;
