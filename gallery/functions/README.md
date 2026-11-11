# Cloudflare Pages Functions

Run the site locally from `gallery/` with `npx wrangler pages dev .`. Set the Firebase project ID and admin email list in local Wrangler configuration or with environment variables. Bind a KV namespace (or R2 bucket) named `PHOTOS` in the local environment; do not put credentials in `wrangler.toml`.

Before deployment, check the KV namespace id and Firebase project id in `wrangler.toml`, set `ADMIN_EMAILS` as a Pages secret (`npx wrangler pages secret put ADMIN_EMAILS`), and make sure the Pages project has the `PHOTOS` KV binding. Deploy with `pipeline/deploy_cloudflare.ps1 -Deploy` (it uploads only the built `dist/` folder and puts the data JSON in KV). Never run `wrangler pages deploy .`: that publishes every file in `gallery/` including `data/`. Firebase users must have access to their own Firestore `users/{uid}` document for status checks.

Run the function unit tests with `node --test functions/tests/*.test.mjs` from `gallery/`.

## Ingest, upload status, and storage endpoints
- `POST/GET/DELETE /api/admin/fb-cookies`: Facebook session cookies for the `scan` workflow. Write-only: `GET` returns presence, size and date, never the cookies. Stored under `secrets/fb-cookies.txt` (and the optional page URL under `secrets/fb-url.txt`) in the `PHOTOS` store.
- `POST/GET /api/admin/scan`: dispatches / reads the latest run of `.github/workflows/scan.yml`. The POST accepts `fetchFacebook`, `recluster`, `reembed`, and `migrateFrom` (`kv` or `r2`).
- `POST/GET /api/admin/uploads`: lists processed review items and raw pending count; POST `{photo, action}` writes an approval/rejection marker for the pipeline.
- `GET /api/uploads/mine`: approved users see their processed upload states and raw uploads still queued.
- `GET/POST /api/admin/storage`: reads storage backend setting and usage, or sets `STORAGE_BACKEND` to `kv`/`r2`.
- GitHub calls need `GITHUB_DISPATCH_TOKEN` and `GITHUB_REPO`. The fine-grained GitHub token needs Actions: read/write and Variables: read/write on this repository only.
- Storage keys are shared between KV and R2: `pending/<uid>/<ms>-<uuid>.<jpg|png|webp>` raw uploads until processed; `status/scan.json` scan state; `status/uploads/<uid>.json` per-user states; `status/storage.json` backend usage; `data/pending.json` admin review queue; and `review/<photo>.json` approve/reject markers consumed and deleted by the pipeline. Photo IDs match `p` plus eight lowercase hexadecimal digits.
- Workflow secrets: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` and optionally `IMG_KEY` (obfuscates image names). R2 objects the job expects: `people/identities.json` + `people/reference_faces/`.
