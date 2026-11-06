# Firestore rules tests

Run from `gallery/tests` (the test reads `../firestore.rules` relative to this dir):

    npm install   # once; local firebase-tools 13.x is required, see below
    npm test      # = firebase emulators:exec --only firestore,auth --project demo-rkmv-gallery --non-interactive "node --test rules.test.mjs"

Notes
- Uses the local firebase-tools 13.24.2 (devDependency). firebase-tools 15 needs Java 21; with JDK 17 use 13.x, whose emulator jar (v1.19.8) runs on Java 11+.
- The `demo-` project id needs no login. The jar is downloaded on first run to `~/.cache/firebase/emulators`; to pre-fetch it (needs network) run `npx firebase setup:emulators:firestore`.
- Ports are 8181 (firestore) and 9199 (auth) in `../firebase.json`, because 8080 was taken by another local process.
- The "emulator will default to allowing all reads and writes" warning is harmless: the test passes `../firestore.rules` to the emulator itself.
