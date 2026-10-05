# pokeyellow — Pokémon Yellow

**Status: a byte-matching build of all three upstream targets, and a browser target
that was driven from the ROM picker through the whole opening, to a named save, a
page reload and a resume.** Everything below was run on this box on 2026-10-05.

---

## 0. The ROM hash in my brief was wrong — read this first

My brief gave the staged ROM as `~/decompfleet/roms/pokeyellow.gb`, 1048576 bytes,
sha1 **`0f8670a583255cff3e5b7ca71b5d7454d928fc48`**, and told me to verify against
upstream's own documented hashes before building and to report a mismatch plainly
rather than patch around it. Doing exactly that:

| | |
|---|---|
| sha1 of the staged file, measured | **`cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1`** |
| sha1 in upstream's `roms.sha1` and `README.md` | **`cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1`** ✅ |
| sha1 in my brief | `0f8670a583255cff3e5b7ca71b5d7454d928fc48` ❌ |

**The staged ROM is genuine and needs no explaining.** It matches upstream's
documented hash exactly, and the build reproduces it byte for byte.

The hash in the brief belongs to a different game: it is the sha1 of
`~/decompfleet/roms/poketcg.gbc` (Pokémon Trading Card Game). The staging record was
simply reading off the wrong row. Had I "verified" against the brief instead of
against upstream I would have reported a mismatch that does not exist, and probably
gone looking for a revision difference that is not there either — upstream has one
Yellow target, not a rev-0/rev-1 split. The coordinator sent the same correction
independently while I was mid-build; this section is what I had already measured.

Two smaller corrections to the brief, for the record:

- The staged file's extension is `.gb`; upstream's build output is named
  `pokeyellow.gbc`. Same bytes, different filename. The page accepts either, and
  the test deliberately feeds the file in under the name `some cartridge dump.bin`
  to prove the page identifies ROMs by content, not by filename.
- The brief said rgbds **1.0.4** and pointed at the shared install. pokeyellow pins
  **1.0.3**. See §2.

## 1. Upstream revision (pinned)

| | |
|---|---|
| Repo | https://github.com/pret/pokeyellow |
| Revision | `e89ead154b9968aa50eed9328ff2b38b6c194382` |
| Date / subject | 2026-09-02 — *Add some constants, minor cleanup (#166)* |
| Branch | `master`; this commit was the branch tip when pinned |
| Tags | the repo has none (`git tag` is empty) — the commit hash is the only pin available |

Pinned in `build.sh` as `PRET_REV`, which checks out that exact commit, so a re-run
cannot silently drift onto a newer master.

## 2. Toolchain — and why it is **1.0.3**, not the 1.0.4 everything else uses

| Tool | Version | Where it came from |
|---|---|---|
| rgbds | **1.0.3** (rgbasm / rgblink / rgbfix / rgbgfx) | prebuilt static Linux x86_64 release tarball |
| GNU make | 4.4.1 | system |
| gcc | 15.2.0 | system (builds `tools/` only) |
| OS | Ubuntu 26.04, x86_64, 12 cores | |

**This is the one thing in this project most likely to be got wrong by copying a
sibling.** `~/decompfleet/shared/rgbds-notes.md` says 1.0.4, because pokegold and
pokecrystal both pin 1.0.4. pokeyellow does not:

- `rgbdscheck.asm` says only *"pokeyellow requires rgbds v1.0.0 or newer"* — a
  **minimum**. Passing it does not imply a byte match, so it is not the pin.
- `.github/workflows/main.yml` carries the real pin: `env: rgbds_version: v1.0.3`.
  That is what pret's own CI builds and compares against.
- `.rgbds-version` agrees: `1.0.3`.

So I installed a sibling, `~/decompfleet/shared/rgbds-1.0.3/bin`, and left the
existing 1.0.4 install untouched for the other agents.

```
rgbds-linux-x86_64.tar.xz (v1.0.3)
  sha256 280a52061a0c516999bee75ac357628d6d50784309e0486cef25f7460e6f330b
~/decompfleet/shared/rgbds-1.0.3/bin/
  rgbasm  38a27f2eb73203b28efadbbcc97ec9cefaf453c3ad6bca223d2c6aecf8607ddb
  rgbfix  bcb69574b046e1a2e28796b2eb701fc40320b31762f2f0cb061c04bf866f1929
  rgbgfx  1fd05cd95cb40648ebdeedf6a4155314d2e8710abd70b3d07c716ad541239219
  rgblink 2a6d66594b03e50c57a6db35e3ded42c4d279881b25f46fe20e8e925bdec664e
```
Fully static (`ldd` → "not a dynamic executable"), so no libpng/zlib on the host and
no glibc concerns. Built from source is not an option here: `bison` is not installed
and `sudo` needs interactive auth.

**Does the pin actually matter for this repo?** I tested it rather than assuming.
I built the full target set with 1.0.3, then `git clean -xfd` and rebuilt with
1.0.4: **at this revision both produce the identical ROM**, sha1
`cc7d0326…` either way. So for pokeyellow-at-this-commit the version difference is
not load-bearing. I still ship 1.0.3, because CI is the authority and "it happened
to match this time" is not a reason to drift off the pin — rgbgfx output and LZ
settings do move between minor versions, and the next revision may differ.

## 3. Hashes — all three targets match

`make DEBUG=1 -j12 compare` from a `git clean -xfd` tree, in **0.9 s** on 12 cores.
`DEBUG=1` is what pret's CI uses; it only adds `-E` to rgbasm (export more symbols
into the `.sym`), which is what lets the save decoder in §5 resolve `sGameData` and
friends. The ROM bytes are identical with or without it.

