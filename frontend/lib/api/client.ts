/**
 * The typed API client (FD-8).
 *
 * `openapi-fetch` over the generated `paths`, same-origin so the Cloudflare Access
 * cookie rides along (`credentials: "include"` — no Authorization threading). Two
 * middlewares enforce the contract:
 *
 * - onRequest: attaches the CSRF double-submit header to mutating requests, fetching
 *   the token lazily from `/api/csrf` (which also sets the cookie the BFF forwards).
 * - onResponse: fails LOUD — an HTML body means an Access login redirect, so we throw
 *   `SessionExpiredError`; any other non-2xx becomes a typed `ApiError`/`LockContentionError`.
 *
 * The browser talks only to same-origin `/api/*`; the Next.js BFF proxies it to the
 * internal FastAPI (FD-5). Server components pass an absolute `baseUrl`.
 */

import createClient, { type Client, type Middleware } from "openapi-fetch";
import type { paths } from "./schema";
import { isLoginRedirect, SessionExpiredError, toApiError } from "./errors";

const CSRF_HEADER = "x-csrf-token";
const WRITE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

let csrfToken: string | null = null;
let csrfInFlight: Promise<string> | null = null;

/** Fetch (once) and cache the CSRF token; the cookie is set as a side effect. */
async function ensureCsrfToken(baseUrl: string): Promise<string> {
  if (csrfToken) return csrfToken;
  if (!csrfInFlight) {
    csrfInFlight = fetch(`${baseUrl}/api/csrf`, { credentials: "include" })
      .then(async (res) => {
        if (!res.ok) throw toApiError(res.status, await safeBody(res));
        const body = (await res.json()) as { csrf_token: string };
        csrfToken = body.csrf_token;
        return csrfToken;
      })
      .finally(() => {
        csrfInFlight = null;
      });
  }
  return csrfInFlight;
}

/** Invalidate the cached CSRF token (e.g. after a 403) so the next write re-fetches. */
export function resetCsrfToken(): void {
  csrfToken = null;
}

async function safeBody(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  try {
    if (contentType.includes("application/json")) return await response.json();
    return await response.text();
  } catch {
    return null;
  }
}

function makeMiddleware(baseUrl: string): Middleware {
  return {
    async onRequest({ request }) {
      if (WRITE_METHODS.has(request.method.toUpperCase())) {
        const token = await ensureCsrfToken(baseUrl);
        request.headers.set(CSRF_HEADER, token);
      }
      return request;
    },
    async onResponse({ response }) {
      if (response.ok) return response;
      // Access served a login page instead of our JSON API → session expired.
      if (isLoginRedirect(response)) {
        throw new SessionExpiredError();
      }
      if (response.status === 403) resetCsrfToken();
      const body = await safeBody(response.clone());
      throw toApiError(response.status, body);
    },
  };
}

export type ApiClient = Client<paths>;

/**
 * Create a client. In the browser `baseUrl` is "" (same origin → BFF); on the server
 * pass the absolute internal URL. The client is created per call-site so tests can
 * point it at an MSW origin.
 */
export function createApiClient(baseUrl = ""): ApiClient {
  const client = createClient<paths>({ baseUrl, credentials: "include" });
  client.use(makeMiddleware(baseUrl));
  return client;
}

/**
 * The active base URL. Empty in the browser (same-origin BFF). Tests set
 * `globalThis.__MOOTLOOP_API_BASE__` to an absolute origin so undici (which rejects
 * relative URLs under jsdom) and MSW can intercept.
 */
function resolveBaseUrl(): string {
  const g = globalThis as { __MOOTLOOP_API_BASE__?: string };
  return g.__MOOTLOOP_API_BASE__ ?? "";
}

let cachedClient: ApiClient | null = null;
let cachedBase: string | null = null;

/** The memoized client for the current base (rebuilt if a test changes the base). */
export function getClient(): ApiClient {
  const base = resolveBaseUrl();
  if (!cachedClient || cachedBase !== base) {
    cachedClient = createApiClient(base);
    cachedBase = base;
  }
  return cachedClient;
}

/** The default browser client (same-origin BFF). */
export const apiClient: ApiClient = getClient();
