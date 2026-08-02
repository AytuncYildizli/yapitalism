# yapitalism.com

The landing page, as a standalone static Next.js app.

It previously lived at `apps/marketing/src/app/yapitalism/` inside a **fork of
Superset** — so shipping it meant deploying someone else's marketing app, and
that fork is now frozen. It lifted out cleanly because it never imported
anything Superset-specific: only `next`, `next/link` and `lucide-react`.

## Local

    bun install
    bun run dev        # http://localhost:3000
    bun run build      # static export to ./out
    bun run typecheck

## Deploy

`output: "export"` means this is pure static output — no server, no runtime
secrets, nothing to leak. Point Vercel at this directory as the project root.

    vercel --cwd web            # preview
    vercel --cwd web --prod     # production

Then attach the domain in the Vercel dashboard, or:

    vercel domains add yapitalism.com <project>

`vercel.json` sets `X-Frame-Options: DENY`, `nosniff`, a strict
`Referrer-Policy`, and a CSP with `script-src 'self'` and `form-action 'none'`.
The page collects nothing and posts nowhere, so the CSP can stay this tight —
keep it that way, and if a form or third-party script is ever added, widen the
policy deliberately rather than removing it.