| Target | sha1 | upstream `roms.sha1` |
|---|---|---|
| `pokeyellow.gbc` | `cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1` | ✅ match |
| `pokeyellow_debug.gbc` (YELLMONS.GB) | `d44e96eddfbdad633cbe4e6e64915e9e198974b0` | ✅ match |
| `pokeyellow.patch` (Virtual Console) | `f3346a5559d52c296b8feab0cdbbfb0e250ac161` | ✅ match |

### Against the staged dump — byte-for-byte, not just same-hash

| File | sha1 | md5 | size |
|---|---|---|---|
| `~/decompfleet/roms/pokeyellow.gb` (staged) | `cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1` | `d9290db87b1f0a23b89f99ee4469e34b` | 1048576 |
| `upstream/pokeyellow.gbc` (built) | `cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1` | `d9290db87b1f0a23b89f99ee4469e34b` | 1048576 |

`cmp` reports the two files identical. sha256 of both:
`8cbaa499397e4f1a679c992ea9382a2dd7942ab398b48c19829c2d9529de47bf`.

ROM header, read out of the staged file: title `POKEMON YELLOW`, CGB flag `$80`,
cart type `$1b` (**MBC5+RAM+BATTERY**), ROM size `$05` (1 MiB), RAM size `$03`
(32 KiB SRAM), version `$00`. The battery is why §4 and §6 matter.

`./build.sh` does the whole thing — installs rgbds 1.0.3 if missing, clones and pins
upstream, builds, and fails loudly if the result is not byte-identical to the staged
dump. Full run: **3.2 s**.

## 4. Browser target

`web/` is a static site; serve it from anywhere (GitHub Pages works, no server side).

- **Core:** libretro **gambatte**, via **EmulatorJS 4.2.3**, both vendored under
  `web/data/` (licences and hashes in `web/THIRD_PARTY.md`). No emscripten build was
  needed — the core is a GPL-2.0 wasm build published by the EmulatorJS project,
  which is this fleet's established pattern. Both the `-wasm` and `-legacy-wasm`
  variants are vendored, along with `cores/reports/gambatte.json`; that report has
  `"options": {}`, so `options.defaultWebGL2` is undefined and **the -legacy (WebGL1)
  core is the one actually loaded**. (Vendoring the report also removes the
  known-harmless 404 that the sibling pokegold tree left whitelisted.)
