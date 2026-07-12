# MootLoop frontend

Next.js 16 (App Router) matter cockpit — the single Cloudflare-Access-verified surface
(FD-5 BFF topology). The browser talks only to same-origin `/api/*`, which
`app/api/[...path]/route.ts` proxies to the internal FastAPI matter API
(`mootloop.web.api.create_matter_api`) presenting the `X-Mootloop-Internal` shared
secret. Two rooms, wrapped in the pleading-spine shell:

- **Run cockpit** — `/matters/[id]/runs/[runId]`: instrument band (status, spend gauge,
  turns), run controls, persona pipeline, gate ledger, and the live SSE iteration timeline.
- **Decision inbox** — `/matters/[id]/inbox`: blocking vs. entered decision cards and the
  attestation panel.

Theming is class-based (`.dark` / `.light` on `<html>`) over Tailwind v4
courtroom-ledger tokens, applied pre-paint (no FOUC) and toggled via `ThemeToggle`.

## Develop

```bash
npm install
npm run dev        # http://localhost:8730
npm run lint && npm run typecheck && npm test
```

Env the dev server reads:

- `MOOTLOOP_API_URL` — internal FastAPI base (e.g. `http://127.0.0.1:8731`).
- `MOOTLOOP_INTERNAL_SECRET` — shared secret the BFF presents to FastAPI.
- `MOOTLOOP_DEV_BYPASS_ACCESS=1` — dev-only: skip the middleware Access check
  (`NODE_ENV !== "production"` only).

To serve real data locally, bake the synthetic demo vault into a matters-root and point
the FastAPI at it (`MOOTLOOP_MATTERS_ROOT`); see `src/mootloop/web/bake.py`
(`build_demo_vault`).

> Open the app at `http://localhost:8730` **or** `http://127.0.0.1:8730` — both hydrate.
> `next.config.ts` lists `127.0.0.1` in `allowedDevOrigins` so the loopback IP isn't
> blocked as a cross-origin dev resource (which would otherwise stall the HMR/dev-runtime
> handshake and prevent client hydration).

## Visual verification

FE-2 house rule: both rooms are screenshot-verified in **both** themes before ship.

- **2026-07-12** — cockpit + inbox verified in light and dark against the baked synthetic
  demo vault (`northfield-widgets-v-granite-supply`, run `demo-discovery-responses`) via
  the FastAPI matter API + BFF. Cockpit: instrument band (FINISHED, 201 turns, `$0.80`
  spend, EXPORT READY), persona pipeline, and green gate chips render; mono numerals
  legible; dark theme inverts parchment→ink cleanly. Inbox: BLOCKING (0) empty state and
  8 ENTERED/APPROVED decision cards (category chips, RECOMMENDED-flagged dispositions,
  attorney/timestamp footers) plus the "Certify & release" attest panel render in both
  themes with good contrast. No visual defects found. One dev-only fix applied: the
  `allowedDevOrigins` entry above.
