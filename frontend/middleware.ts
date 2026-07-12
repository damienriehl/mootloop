/**
 * Perimeter middleware (FD-8/FD-5): the Next.js app is the single Access-verified
 * surface. Every app route re-verifies the Cloudflare Access JWT with jose, FAIL-CLOSED
 * — a missing/invalid token or unconfigured Access is rejected, never waved through.
 *
 * The matcher is limited to app routes; the BFF `/api/*` proxy is excluded here (it
 * forwards the same Access header downstream to FastAPI, which verifies it again).
 */
import { NextResponse, type NextRequest } from "next/server";
import { accessConfigFromEnv, verifyAccessToken } from "@/lib/auth/access";

export const config = {
  matcher: ["/matters/:path*"],
};

const ACCESS_HEADER = "cf-access-jwt-assertion";
const ACCESS_COOKIE = "CF_Authorization";

function devBypass(): boolean {
  return (
    process.env.NODE_ENV !== "production" &&
    process.env.MOOTLOOP_DEV_BYPASS_ACCESS === "1"
  );
}

export async function middleware(request: NextRequest): Promise<NextResponse> {
  if (devBypass()) return NextResponse.next();

  const config = accessConfigFromEnv();
  if (!config) {
    // Access not configured → fail closed (never serve matter routes unprotected).
    return new NextResponse("Access is not configured", { status: 503 });
  }

  const token =
    request.headers.get(ACCESS_HEADER) ?? request.cookies.get(ACCESS_COOKIE)?.value;

  try {
    await verifyAccessToken(token, config);
  } catch {
    return new NextResponse("Access denied", { status: 401 });
  }

  return NextResponse.next();
}