- **The user supplies the ROM.** File picker or drag-and-drop → `file.arrayBuffer()`
  → SHA-1 via WebCrypto → handed to the core as an in-memory `File`. Yellow and the
  debug build are recognised by hash and named in the status bar; any other 1 MiB
  Game Boy ROM still loads, labelled as unrecognised.
- **It is never uploaded.** Two layers, and the first is the real one:
  - **the CSP** `<meta>` pins `connect-src 'self' blob: data:` (plus
    `form-action 'none'`, `frame-src 'none'`, `object-src 'none'`, `base-uri 'none'`).
    **The browser enforces this, not my code** — no script on the page can reach a
    remote origin even if it tries, there is no race against EmulatorJS's own
    scripts, and it is one line to audit rather than a patched global;
  - `web/no-phone-home.js`, loaded first, wraps `fetch`, `XMLHttpRequest`,
    `sendBeacon`, `WebSocket` and `RTCPeerConnection`, refuses anything not
    same-origin, and **records** every outbound body it sees — which is what makes
    blocked attempts visible to the test.

  In the test, CDP's own network log shows the browser contacted exactly one host —
  `127.0.0.1:<test port>`, the local static server — and zero requests carried a
  body. See §6 for why one refusal is expected and is not a leak.
- **Saves persist.** `EJS_gameName = "pokeyellow"` is set explicitly, so the core
  always writes `/data/saves/Gambatte/pokeyellow.srm` (32768 bytes) on emscripten's
  IDBFS mount, which is backed by IndexedDB. This is **not cosmetic**: the ROM is
  handed over as a `File`, and without an explicit name EmulatorJS falls back to the
  ROM's filename, which for a blob is a fresh UUID every load — each session would
  write a different save and the previous one would look lost. The page also flushes
  explicitly every 10 s and on `visibilitychange`, because IDBFS's `autoPersist`
  debounces and a tab left alone mid-debounce loses the write.
- `EJS_disableDatabases = true` and `EJS_CacheLimit = 0` keep the ROM itself out of
  IndexedDB. It exists only in page memory, for the life of the tab. Neither affects
  the battery save, which lives on the IDBFS mount rather than in EmulatorJS's own
  stores.

### Stock EmulatorJS 4.2.3 destroys the battery save on every page exit

Yellow has a battery (`MBC5+RAM+BATTERY`), so this applies here and the page patches
around it. `emulator.js:1092` registers a `beforeunload` listener that fires
EmulatorJS's `exit` event; the handler at `GameManager.js:44` ends with
`FS.unmount("/data/saves")`, and the IndexedDB store behind that mount comes back
**empty**. `addEventListener` at `emulator.js:82` does not keep the handle, so
`removeEventListener` is not available — `index.html` therefore filters
`window.addEventListener` before injecting `data/loader.js`, drops that one listener,
and registers its own, which writes and syncs and never unmounts. Every flush is
additionally guarded by a check that `/data/saves` is still an IDBFS mount with
`autoPersist`, because syncing a non-mount makes IDBFS's reconcile delete the remote
entries.

**Measured here, not inherited as a warning.** `test/save_mechanism.py` runs two
arms, one query parameter apart, and checks the control before comparing them:

|  | stock EmulatorJS (`?stockunload=1`) | this page (default) |
|---|---|---|
| IndexedDB after boot | `{pokeyellow.srm: 32768}` | `{pokeyellow.srm: 32768}` |
| IndexedDB after `beforeunload` | **`{}`** | `{pokeyellow.srm: 32768}` |
| mount `autoPersist` before | `true` | `true` |
| mount `autoPersist` after | **`false`** | `true` |

No gameplay is needed to reproduce it — gambatte writes a `.srm` within seconds of
boot — and `autoPersist` flipping to `false` is the fingerprint of the mount being
torn down and replaced.

