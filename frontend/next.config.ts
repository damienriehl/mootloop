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
  // Dev-only: allow the loopback IP as a dev origin so the HMR/dev-runtime handshake
  // isn't blocked when the app is opened at http://127.0.0.1:<port> (the deterministic
  // dev-port convention and headless verification browsers hit 127.0.0.1, not the
  // literal "localhost"). Without this, Next 16 blocks the /_next/webpack-hmr upgrade
  // as cross-origin and the client never hydrates. No effect on production output.
  allowedDevOrigins: ["127.0.0.1"],
  // The BFF proxy + middleware read runtime env; never inline the internal secret
  // into the client bundle (only NEXT_PUBLIC_* are exposed to the browser).
  env: {
    NEXT_PUBLIC_APP_NAME: "MootLoop",
  },
};

export default nextConfig;
