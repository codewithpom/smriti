# Contributing to Smriti

Thanks for looking. This is a volunteer project for a school batch, so small, careful changes are the best kind.

## The one rule

**Never commit real photos, names, registration numbers, face embeddings, cookies or keys.** The repository contains only fabricated demo data (`gallery/demo/`). Test with that, or with the emulator and made-up fixtures. A pull request that includes real data will be closed and the history rewritten.

## Find something to do

- Browse [good first issues](https://github.com/codewithpom/smriti/labels/good%20first%20issue) and [help wanted](https://github.com/codewithpom/smriti/labels/help%20wanted).
- Comment on the issue so two people don't do the same work.
- Have an idea? Open an issue first for anything bigger than a small fix, so we can agree on the approach.

## Set up

You need Node 20+, Python 3.12, and a JDK 17+ only for the Firestore emulator.

```bash
git clone https://github.com/codewithpom/smriti && cd smriti

# browse the demo locally (git-ignored folders; overwrites data you keep there)
cp -r gallery/demo/data gallery/demo/img gallery/
python -m http.server 8000 --directory gallery --bind 127.0.0.1
```

## Run the checks

```bash
cd gallery/tests && npm install && npm test                                      # Firestore rules
cd gallery/functions && node --test --test-isolation=none tests/*.test.mjs       # Functions
cd gallery/pipeline && python -m unittest test_tally test_obfuscate test_filter_batch
pip install pillow && python gallery/demo/check_demo.py                          # demo data
```

CI runs the same checks on every pull request.

## How the code is organised

- **Front end** (`gallery/js`, `gallery/css`): vanilla ES modules, no bundler, no framework. Keep it that way: it keeps the free hosting simple and the code readable. Prefer small functions and the helpers in `util.js` (`el`, etc.).
- **Access control** lives in `gallery/firestore.rules`. If you change a rule, add or change a test in `gallery/tests/rules.test.mjs` in the same pull request.
- **Functions** (`gallery/functions`): the only custom auth code is `_shared/auth.js`. Be conservative here.
- **Pipeline** (`gallery/pipeline`): Python scripts. Paths come from `RKMV_SOURCE` and `RKMV_PEOPLE`; do not hard-code absolute paths. Printing names or ids to the console is a bug.

## Pull requests

- One topic per pull request, with a short description of the why.
- Add or update a test when you add logic (a branch, a parser, anything touching access).
- Keep the diff small. Don't reformat files you aren't changing.
- Accessibility counts: keyboard use, focus states, labels and contrast. The review UI is meant to work with a keyboard and a phone.
- Say what you tested and what you didn't.

## Reporting a security problem

Please do **not** open a public issue. See [SECURITY.md](SECURITY.md).

## Conduct

Be kind and assume good intent. This project follows the [Code of Conduct](CODE_OF_CONDUCT.md).