## 5. What was actually tested

`test/browsercheck.py` — headless Chrome 151 over a hand-rolled CDP client (no pip
on this box, so no playwright/selenium). **Audio off twice over: Chrome runs
`--mute-audio` and the page runs `?mute=1` → `EJS_volume = 0` plus `setVolume(0)`.**
Run: `python3 test/browsercheck.py`. Screenshots land in `test/out/` (gitignored);
I looked at every one of them, as a contact sheet.

Last two runs, back to back, 2026-10-05: **PASS and PASS.**

| Check | Result |
|---|---|
| ROM accepted through the page's real `<input type=file>` (DataTransfer + a real `change` event), named `some cartridge dump.bin` | ✅ |
| Page-computed sha1 == on-disk sha1 | `cc7d0326…` |
| Core identity | gambatte |
| Canvas non-zero (960×686 backing and on screen) | ✅ |
| Canvas actually drew a picture (driver-side PNG decode, cropped to the canvas) | peak mean luma 189–190 |
| Runs at ~59 fps, frame counter climbing | ✅ |
| Muted | ✅ |
| Hosts contacted, per CDP network log | `127.0.0.1:<port>` only |
| Outbound request bodies | none |
| Played: GAME FREAK → Pikachu intro → title → NEW GAME → Oak's speech → named the player → named the rival → the bedroom in Pallet Town | ✅ |
| Walked: D-pad moves the player (21.3% of pixels change) | ✅ |
| In-game START → SAVE → YES completes | ✅ |
| Save survives a full page reload (`.srm` sha1 identical either side) | ✅ |
| …and resumes: CONTINUE on the main menu, the save's status panel, player back on the exact tile | 0.3% pixel delta |
| Console errors | none |
| Unexpected HTTP failures | none |

### Proving the tab is mine

Ten agents share this machine and a fixed DevTools port lets Chrome attach to
someone else's browser — `/json/list` then hands you their page and you grade their
game as yours. So:

- `--remote-debugging-port=0`, with the real port read back from
  `DevToolsActivePort` inside this harness's own private `--user-data-dir`. There is
  no port argument anywhere in this tree, on purpose.
- a nonce in the URL that the attached target must echo back from `location.href`;
- `EJS_emulator.getCore()` must be `gambatte`;
- the SHA-1 **the page computed over the ROM bytes** must equal the SHA-1 of the
  file on disk.

All four assert on every run, including after each navigation.

### "Played", not "watched" — gated on the game's own save data

Yellow's opening is a long scripted sequence — the GAME FREAK logo, the Pikachu
animation, Oak's speech, two naming screens — that is screenshot-indistinguishable
from play. Screenshots settle nothing on their own. The gate is the save, decoded
with the symbol table **of the build under test**. `test/gen1save.py` parses
`upstream/pokeyellow.sym`, `ram/sram.asm`, `constants/charmap.asm`,
`constants/text_constants.asm` and `constants/map_constants.asm` at run time;
nothing is hardcoded from a wiki.

**Gen 1 is a stronger gate than Gen 2.** pokegold's test looks for two magic bytes
(`SAVE_CHECK_VALUE_1/_2`) in the right places. Gen 1 has no such constants — it
validates a save with a real **checksum**, so the test reimplements
`engine/menus/save.asm`'s own `CalcCheckSum` (the complement of the 8-bit sum of
`sGameData..sGameDataEnd`) and checks it against `sMainDataCheckSum`:

```
sGameData         = 01:a598 -> .srm+0x2598      sPlayerName is the same address
sGameDataEnd      = 01:b523 -> .srm+0x3523
sMainDataCheckSum = 01:b523 -> .srm+0x3523
checksum  = ~(sum of .srm[0x2598:0x3523]) & 0xff

run 1: stored 178 == computed 178     run 2: stored 177 == computed 177
```

