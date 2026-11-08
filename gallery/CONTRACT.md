# CONTRACT: crowd review build (every agent builds against this file; do not deviate, ask the lead)

Plan: kept outside the repo. Static site in `gallery/` (vanilla ES modules, no bundler, no npm deps in the browser). Free tiers only.

## Hard rules
- Never read, print, upload or send anything under `gallery/data/`, `gallery/img/` or `sorted/` (private student photos, names, face data). Work from this file and the code only.
- Never commit secrets. Firebase web config is public, but service-account JSON, Cloudflare tokens and cookies are not.
- Touch only the files your task owns (listed per task). If you need a change elsewhere, say so in your final note.
- Match existing code style (`js/util.js` `el()` helper, no frameworks, plain CSS tokens in `css/`).

## Facts from the existing code
- Questions: static `data/questions.json`, each `{id,type,priority,...}`.
  - cluster: `id="c<cid>"`, `faces:[ "<photo>:<i>", ... ]`, `cands:[pid,...]`, `sample`
  - face: `id="f<photo>:<i>"`, `photo`, `i`, `bbox`, `cands`
  - scene: `id="s<photo>"`, `photo`, `reason`, `options`, `all` (17 scene ids)
- Answer value: face/cluster -> `pid` | `"none"` | `"skip"`; scene -> scene id | `"skip"`.
- `pipeline/review_server.py` `answer()` writes `data/corrections.json`: `{faces:{"<photo>:<i>":pid|"none"}, scenes:{photo:scene}, skipped:[qid], history:[...]}`; cluster answer applies to every key in `q.faces`. `build.py` and `faces.py match` read it. The export must write the same shape.
- `js/util.js` `postJSON(path, body)` posts to `API="/api"`; `review.js` calls `/answer`, `/undo`, `/rebuild`.
- People ids: students look like `S<regno>` (e.g. `S1001`); staff like `STAFF_074`. Assumption: regno = digits after `S`. The roster script must verify against `people.json` and report mismatches instead of guessing.

## People added before names are known
- People may carry `batch` (for example `MB27` or `MB28`); empty names display as `Reg <reg_no>`. Batch segments fall back to Students/Staff in the demo.
- Review can add a student by registration number and batch, then use that pid through the usual answer path. Names are optional; supply names later via CSV in the pipeline. Admin's New people tab edits names and downloads `extra_people.json` (`[{reg_no,name,batch}]`) for `pipeline/import_extra_people.py`.
- Local review only: `POST API + '/api/addperson'`, JSON `{reg_no,name?,batch?}`. Success `{ok:true,person:{id,name,batch,kind:"student",reg_no,photos:0,avatar:null}}`; HTTP 400 `{ok:false,error}` on failure. Crowd review writes `people_extra` instead; demo hides this control.

## Firebase (Spark plan: Auth + Firestore only; NO Storage, NO Functions)
Config: `js/firebase-config.js` exports `firebaseConfig` with placeholder values (`YOUR_API_KEY` etc.) and `ADMIN_EMAILS` is NOT in the browser; admins are defined in `firestore.rules` and Function env var `ADMIN_EMAILS`.
Use the modular Firebase JS SDK from `https://www.gstatic.com/firebasejs/10.14.1/` (ESM URLs), pinned.

### Identity
Valid student: token `email` matches `rkmv<digits>@gmail.com`, `email_verified == true`, and `roster/<digits>` exists.

### Collections
- `roster/{regno}`: `{pid, name}`. Read: signed-in valid student (own regno only: `regno == email digits`) or admin. Write: nobody (seeded by admin script with service account).
- `users/{uid}`: `{email, regno, pid, status, createdAt}`. Create: own uid, valid student, `status == 'approved'` (no waiting room; roster membership is the gate), regno/pid match roster. Read: own doc or admin. Update: admin only (sets `status` to `approved|blocked`). No delete.
- `votes/{qid}__{uid}`: `{qid, qtype, uid, regno, answer, ts}`. Create/update/delete: only own uid, `users/{uid}.status == 'approved'`, doc id equals `qid + '__' + uid`, `answer` is a non-empty string <= 64 chars, `ts == request.time`. Read: own votes or admin.
- `meta/progress`: `{settled:{qid:true}, disputed:[qid], updatedAt}`. Read: approved users or admin. Write: admin only. Single doc on purpose (read budget).
- `people_extra/{pid}`: `{reg_no, name, batch, addedBy, ts}`. `pid = "S" + reg_no` (4-6 digits); name may be empty, at most 60 characters; batch matches `MB` + two digits. Read: approved users or admin. Create: approved writer's uid in `addedBy`, server timestamp, only these five fields. Update/delete: admin only; updates preserve identity, writer, timestamp and shape. Loaded once per signed-in session, merged behind static people.
- Everything else denied.
- Admin = email in a list inside `firestore.rules` (currently `codewithpom@gmail.com`; add more maintainers there and in the `ADMIN_EMAILS` Pages secret).

