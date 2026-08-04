# MemoryGuard Landing

Public landing page and docs site for the MemoryGuard OSS alpha.

## Local development

```bash
pnpm install
pnpm --filter @memoryguard/landing dev
```

The dev server defaults to `http://localhost:3000`.

## Build

```bash
pnpm --filter @memoryguard/landing build
```

The site is configured with Next.js static export. Build output is written to
`apps/landing/out`.

## Deploy

Deploy the static `out` directory to any static host that supports custom
domains:

- Current domain: `memoryguard.atharv.me`
- Future domain: `memoryguard.dev`

Recommended setup:

1. Run `pnpm --filter @memoryguard/landing build`.
2. Upload `apps/landing/out` to the static host.
3. Point the domain DNS record at that host.
4. Configure HTTPS at the host.

No backend, paid service, or package publication is required for this site.