A scripted intro cannot produce a save whose own checksum is valid over a player
name I chose. And the name is chosen, not assumed: both naming screens default to
NEW NAME, the keyboard starts on "A", and `engine/menus/naming_screen.asm` forces
the cursor onto "ED" once the field is full, so mashing A spells
`PLAYER_NAME_LENGTH - 1` = **7** A's and confirms. The expected name is computed from
that constant, not typed into the test.

Decoded identically before and after the reload, in both runs:

```
checksum_ok            true
player_name            "AAAAAAA"      (bytes 80 80 80 80 80 80 80 50 — charmap.asm: "A"=$80, "@"=$50)
player_name_terminated true           <- the main menu's own CheckForPlayerNameInSRAM test
rival_name             "AAAAAAA"
cur_map                38 = REDS_HOUSE_2F     <- resolved from constants/map_constants.asm
money                  3000           <- matches START_MONEY ($3000) in init_player_data.asm
pikachu_happiness      90             <- matches "ld a, 90 ; initialize happiness to 90"
```

Three of those (`REDS_HOUSE_2F`, `START_MONEY`, happiness 90) are independent
corroboration that the decoder is reading the fields it thinks it is: they are
values `InitPlayerData` demonstrably writes, found at offsets derived only from the
symbol table.

`player_name_terminated` is worth calling out: it is literally the condition
`engine/menus/main_menu.asm`'s `CheckForPlayerNameInSRAM` tests (scan `NAME_LENGTH`
bytes at `sPlayerName` for the terminator) before it offers CONTINUE. So the
automated check and the visible CONTINUE entry are the same fact.

The `.srm` sha1 is **not** reproducible across runs — Gen 1 rolls a random trainer
ID at new game (`InitPlayerData` calls `Random` twice), and run 1 got id 16250 while
run 2 got 39196. The assertion is therefore that the hash is *identical on both
sides of the reload within a run*, not that it equals a fixed value. The decoded
fields are the stable part and they are what the pass/fail gate uses.

### How persistence was measured (and the two ways it lies)

- `gameManager.getSaveFile()` calls `saveSaveFiles()` first, which writes the core's
  **current** SRAM over the file. Called shortly after a reload, before the core has
  loaded the restored `.srm`, it overwrites a good save with blanks and then reports
  the blanks — the test destroys the thing it is measuring. So persistence is read
  **straight out of IndexedDB on a fresh page load with no ROM picked**, when no core
  exists to clobber anything.
- The IDBFS database is **enumerated**, never guessed. `indexedDB.open()` *creates* a
  database that does not exist, so a wrong name invents an empty one and then
  confidently reports "the save was lost". It is enumerated via
  `indexedDB.databases()` and found to be named after the mount point, `/data/saves`,
  store `FILE_DATA`.
- The reload **navigates away in the same tab** rather than closing it. Closing a tab
  over CDP can skip `beforeunload` entirely, which is exactly the code path under
  test. Navigation also uses a URL nonce that must appear in `location.search`
  before anything is evaluated, because after `Page.navigate` CDP keeps evaluating
  against the *old* document for a short window.

### Two of my own test bugs, because they are the interesting part

Both produced confident, wrong results from a green-looking harness.

1. **A fixed-schedule resume photographed the wrong screens.** The first version
   pressed buttons at fixed times after reload and diffed the first run's *main
   menu* against the second run's *title screen*, reporting "the main menu changed by
   33%" — a large, meaningless number that read as a pass. The run's clock starts at
   a different point on the resume path than on the first play-through, so the same
   absolute times land on different screens. Fixed by stepping with A until the
   picture matches the spot the game was saved at, photographing every step so the
   sequence is auditable by eye. It now takes 5 or 6 presses depending on the run —
   which is precisely the variance the fixed schedule could not absorb.

