# Cloudflare Pages Functions

Run the site locally from `gallery/` with `npx wrangler pages dev .`. Set the Firebase project ID and admin email list in local Wrangler configuration or with environment variables. Bind a KV namespace (or R2 bucket) named `PHOTOS` in the local environment; do not put credentials in `wrangler.toml`.

Before deployment, check the KV namespace id and Firebase project id in `wrangler.toml`, set `ADMIN_EMAILS` as a Pages secret (`npx wrangler pages secret put ADMIN_EMAILS`), and make sure the Pages project has the `PHOTOS` KV binding. Deploy with `pipeline/deploy_cloudflare.ps1 -Deploy` (it uploads only the built `dist/` folder and puts the data JSON in KV). Never run `wrangler pages deploy .`: that publishes every file in `gallery/` including `data/`. Firebase users must have access to their own Firestore `users/{uid}` document for status checks.

Run the function unit tests with `node --test functions/tests/*.test.mjs` from `gallery/`.
