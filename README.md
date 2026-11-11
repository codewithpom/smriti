# Smriti

**A private photo archive for a school batch, where face matching proposes and the students confirm. Runs entirely on free tiers.**

[![CI](https://github.com/codewithpom/smriti/actions/workflows/ci.yml/badge.svg)](https://github.com/codewithpom/smriti/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Live demo](https://img.shields.io/badge/demo-no%20sign--in-brightgreen)](https://smriti-gallery.pages.dev)

**[Live demo](https://smriti-gallery.pages.dev)** (fabricated data, no sign-in, press **F** in the photo viewer to outline faces)

![Photo viewer with a detected face outlined in red and labelled](docs/img/demo-faces.png)

## Why this exists

I'm an ex-student of a school that runs under a charity trust. A few hundred photos of my batch were scattered across phones and the school's Facebook page, and nobody knew who was in what. I'm building Smriti (Sanskrit for *memory*) for them, unpaid, so every classmate can find their own pictures.

The hard parts turned out to be the interesting ones: the photos show minors, there is no hosting budget, and a face matcher is wrong often enough that I didn't want it deciding on its own. So the model only **proposes**, and the students **confirm**.

If that sounds like something you'd like to help with, you're very welcome. See [Contributing](#contributing) and the [good first issues](https://github.com/codewithpom/smriti/labels/good%20first%20issue).

## Features

- **Private gallery.** Photos, people, places and a force-directed "friends" graph built from who appears together.
- **Face matching, humans in the loop.** InsightFace embeds every face and proposes candidates. Anything uncertain becomes a review question instead of a guess.
- **Crowd review.** Students answer short questions ("Is this Asha?", "Where was this taken?"). A weighted vote settles each one and disputes go to an admin.
- **Google sign-in, locked to the batch.** Only verified `rkmv<registration number>@gmail.com` accounts that are on the roster get in. An admin can add emails and block accounts. An admin can also switch on approval mode, so each new student waits to be approved.
- **Student uploads** with server-side validation, per-student quotas and admin approval.
- **Admin dashboard.** Manage users, resolve disputes, review uploads, watch per-student stats, export settled answers.
- **Facebook page ingest** with `gallery-dl`, run locally with your own cookies, on the page owner's permission.
- **No build step.** Vanilla ES modules, keyboard-first review UI, works at phone width.

![Gallery grid in the demo](docs/img/demo-gallery.png)

## Try it

**Online:** <https://smriti-gallery.pages.dev>. Everything there is fabricated.

**Locally** (browse only, no accounts):

```bash
git clone https://github.com/codewithpom/smriti && cd smriti
cp -r gallery/demo/data gallery/demo/img gallery/      # git-ignored folders; this overwrites any data you keep there
python -m http.server 8000 --directory gallery --bind 127.0.0.1
# open http://127.0.0.1:8000
```

On `127.0.0.1` the app skips sign-in and reads the static files. Review answers aren't saved in this mode.

## How it works

```mermaid
flowchart LR
  S[Student browser] -- Google sign-in --> FA[Firebase Auth]
  S -- votes, own profile --> FS[(Firestore + security rules)]
  S -- Bearer ID token --> PF[Cloudflare Pages Functions]
  PF -- verify RS256 token --> FA
  PF -- approved? with user's own token --> FS
  PF --> KV[(Workers KV: data JSON, uploads)]
  S -- static assets --> CP[Cloudflare Pages]
  A[Admin dashboard] --> FS
  A --> PF
  subgraph Pipeline [Python pipeline: runs locally]
    C[catalog.py] --> AS[assets.py] --> F[faces.py InsightFace] --> B[build.py]
  end
  Pipeline -- data JSON --> KV
  FS -- tally.py: settled answers --> Pipeline
```

Design notes and the reasoning behind each choice: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The build contract the parts were written against: [`gallery/CONTRACT.md`](gallery/CONTRACT.md).

### How the crowd decides

One vote per student per question, tallied by [`gallery/pipeline/tally.py`](gallery/pipeline/tally.py):

| Rule | Value |
|---|---|
| Normal vote | weight 1 |
| "That's me" from a student who is one of the candidates | weight 2 |
| **Settled** | top answer weight >= 3 **and** >= 2x the runner-up |
| **Disputed** | total weight >= 5 and not settled, goes to the admin |
| Admin override | always wins |
| `skip` | ignored |

A cluster answer ("these 14 faces are the same person") settles every face in the cluster at once, so clusters are asked first.

## Project layout

```
gallery/
  index.html, css/, js/        front end (review UI, friends graph, admin, upload)
  firestore.rules              security rules: the real access-control layer
  functions/                   Cloudflare Pages Functions (token check, data, uploads)
  pipeline/                    catalog, assets, faces, build, tally, roster seeding, ingest, deploy
  demo/                        fabricated demo dataset and the script that builds it
  tests/                       Firestore rules tests (emulator)
docs/                          architecture notes and screenshots
.github/                       CI, issue and PR templates
```

## Development

**Prerequisites:** Node 20+, Python 3.12, and a JDK 17+ *only* for the Firestore emulator.

```bash
# Firestore rules (emulator)
cd gallery/tests && npm install && npm test

# Cloudflare Functions
cd gallery/functions && node --test --test-isolation=none tests/*.test.mjs

# Pipeline: tally, image renaming, photo filter
cd gallery/pipeline && python -m unittest test_tally test_obfuscate test_filter_batch

# Demo dataset consistency
pip install pillow && python gallery/demo/check_demo.py
```

The face pipeline needs `insightface`, `onnxruntime`, `numpy`, `scipy` and `pillow` (see [`gallery/pipeline/requirements-scan.txt`](gallery/pipeline/requirements-scan.txt)). Paths come from `RKMV_SOURCE` (photos, one folder per album) and `RKMV_PEOPLE` (student portraits and `identities.json`).

## Deploy your own (all free)

1. Create a Firebase project, enable Google sign-in and Firestore, paste the web config into `gallery/js/firebase-config.js`, then `firebase deploy --only firestore:rules`.
2. Create a free KV namespace (`wrangler kv namespace create <name>`, no card needed) and copy `gallery/deploy/smriti.toml` to your own target with that id.
3. `npx wrangler pages secret put ADMIN_EMAILS` (comma-separated admin emails; also edit the admin list in `firestore.rules`).
4. Import the roster from the admin dashboard, or with `python gallery/pipeline/seed_roster.py --dry-run` first.
5. Choose the approval mode in Admin > Users: `auto` (default) approves a roster student on first sign-in, `admin` leaves new users pending until you approve them.
6. `gallery/pipeline/deploy_cloudflare.ps1 -Target <yours> -Deploy` builds `dist/`, renames images to unguessable names, uploads the data JSON to KV and deploys Pages.

The `rkmv<number>@gmail.com` email pattern belongs to the school this was built for. Change it in `firestore.rules`, `gallery/js/auth.js` and `gallery/functions/_shared/auth.js` for your own deployment.

### Free-tier budget

| Service | Used for | Free limit |
|---|---|---|
| Firebase Auth | Google sign-in | 50k monthly active users |
| Firestore | votes, users, progress | 50k reads and 20k writes per day, 1 GiB |
| Cloudflare Pages | site + Functions | unlimited static bandwidth, 100k Function requests per day |
| Cloudflare Workers KV | data JSON, uploads | 1 GB, about 1k writes per day (no card needed) |

Read budget is the tight one, so a student's browser reads one progress doc plus only their own votes, with the answered set cached locally.

## Security and privacy

Photos of minors and face data are the sensitive part, so the rules are strict and tested. See [`SECURITY.md`](SECURITY.md) for how to report a problem.

| Threat | Control |
|---|---|
| Someone outside the batch signs in | Firestore rules and the Function both require `email_verified`, the email pattern, and a roster entry |
| A student unblocks or promotes themselves | `users` can only be created for a roster member through Google sign-in, with a server timestamp; only an admin can change `status` (tested) |
| Students admitted without review | Approval mode in `meta/config`: `auto` (default) approves a roster student on first sign-in; `admin` keeps new users `pending` until an admin approves them (tested) |
| Vote tampering or ballot stuffing | One doc per `{question}__{uid}`; rules force own uid, approved status, an exact payload shape, answer format and a matching question type. An update must wait 10 seconds after the stored vote, which caps update loops (tested) |
| Junk entries in extra people | `people_extra` can only be added for a registration number that is in the roster, so the collection stays roster-sized (every student reads it) (tested) |
| Quota exhaustion by one account | Id format and length limits, 50 pending uploads per student, 10 MB streamed upload cap |
| Forged tokens | Functions verify Firebase ID tokens with WebCrypto against Google's keys: RS256 pinned, `aud`, `iss`, `exp`, `sub` and `email_verified` checked |
| Student names served publicly | Data JSON lives in KV and is served only by an authenticated Function, never as a static file |
| Private data in git | `.gitignore` excludes photos, `data/`, embeddings and secrets; the admin list is a Pages secret |

**Known limits, stated plainly:**

- Photos are static assets protected by unguessable filenames (capability links), not by a login check, because the free Workers tier allows only 100k requests a day. Anyone who obtains one photo's link can open that photo. Ideas for better: see the open issues.
- Firestore rules cannot rate-limit. Abuse is bounded by the quotas above and visible in the admin Stats tab.
- The vote update throttle does not stop a delete-then-create loop, because rules have no per-user counter.
- Vote question ids are only format-checked, not checked against the question list, so junk ids cost writes. The tally ignores them.
- In `auto` approval mode, anyone who creates a roster Gmail address nobody has claimed can sign in. Use `admin` mode if the school did not create the addresses.
- Student emails follow a guessable pattern, so the roster and the verified-email check are the only gate. Add an address to the roster only if you know it belongs to that student.
- This handles photos and biometric data of minors. If you adapt it, get written consent from the institution and guardians, follow your local law (for example India's DPDP Act), and never publish the roster or the photos.

## Contributing

Contributions are welcome, from typo fixes to the roadmap items below. Read [`CONTRIBUTING.md`](CONTRIBUTING.md) first; the short version:

1. Pick an issue, especially one labelled [good first issue](https://github.com/codewithpom/smriti/labels/good%20first%20issue), and say you're on it.
2. Keep it small, add or update a test, and run the checks in [Development](#development).
3. **Never commit real photos, names, registration numbers, face data or cookies.** Use the demo dataset.

By taking part you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

### Roadmap

Tracked as [issues](https://github.com/codewithpom/smriti/issues). Highlights: browser smoke tests, per-image access control, a finished scheduled face-scan job, Instagram ingest, translated UI strings, an accessibility audit.

## License and credits

- Code: [MIT](LICENSE).
- Demo faces: samples from the [SFHQ synthetic face set](https://www.kaggle.com/datasets/selfishgene/synthetic-faces-high-quality-sfhq-part-1) (StyleGAN2, CC0-1.0 on Kaggle). They depict nobody. Details in [`gallery/demo/README.md`](gallery/demo/README.md).
- Face matching uses [InsightFace](https://github.com/deepinsight/insightface). Its pretrained models are published for non-commercial research use, which fits a volunteer project for a charity school but may not fit yours. Check their terms before you deploy.
- Built on Firebase, Cloudflare Pages and Workers, `gallery-dl`, NetworkX and d3.

## Author

Built by **Padmashree Jha** ([codewithpom@gmail.com](mailto:codewithpom@gmail.com)), an ex-student giving something back. I like systems design under tight budgets: security rules, auth, free-tier architecture, computer-vision pipelines, accessible front ends. Open to full-stack and applied-ML roles.