2. **A fixed number of B presses left a text box open, and the save silently never
   happened.** The A-mashing that drives the opening keeps going once the player is
   standing in the bedroom, where A talks to the SNES. Whether the mash window ends
   with that dialogue open is a coin flip. A Gen 1 text box swallows START, so the
   save sequence pressed START, DOWN×3, A, A into a dialogue, never opened the menu,
   and never saved — and the test then correctly reported an all-`0xff` save, having
   never exercised saving at all. Four fixed B presses at 0.5 s did not clear it
   either: a box that is still typing ignores input, so a press on too short a timer
   is simply dropped. Fixed by making both steps **state-verified**: press B until
   the canvas crop's mean luma shows no text box (open box 153–155, clear room
   87–110 — a wide, clean gap measured on this page), then press START until the
   screen actually moves by >3%, retrying with B if it does not.

   This one is worth keeping in mind generally: the failing run looked like a save
   bug, and the save code was fine. The harness had just never pressed the button.

Both thresholds in that fix are numbers measured off real runs on this page, not
guesses, and they are in the source next to the code that uses them.

### One check I deliberately did **not** automate

There is no pixel gate on "the main menu gained a CONTINUE entry". Measured here,
the main menu with CONTINUE sits 3.4% away from the one without it, while the
Pikachu opening animation sits 4.1% away from it — both are mostly-white screens,
so the two are not separable by that metric, and a threshold between them would be
a coin flip dressed up as a test. The automated gate for "the game can see a save
file" is the decoded `player_name_terminated` described above, which is the game's
own test. CONTINUE appearing is confirmed **by eye** in the
`test/out/18-resume-step*.png` sequence — which step it lands on varies by run,
which is exactly why the loop is state-driven — and is corroboration, not the gate.

## 6. What was NOT tested — read this before trusting anything above

- **Only the opening ~2 minutes was played.** The harness reaches the bedroom in
  Pallet Town (`REDS_HOUSE_2F`), walks two tiles, and saves. **It has never left the
  bedroom.** It has never gone downstairs, never met Oak outside, and — note — has
  **never obtained Pikachu**, which in Yellow happens on Route 1. So nothing
  Yellow-specific has been exercised: no Pikachu following the player, no Pikachu
  mood/happiness changes in play, no Surfing Pikachu, no Yellow-specific events, no
  battles, no Pokédex, no gyms, no Kanto beyond Pallet Town, no link/trade.
- **No audio was ever enabled**, so audio output is entirely unverified. The fleet
  rules require muting; that means "sound works" is not a claim I can make.
- **Only headless Chrome 151 on Linux.** No Firefox, no Safari, no iOS, no Android,
  no touch controls. EmulatorJS's on-screen gamepad is configured but was never
  pressed.
- **The OS file dialog was not driven.** The test injects a `File` into the real
  `<input type=file>` via `DataTransfer` and dispatches a real `change` event, so the
  page's own handler runs end to end — but the native picker UI itself is untested.
  Drag-and-drop is wired to the same entry point and was likewise never exercised.
- **GitHub Pages deployment is untested.** The site was only ever served from
  `http://127.0.0.1` by `test/serve.py`. One behavioural difference to expect:
  EmulatorJS 4.2.3 only pings `cdn.emulatorjs.org/stable/data/version.json` when the
  host is localhost (`src/emulator.js:207` gates it on `this.debug || localhost`), so
  the egress guard logs exactly one refused request in the test that would **not**
  occur in production. That one refusal is whitelisted in the assertions as the guard
  working, not as a leak. It is the worst way round — invisible in production, noisy
  in the thing being graded — so it is called out rather than hidden.
- **The debug ROM and the Virtual Console patch build and match, but were never
  run.** Only `pokeyellow.gbc` was booted.
- **Save states (as opposed to the battery save) are untested.** EmulatorJS's own
  quick-save/load-state UI exists; I did not exercise it, and
  `EJS_disableDatabases = true` would affect it (it does not affect the SRAM file).