### Read/write budget (Spark: 50k reads/day, 20k writes/day)
- A student load = 1 read of `meta/progress` + their own votes (cache the answered set in `localStorage` key `rkmv.votes.<uid>`; query `votes` where `uid == me` only when the cache is empty).
- Never read the whole `votes` collection from a student browser.

### Vote rule (tally)
Ignore `skip` and votes from non-approved users. Weight: 1 per vote; 2 for a face/cluster vote where the voter's `pid` is in `q.cands` and `answer == voter.pid` (self-claim).
- settled: top answer weight >= 3 and top >= 2 * second.
- disputed: total weight >= 5 and not settled.
Result goes to `meta/progress` and, for export, into `data/corrections.json` (same shape as above; cluster answer expands to all `q.faces`; skip `history`).

## Cloudflare
- Pages project serves the static site. Images in `img/` stay static assets (no Function per image: Workers free tier is 100k requests/day, a grid load is hundreds). Image filenames must be unguessable (capability URLs) — build step to rename is a later task, out of scope here.
- Data JSON (`photos`, `people`, `graph`, `questions`, `clusters`) is served by the Function so it needs a valid token: `GET /api/data/<name>` reads R2 key `data/<name>.json`.
- R2 bucket binding `PHOTOS`. Keys: `data/*.json`, `pending/<uid>/<ts>-<rand>.<ext>`, `inbox/<name>` (approved, awaiting pipeline).
- Env vars: `FIREBASE_PROJECT_ID`, `ADMIN_EMAILS` (comma list).

### Token check (all `/api/*` except `OPTIONS`)
`Authorization: Bearer <Firebase ID token>`. Verify RS256 against Google JWKS `https://www.googleapis.com/service_accounts/v1/jwk/securetoken@system.gserviceaccount.com` (cache by `Cache-Control`), `aud == FIREBASE_PROJECT_ID`, `iss == https://securetoken.google.com/<project>`, `exp`, `sub`, `email_verified`, email pattern. Status checks (approved) use Firestore REST `GET /v1/projects/<p>/databases/(default)/documents/users/<uid>` with the user's own ID token, so security rules apply and no service account lives in the Function.

### Endpoints (JSON; errors `{ok:false,error}` with 401/403/400/413)
- `GET /api/data/<name>`: approved user. 404 for unknown name (allowlist: photos, people, graph, questions, clusters).
- `POST /api/upload`: approved user, multipart `file` (jpeg/png/webp by magic bytes, <= 10 MB). Stores `pending/...`. Returns `{ok:true,key}`.
- `GET /api/admin/uploads`: admin. Lists `pending/` keys with size, uploader uid, time.
- `POST /api/admin/uploads` body `{key, action:'approve'|'reject'}`: admin. approve = copy to `inbox/`, delete pending; reject = delete.
- `GET /api/admin/upload-image?key=...`: admin only, streams a pending file for preview.

## Browser modules
- `js/auth.js`: exports `signIn()`, `signOut()`, `onUser(cb)`, `getToken()`, `currentProfile()` (status from `users/{uid}`), `isAdmin()` (via a `/api/admin/uploads` HEAD-style probe or token claim `email` in a list returned by the Function; lead decides, default: ask Function `GET /api/me` -> `{ok,email,regno,status,admin}`; add this endpoint).
- `js/votes.js`: exports `submitVote(q, answer)`, `deleteVote(qid)`, `loadMyVotes()`, `loadProgress()`. `review.js` uses these instead of `postJSON('/answer')`; keep keyboard shortcuts, optimistic advance, and the existing UI strings.
- `js/views/admin.js`: tabs Users (pending/approved/blocked + approve/block), Disputes (question + vote counts + choose answer, writes `meta/progress` and a `votes`-free override doc `overrides/{qid}` admin-only), Uploads, Stats (votes per student), Export (button runs the tally in the browser and offers `corrections.json` for download; no server).
- `data/` loads go through `store.js` `get()` with the token header once auth exists.

## Files per task (owners do not overlap)
- T1 `firestore.rules`, `firebase.json`, `tests/rules.test.mjs` (emulator), `pipeline/seed_roster.py`, `pipeline/tally.py`
- T2 `functions/**` (Pages Functions), `wrangler.toml`, `functions/README.md`
- T3 `js/views/admin.js`, `css/admin.css`, upload form `js/views/upload.js`
- T4 `js/auth.js`, `js/votes.js`, `js/firebase-config.js`, edits to `js/app.js`, `js/store.js`, `js/util.js`, `js/views/review.js`, `index.html`
- T5 `pipeline/ingest_facebook.ps1`, `.github/workflows/scan.yml`
- T6 scene vision proposals (writes `scene_proposals/shard_N.json` outside the repo tree, never into `data/`)
