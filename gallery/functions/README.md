# Cloudflare Pages Functions

Run the site locally from `gallery/` with `npx wrangler pages dev .`. Set the Firebase project ID and admin email list in local Wrangler configuration or with environment variables. Bind an R2 bucket named `PHOTOS` in the local environment; do not put credentials in `wrangler.toml`.

Before deployment, replace the R2 bucket and Firebase project placeholders, set `ADMIN_EMAILS` as a Pages secret (`npx wrangler pages secret put ADMIN_EMAILS`), and bind the production R2 bucket as `PHOTOS` in the Pages project settings. Deploy with `pipeline/deploy_cloudflare.ps1 -Deploy` (it uploads only the built `dist/` folder and puts the data JSON in R2). Never run `wrangler pages deploy .`: that publishes every file in `gallery/` including `data/`. Firebase users must have access to their own Firestore `users/{uid}` document for status checks.

Run the function unit tests with `node --test functions/tests/*.test.mjs` from `gallery/`.
