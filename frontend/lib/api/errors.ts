/**
 * Typed API error surface (FD-8).
 *
 * The client throws one of these on any non-2xx response so callers (TanStack Query,
 * mutations) branch on a real type rather than sniffing shapes:
 *
 * - `ApiError`         — a JSON error body from FastAPI (carries `status` + parsed body).
 * - `LockContentionError` — the typed 409 (`{ error: "lock_held" }`); `retriable` drives
 *   the decide-mutation's backoff (never thrown for attest — attest is never optimistic).
 * - `SessionExpiredError` — the response was HTML, meaning Cloudflare Access served a
 *   login redirect instead of our JSON API. The UI must re-authenticate, not parse it.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export class LockContentionError extends ApiError {
  readonly retriable: boolean;

  constructor(status: number, detail: string, retriable: boolean, body: unknown) {
    super(status, detail, body);
    this.name = "LockContentionError";
    this.retriable = retriable;
  }
}

export class SessionExpiredError extends Error {
  constructor(message = "Cloudflare Access session expired — re-authentication required") {
    super(message);
    this.name = "SessionExpiredError";
  }
}

/** True when the response is an Access login page (HTML) rather than our JSON API. */
export function isLoginRedirect(response: Response): boolean {
  const contentType = response.headers.get("content-type") ?? "";
  return contentType.includes("text/html");
}

interface LockBody {
  error: "lock_held";
  detail?: string;
  retriable?: boolean;
}

function isLockBody(body: unknown): body is LockBody {
  return (
    typeof body === "object" &&
    body !== null &&
    (body as { error?: unknown }).error === "lock_held"
  );
}

/** Build the right error subtype from a failed response's status + parsed body. */
export function toApiError(status: number, body: unknown): ApiError {
  if (status === 409 && isLockBody(body)) {
    return new LockContentionError(
      status,
      body.detail ?? "A lock is held on this run; retry shortly.",
      body.retriable ?? true,
      body,
    );
  }
  const detail =
    typeof body === "object" && body !== null && "detail" in body
      ? String((body as { detail: unknown }).detail)
      : `Request failed with status ${status}`;
  return new ApiError(status, detail, body);
}
