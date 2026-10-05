# Third-party runtime in this page

- **EmulatorJS 4.2.3** (`data/`), GPL-3.0 — https://github.com/EmulatorJS/EmulatorJS
  (license text: `data/LICENSE.EmulatorJS`). Vendored unmodified; the one behavioural
  change this project needs — not letting `beforeunload` reach EmulatorJS's `exit`
  handler, which unmounts `/data/saves` and empties the battery save — is made from
  `index.html` by filtering `window.addEventListener`, not by editing `data/`.
- **libretro Gambatte core** (`data/cores/gambatte-wasm.data`, `gambatte-legacy-wasm.data`),
  GPL-2.0 — source https://github.com/libretro/gambatte-libretro, built by the EmulatorJS
  project and fetched from `cdn.emulatorjs.org/4.2.3/data/cores/`.
  sha256 `ad67c7bf57f8f8b62606048e6ea498afac5b5abc76ad8de5f9dfc2a6719374bb` (wasm)
  sha256 `940a7381d51223c0be0c0f98d1a988d42cc98d03c27b392cbd29cb9f357b5f62` (legacy-wasm)
  Both variants are vendored: EmulatorJS picks one at runtime from the core's build
  report, and gambatte's report has `"options": {}`, so `options.defaultWebGL2` is
  undefined and the **-legacy (WebGL1) core is the one actually loaded**.
- **gambatte build report** (`data/cores/reports/gambatte.json`), from the same CDN.
  sha256 `a240a47bd6b2a38a6c46ee63c80bdcd24befbd50474453df4898d49282bd5f57`.

`index.html` and `no-phone-home.js` are this project's own code.

**No ROM, and nothing extracted from a ROM, is distributed here.** The page runs
whatever Game Boy ROM the person loads from their own disk. It is read with
`FileReader`, handed to the core as an in-memory `File`, and never sent anywhere —
the CSP in `index.html` pins `connect-src` to `'self' blob: data:`, which is the real
boundary because the *browser* enforces it, and `no-phone-home.js` refuses any
cross-origin request at runtime as a second, loggable layer.