- **Two saves in one session were not tested.** Saving over an existing file takes a
  different path in `SaveMenu` (`CheckPreviousSaveFile` → "The older file will be
  erased. OK?"), which the harness never hits because it saves once on a fresh game.
- **No multi-day / clock behaviour.** Yellow has no RTC (unlike Gold/Silver), so
  there is no `.rtc` file and nothing clock-dependent to test — but I also did not
  verify that absence beyond observing that IndexedDB holds only `pokeyellow.srm`.
- **Browser storage eviction was not tested.** The save lives in IndexedDB under the
  page's origin; I did not test quota pressure, private browsing, or what happens
  when the browser evicts it.
- **The `--mash 120` default is a time budget, not a detection.** If upstream's
  opening ever got longer, the harness would start mashing A in the wrong place. It
  would fail loudly rather than pass wrongly (the save gate catches it, as it did in
  the broken run above), but it is a timing assumption and worth naming.

## 7. Layout

```
build.sh              fetch rgbds 1.0.3, clone+pin pret/pokeyellow, build, verify hashes
upstream/             the disassembly (cloned by build.sh, gitignored, never vendored)
web/                  the browser target — static, deployable as-is
  index.html          ROM picker, emulator host, save flushing, unload patch, test hooks
  no-phone-home.js    runtime egress guard (loaded first)
  THIRD_PARTY.md      EmulatorJS + gambatte licences and hashes
  data/               EmulatorJS 4.2.3 + gambatte core (vendored, GPL)
test/
  browsercheck.py     the headless check described above
  save_mechanism.py   the deterministic two-arm trap-10 test (§4)
  gen1save.py         decodes a .srm using the built ROM's own .sym, charmap and constants
  cdp.py              minimal CDP client (hand-rolled websocket; no pip on this box)
  serve.py            threaded static server + one test-only route for the ROM
  pngstat.py          stdlib PNG brightness reader (the driver-side canvas measurement)
  out/                screenshots, result.json, scratch (gitignored)
```

No ROM, and nothing extracted from one, is in `web/` — checked, not assumed: no file
under `web/` contains the ROM's header byte-run, and the whole tree is 3.9 MiB of
EmulatorJS plus two source files. `.gitignore` excludes every ROM extension
(including `*.md` for Genesis, with `STATUS.md` and the `THIRD_PARTY.md` files
negated), `*.srm`, `*.sav`, `upstream/` and `test/out/`.

## 8. Proposed catalog fragment

Not merged — `src/data/games.json` is the user's to edit. Field names follow
`~/decompfleet/shared/catalog-schema.md` (the real 41-record schema).

Three values I could not read off anything and had to pick — please check them:
`shelf` (I used `"decomp"`, matching what pokegold proposed), `embedUrl` (depends
where you host `web/`), and `complete` (see the note below the JSON).

```json
{
  "id": "pokemon-yellow",
  "title": "Pokémon Yellow",
  "aliases": [
    "pokeyellow",
    "Pokemon Yellow",
    "Pokémon Yellow Version: Special Pikachu Edition"
  ],
  "year": 1998,
  "genre": "RPG",
  "kind": "Disassembly",
  "mode": "files",
  "engine": "The real ROM, built byte-for-byte from the pret/pokeyellow disassembly, running on the libretro Gambatte core (EmulatorJS 4.2.3, WebAssembly)",
  "description": "Kanto with a Pikachu that follows you everywhere.",
  "overview": "Pokémon Yellow built from the pret/pokeyellow disassembly with rgbds 1.0.3, byte-identical to the retail cartridge, running in the browser on a WebAssembly Game Boy core. You supply your own ROM: it is read with FileReader, kept in the tab's memory and never uploaded, and the page recognises the ROM by its SHA-1. Battery saves persist in this browser.",
  "source": "https://github.com/pret/pokeyellow",
  "engineLicense": "EmulatorJS is GPL-3.0 and the Gambatte core is GPL-2.0; the pret/pokeyellow disassembly is unlicensed research. See web/THIRD_PARTY.md.",
  "assetLicense": "No game data is distributed. Everything comes from the ROM you load.",
  "assetRequirements": "You supply your own Pokémon Yellow ROM (1,048,576 bytes, sha1 cc7d03262ebfaf2f06772c1a480c7d9d5f4a38e1 — the same ROM this project's build produces). It is read in the browser and never uploaded.",
  "controls": [
    "Arrow keys — D-pad",
    "X — A",
    "Z — B",
    "Enter — Start",
    "Shift — Select",
    "Gamepad — standard mapping",
    "Touch — on-screen Game Boy pad on phones and tablets"
  ],
  "saveInstructions": "Save in game the normal way (START → SAVE). The battery save is kept in this browser's storage and survives a reload. Clearing site data removes it.",
  "limitations": [
    "Only the opening was play-tested — through Oak's speech and the naming screens to the bedroom in Pallet Town, an in-game save, a page reload and a resume. Nothing after that has been exercised, including getting Pikachu.",
    "Audio is unverified: every test ran muted.",
    "Only tested in headless Chrome on Linux. Firefox, Safari, iOS, Android and the on-screen controls are untested.",
    "EmulatorJS's own save states are untested; the battery save is the one that was verified.",
    "Link trading and battling are not possible in a single browser tab."
  ],
  "inputs": [
    "Gamepad",
    "Keyboard",
    "Touch"
  ],
  "downloadMB": null,
  "complete": true,
  "color": "#f2c744",
  "shelf": "decomp",
  "platform": "Game Boy",
  "launch": "embed",
  "touch": true,
  "embedUrl": "https://andrewnakas.github.io/pokeyellow/",
  "verification": {
    "status": "smoke-tested",
    "date": "2026-10-05",
    "browsers": ["Chrome 151 (headless, Linux)"],
    "scope": "All three upstream targets build byte-identical to upstream's documented hashes, and pokeyellow.gbc is cmp-identical to the staged dump. In the browser: loaded through the page's own file picker, ran at ~59 fps, played the opening through Oak's speech and both naming screens to the bedroom in Pallet Town, walked, saved in game, reloaded the page and resumed — the restored save reproduces the game's own CalcCheckSum and carries the player name that was typed, decoded using the symbol table of the build under test. Nothing past the bedroom was played; Pikachu was never obtained; audio was muted throughout.",
    "playthrough": false
  }
}
```

On `complete`: set to `true` because the ROM is byte-identical to the retail
cartridge — the *game* is complete by construction, there is no porting work left
undone. What is partial is my **testing**, which is why `verification.playthrough`
is `false` and the caveats are in `limitations`. If `complete` means "somebody
finished it in this runtime", set it to `false` — I did not.

## 9. Notes worth folding back into the shared docs

- `~/decompfleet/shared/rgbds-notes.md` says "pokegold and pokecrystal pin 1.0.4".
  **pokeyellow pins 1.0.3** (`.github/workflows/main.yml`, confirmed by
  `.rgbds-version`), and `rgbds-1.0.3/bin` is now installed alongside. Every GB/GBC
  agent should read its own workflow file rather than assuming the fleet default.
- `~/decompfleet/shared/emulatorjs-notes.md` trap 10 is confirmed a third time, now
  on **gambatte with a 32 KiB MBC5 SRAM save** (after pokegold on gambatte/Gen 2 and
  pokefirered on mGBA/flash). The deterministic `dispatchEvent(new Event(
  "beforeunload"))` method is the one to use; the control check matters as much as
  the comparison.
- `data/cores/reports/gambatte.json` **does exist** on the CDN
  (`cdn.emulatorjs.org/4.2.3/data/cores/reports/gambatte.json`, 200). pokegold's
  STATUS lists its 404 as a known-harmless whitelist; it can simply be vendored
  instead, which this tree does. Its contents (`"options": {}`) also confirm the
  `-legacy` WebGL1 core is the one that loads.
