/** @type {import('next').NextConfig} */
const development = process.env.NODE_ENV === "development";
const scriptSrc = development
  ? "script-src 'self' 'unsafe-inline' 'unsafe-eval'"
  : "script-src 'self' 'unsafe-inline'";
const connectSrc = development
  ? "connect-src 'self' https: http://localhost:8000 http://127.0.0.1:8000 ws: wss:"
  : "connect-src 'self' https:";
const upgrade = development ? "" : "; upgrade-insecure-requests";

const nextConfig = {
  output: "standalone",
  async headers() {
    return [{
      source: "/(.*)",
      headers: [
        { key: "Content-Security-Policy", value: `default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self' https://bgm.tv; ${scriptSrc}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https:; ${connectSrc}; font-src 'self' data:${upgrade}` },
        { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        { key: "X-Content-Type-Options", value: "nosniff" },
        { key: "X-Frame-Options", value: "DENY" },
        { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      ],
    }];
  },
};

export default nextConfig;
