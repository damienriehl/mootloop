// @vitest-environment node
// Pure crypto — run in Node (jsdom's separate Uint8Array realm breaks jose signing).
import { describe, expect, it, beforeAll } from "vitest";
import { SignJWT, exportJWK, generateKeyPair, type KeyLike } from "jose";
import {
  AccessDeniedError,
  verifyAccessToken,
  type AccessConfig,
} from "@/lib/auth/access";

const CONFIG: AccessConfig = {
  issuer: "https://acme.cloudflareaccess.com",
  aud: "app-aud-tag",
  allowedEmail: "attorney@example.com",
};

let privateKey: KeyLike;
let publicKey: KeyLike;

async function sign(claims: Record<string, unknown>, opts?: { aud?: string; iss?: string; expOffset?: number }): Promise<string> {
  return new SignJWT(claims)
    .setProtectedHeader({ alg: "RS256", kid: "test-kid" })
    .setIssuedAt()
    .setIssuer(opts?.iss ?? CONFIG.issuer)
    .setAudience(opts?.aud ?? CONFIG.aud)
    .setExpirationTime(`${opts?.expOffset ?? 3600}s`)
    .sign(privateKey);
}

beforeAll(async () => {
  const pair = await generateKeyPair("RS256");
  privateKey = pair.privateKey;
  publicKey = pair.publicKey;
  await exportJWK(publicKey); // sanity: key is exportable as a JWK
});

describe("Access JWT verification (middleware perimeter, fail-closed)", () => {
  it("accepts a correctly signed, audienced token for the allowed email", async () => {
    const token = await sign({ email: "attorney@example.com", sub: "u1" });
    const principal = await verifyAccessToken(token, CONFIG, publicKey);
    expect(principal.email).toBe("attorney@example.com");
    expect(principal.sub).toBe("u1");
  });

  it("rejects a missing token", async () => {
    await expect(verifyAccessToken(undefined, CONFIG, publicKey)).rejects.toBeInstanceOf(
      AccessDeniedError,
    );
    await expect(verifyAccessToken(null, CONFIG, publicKey)).rejects.toBeInstanceOf(
      AccessDeniedError,
    );
  });

  it("rejects a token signed by a different key (bad signature)", async () => {
    const otherPair = await generateKeyPair("RS256");
    const forged = await new SignJWT({ email: "attorney@example.com" })
      .setProtectedHeader({ alg: "RS256" })
      .setIssuedAt()
      .setIssuer(CONFIG.issuer)
      .setAudience(CONFIG.aud)
      .setExpirationTime("1h")
      .sign(otherPair.privateKey);
    await expect(verifyAccessToken(forged, CONFIG, publicKey)).rejects.toBeInstanceOf(
      AccessDeniedError,
    );
  });

  it("rejects the wrong audience", async () => {
    const token = await sign({ email: "attorney@example.com" }, { aud: "some-other-app" });
    await expect(verifyAccessToken(token, CONFIG, publicKey)).rejects.toBeInstanceOf(
      AccessDeniedError,
    );
  });

  it("rejects the wrong issuer", async () => {
    const token = await sign({ email: "attorney@example.com" }, { iss: "https://evil.cloudflareaccess.com" });
    await expect(verifyAccessToken(token, CONFIG, publicKey)).rejects.toBeInstanceOf(
      AccessDeniedError,
    );
  });

  it("rejects an expired token", async () => {
    const token = await sign({ email: "attorney@example.com" }, { expOffset: -10 });
    await expect(verifyAccessToken(token, CONFIG, publicKey)).rejects.toBeInstanceOf(
      AccessDeniedError,
    );
  });

  it("rejects a valid signature whose email is not the allowed identity", async () => {
    const token = await sign({ email: "someone-else@example.com" });
    await expect(verifyAccessToken(token, CONFIG, publicKey)).rejects.toBeInstanceOf(
      AccessDeniedError,
    );
  });

  it("rejects a service token (no email claim) on this matter surface", async () => {
    const token = await sign({ sub: "service-token" });
    await expect(verifyAccessToken(token, CONFIG, publicKey)).rejects.toBeInstanceOf(
      AccessDeniedError,
    );
  });
});
