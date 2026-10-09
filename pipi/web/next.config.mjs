import path from "node:path";
import { fileURLToPath } from "node:url";
const backend = process.env.PIPI_BACKEND_URL || "http://127.0.0.1:8000";
const root = path.dirname(fileURLToPath(import.meta.url));
const devScriptPolicy =
  process.env.NODE_ENV === "development" ? " 'unsafe-eval'" : "";
const config = {
  outputFileTracingRoot: root,
  turbopack: { root },
  output: "standalone",
  poweredByHeader: false,
  experimental: { proxyClientMaxBodySize: "22mb" },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Frame-Options", value: "DENY" },
          {
            key: "Content-Security-Policy",
            value: `default-src 'self'; script-src 'self' 'unsafe-inline'${devScriptPolicy}; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`,
          },
        ],
      },
    ];
  },
};
export default config;
