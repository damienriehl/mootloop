/**
 * Cloudflare Access JWT verification for the Next.js perimeter (FD-8/FD-2).
 *
 * Next.js is the single Access-verified surface (FD-5), so `middleware.ts` mirrors the
 * backend's `CfAccessVerifier`: RS256 asserted by us (never read from the token),
 * `iss`/`aud`/`exp` pinned, optional allowed-email pin. FAIL-CLOSED — every path that
 * cannot positively verify throws `AccessDeniedError`.
 *
 * The JWKS getter is module-scoped (`createRemoteJWKSet` caches keys across requests)
 * and injectable, so tests sign with a generated keypair and verify offline.
 */
import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey, type KeyLike } from "jose";

export class AccessDeniedError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AccessDeniedError";
  }
}

export interface AccessPrincipal {
  email: string;
  sub: string;
}

export interface AccessConfig {
  /** The team issuer base URL, e.g. https://acme.cloudflareaccess.com */
  issuer: string;
  /** The per-application AUD tag. */
  aud: string;
  /** When set, the verified `email` claim must match (case-insensitive). */
  allowedEmail?: string;
}

/** Normalize a team slug (`acme`) or full domain into the team base URL. */
export function teamBaseUrl(domain: string): string {
  let d = domain.trim().replace(/\/+$/, "");
  if (!d) throw new AccessDeniedError("empty CF_ACCESS_TEAM_DOMAIN");
  if (d.startsWith("http://") || d.startsWith("https://")) return d;
  if (!d.includes(".")) d = `${d}.cloudflareaccess.com`;
  return `https://${d}`;
}

/** Build the config from env, or `null` when Access is not configured. */
export function accessConfigFromEnv(env: NodeJS.ProcessEnv = process.env): AccessConfig | null {
  const team = env.CF_ACCESS_TEAM_DOMAIN;
  const aud = env.CF_ACCESS_AUD;
  if (!team || !aud) return null;
  return {
    issuer: teamBaseUrl(team),
    aud,
    allowedEmail: env.CF_ACCESS_ALLOWED_EMAIL,
  };
}

const CERTS_PATH = "/cdn-cgi/access/certs";
let cachedJwks: JWTVerifyGetKey | null = null;
let cachedIssuer: string | null = null;

function remoteJwks(issuer: string): JWTVerifyGetKey {
  if (!cachedJwks || cachedIssuer !== issuer) {
    cachedJwks = createRemoteJWKSet(new URL(issuer + CERTS_PATH));
    cachedIssuer = issuer;
  }
  return cachedJwks;
}

/** A verification key: the remote JWKS getter, or a concrete key (tests). */
export type KeyResolver = JWTVerifyGetKey | KeyLike | Uint8Array;

/**
 * Verify a Cloudflare Access application token. Throws `AccessDeniedError` on any
 * failure (missing token, bad signature, wrong `aud`/`iss`, expiry, disallowed email).
 */
export async function verifyAccessToken(
  token: string | undefined | null,
  config: AccessConfig,
  keyResolver?: KeyResolver,
): Promise<AccessPrincipal> {
  if (!token) throw new AccessDeniedError("missing Cf-Access-Jwt-Assertion");
  const key = keyResolver ?? remoteJwks(config.issuer);
  let payload;
  try {
    ({ payload } = await jwtVerify(token, key as Parameters<typeof jwtVerify>[1], {
      issuer: config.issuer,
      audience: config.aud,
      algorithms: ["RS256"],
      requiredClaims: ["exp", "iss", "aud"],
    }));
  } catch (err) {
    throw new AccessDeniedError(`token rejected: ${(err as Error).name}`);
  }
  const email = typeof payload.email === "string" ? payload.email : "";
  if (config.allowedEmail && email.trim().toLowerCase() !== config.allowedEmail.trim().toLowerCase()) {
    throw new AccessDeniedError("email claim absent or not the allowed identity");
  }
  return { email, sub: String(payload.sub ?? "") };
}
