/**
 * The thin BFF proxy (FD-5/FD-7): forwards same-origin `/api/*` to the INTERNAL
 * FastAPI. This is the only place the browser's API calls leave Next.js.
 *
 * BFF-IS-THIN invariant: this handler performs NO business logic and NO vault access —
 * it only forwards the request (adding the internal shared secret) and streams the
 * response back. It presents `X-Mootloop-Internal` to FastAPI (localhost trust is dead
 * on the shared Docker network) and forwards the Access header so FastAPI's own
 * verifier still runs. SSE responses are streamed with buffering disabled.
 */
import { type NextRequest } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const API_URL = (process.env.MOOTLOOP_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
const INTERNAL_SECRET = process.env.MOOTLOOP_INTERNAL_SECRET ?? "";
const INTERNAL_HEADER = "x-mootloop-internal";

// Request headers we forward downstream (identity, CSRF, cookies, content negotiation).
const FORWARD_REQUEST_HEADERS = [
  "content-type",
  "accept",
  "cf-access-jwt-assertion",
  "x-csrf-token",
  "cookie",
];

// Response headers we surface back to the browser (content-disposition names the
// streamed deliverable download; content-length lets the browser show progress).
const FORWARD_RESPONSE_HEADERS = [
  "content-type",
  "content-disposition",
  "content-length",
  "cache-control",
  "retry-after",
];

async function proxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
): Promise<Response> {
  const { path } = await context.params;
  const search = request.nextUrl.search;
  const target = `${API_URL}/api/${path.map(encodeURIComponent).join("/")}${search}`;

  const headers = new Headers();
  for (const name of FORWARD_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  headers.set(INTERNAL_HEADER, INTERNAL_SECRET);

  const method = request.method.toUpperCase();
  const init: RequestInit & { duplex?: "half" } = {
    method,
    headers,
    redirect: "manual",
    cache: "no-store",
  };
  if (method !== "GET" && method !== "HEAD" && request.body) {
    init.body = request.body;
    init.duplex = "half";
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, init);
  } catch {
    return new Response("upstream unavailable", { status: 502 });
  }

  const responseHeaders = new Headers();
  for (const name of FORWARD_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  // Forward every Set-Cookie (the CSRF double-submit cookie must reach the browser).
  for (const cookie of upstream.headers.getSetCookie()) {
    responseHeaders.append("set-cookie", cookie);
  }

  // SSE: stream through with buffering disabled so events arrive in real time.
  const contentType = upstream.headers.get("content-type") ?? "";
  if (contentType.includes("text/event-stream")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
    responseHeaders.set("x-accel-buffering", "no");
    responseHeaders.set("connection", "keep-alive");
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
export const HEAD = proxy;
export const OPTIONS = proxy;
