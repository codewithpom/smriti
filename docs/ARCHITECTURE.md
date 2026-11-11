# Architecture and decisions

## Constraint that shaped everything
Zero paid services, student data of minors, and a few hundred users who show up all at once. Every choice below follows from those three.

## Why Firebase *and* Cloudflare
| Need | Choice | Why |
|---|---|---|
| Login | Firebase Auth (Google) | Hardened, free to 50k users, no password handling |
| Votes and approvals | Firestore + security rules | Access control is declarative and testable; no server code to get wrong |
| Private data files, uploads | Cloudflare Pages Functions + Workers KV | Firebase Storage and Cloud Functions need a paid plan, and R2 asks for a card; KV is free with no card (25 MiB per value, ~1k writes a day). `functions/_shared/store.js` also accepts an R2 bucket |
| Static site | Cloudflare Pages | Free bandwidth |

The only custom auth code is one small token check in `functions/_shared/auth.js`. Everything else is rules.

## Request flow
1. Browser signs in with Google and gets a Firebase ID token.
2. `GET /api/data/<name>` sends the token. The Function verifies it (RS256, audience, issuer, expiry, verified email, `rkmv<digits>@gmail.com`) and then asks Firestore whether the user is `approved`, **using the user's own token** so the security rules apply and no service account lives in the Function.
3. Votes go straight from the browser to Firestore. The rules decide who can write what.
4. An admin (or the scheduled job with a service account) runs `tally.py` to settle questions, writes `meta/progress`, and exports `corrections.json`.
5. The pipeline re-matches faces and rebuilds the data files. Settled questions disappear from the queue.

## Firestore model
`roster/{regno}` (seeded, read-only to users) | `users/{uid}` (created `approved` on first sign-in, or `pending` in admin approval mode; an admin can block) | `votes/{qid}__{uid}` (one per student per question) | `meta/progress` (single doc on purpose, to protect the read quota) | `meta/config` (`{approval: 'auto'|'admin'}`, admin-writable; a missing doc means `auto`) | `overrides/{qid}` (admin only)

## Decisions worth knowing
- **One progress document, not a collection.** 350 students reading 1,200 settled-question docs would blow the 50k-reads-a-day free limit. One doc is one read.
- **Approval mode is a switch.** `meta/config.approval` is `auto` (the default, and what a missing doc means), where a roster student is approved on first sign-in, or `admin`, where new users wait as `pending`. Auto suits a roster the school built; admin mode suits one that may hold addresses nobody claimed.
- **Rules cannot rate-limit**, so the design bounds damage instead: id format and length limits, per-student upload quota, admin Stats. The vote update throttle (10 seconds after the stored `ts`) caps update loops but not delete-then-create loops.
- **Images are not token-gated.** A Function per image would exceed 100k requests a day on a single grid load. Trade-off documented in the README; unguessable filenames are the planned mitigation.
- **Face matching proposes, humans decide.** Low-confidence matches never auto-confirm. A cluster answer settles many faces at once, which is why clusters are asked first.
- **Local pipeline, optional CI.** The heavy steps use environment variables for paths (`RKMV_SOURCE`, `RKMV_PEOPLE`) so the same scripts run on a laptop or a runner.

## Testing
- Firestore rules: 31 tests in the emulator (pattern bypass, unverified email, self-approval, vote id forgery, admin needs verified email, id format, vote payload shape, the update throttle, the sign-in provider check, approval modes, the `people_extra` roster gate).
- Functions: 6 tests with a fabricated RSA key and stubbed Firestore and R2 (token forgery, upload magic bytes, size cap, per-student quota, admin routes).
- Tally: 3 tests with fabricated votes.

The emulator is a Java program, which is the only reason a JDK appears in the setup. firebase-tools 13.x runs on Java 17; newer versions need Java 21.
