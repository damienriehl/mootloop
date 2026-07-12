import type { NextConfig } from "next";

/**
 * MootLoop matter-tier frontend.
 *
 * `output: "standalone"` traces only the files the server needs into
 * `.next/standalone` so the Docker runner stage stays lean on the box (FD-9/deploy).
 * This app is the SINGLE Access-verified surface (FD-5 BFF topology): the browser
 * talks only to this Node server, which proxies `/api/*` to the internal FastAPI.
 */
const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  // The BFF proxy + middleware read runtime env; never inline the internal secret
  // into the client bundle (only NEXT_PUBLIC_* are exposed to the browser).
  env: {
    NEXT_PUBLIC_APP_NAME: "MootLoop",
  },
};

export default nextConfig;
