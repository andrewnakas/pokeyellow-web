#!/usr/bin/env bash
# Build Pokemon Yellow from pret/pokeyellow, byte-for-byte.
#
# No sudo and no bison on this machine, so rgbds is NOT built from source the way
# pret's INSTALL.md says. gbdev publishes a statically linked Linux x86_64 binary
# tarball per release; that is what this uses.
#
# THE VERSION MATTERS AND THE REPO TELLS YOU TWO DIFFERENT THINGS:
#   rgbdscheck.asm          asserts a MINIMUM only ("v1.0.0 or newer"). Passing it
#                           does NOT imply a byte match.
#   .github/workflows/main.yml  carries the real pin, and is what pret's own CI
#                           compares against. For pokeyellow that is v1.0.3.
#                           (.rgbds-version agrees: 1.0.3.)
# Note this differs from pokegold/pokecrystal, which pin v1.0.4. Do not assume.
set -euo pipefail

PRET_REV=e89ead154b9968aa50eed9328ff2b38b6c194382   # master @ 2026-09-02
RGBDS_VER=v1.0.3                                     # from .github/workflows/main.yml
SHARED="${SHARED:-$HOME/decompfleet/shared}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM="$HERE/upstream"
RGBDS_BIN="$SHARED/rgbds-${RGBDS_VER#v}/bin"

# --- rgbds -----------------------------------------------------------------
if [ ! -x "$RGBDS_BIN/rgbasm" ]; then
  echo ">> installing rgbds $RGBDS_VER (static release tarball)"
  mkdir -p "$SHARED/dl/rgbds-$RGBDS_VER" "$RGBDS_BIN"
  if command -v gh >/dev/null; then
    gh release download "$RGBDS_VER" --repo gbdev/rgbds \
       --pattern 'rgbds-linux-x86_64.tar.xz' \
       --dir "$SHARED/dl/rgbds-$RGBDS_VER" --clobber
  else
    curl -fsSL "https://github.com/gbdev/rgbds/releases/download/$RGBDS_VER/rgbds-linux-x86_64.tar.xz" \
         -o "$SHARED/dl/rgbds-$RGBDS_VER/rgbds-linux-x86_64.tar.xz"
  fi
  tmp="$(mktemp -d)"
  tar xJf "$SHARED/dl/rgbds-$RGBDS_VER/rgbds-linux-x86_64.tar.xz" -C "$tmp"
  cp "$tmp"/{rgbasm,rgblink,rgbfix,rgbgfx} "$RGBDS_BIN/"
  chmod +x "$RGBDS_BIN"/*
  rm -rf "$tmp"
fi
export PATH="$RGBDS_BIN:$PATH"
echo ">> rgbasm: $(rgbasm --version)"
[ "$(rgbasm --version)" = "rgbasm ${RGBDS_VER}" ] || {
  echo "!! wrong rgbds on PATH; wanted ${RGBDS_VER}" >&2; exit 1; }

# --- upstream --------------------------------------------------------------
if [ ! -d "$UPSTREAM/.git" ]; then
  echo ">> cloning pret/pokeyellow"
  git clone https://github.com/pret/pokeyellow.git "$UPSTREAM"
fi
cd "$UPSTREAM"
git fetch --quiet origin
# Check out the exact pinned commit, so a re-run cannot silently drift onto a
# newer master and produce a ROM that no longer matches the recorded hashes.
git checkout --quiet "$PRET_REV"
echo ">> upstream at $(git log -1 --format='%H  %ad  %s' --date=short)"

# --- build -----------------------------------------------------------------
git clean -xfdq
# DEBUG=1 is what pret's CI uses. It only adds -E to rgbasm (export more symbols
# into the .sym), which is how test/gen1save.py can resolve sGameData and friends.
# The ROM bytes are identical either way.
make DEBUG=1 -j"$(nproc)" compare

# --- verify ----------------------------------------------------------------
echo
echo ">> hashes"
sha1sum pokeyellow.gbc pokeyellow_debug.gbc pokeyellow.patch
STAGED="${STAGED:-$HOME/decompfleet/roms/pokeyellow.gb}"
if [ -f "$STAGED" ]; then
  echo ">> against the staged dump $STAGED"
  sha1sum "$STAGED"
  if cmp -s pokeyellow.gbc "$STAGED"; then
    echo ">> BYTE-IDENTICAL to the staged ROM"
  else
    echo "!! built ROM differs from the staged ROM" >&2; exit 1
  fi
fi
