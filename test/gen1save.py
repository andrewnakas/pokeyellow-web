"""Decode a Pokemon Yellow battery save using the symbol table of the build under
test -- never a wiki memory map.

Everything here is derived at run time from files produced by / shipped with the
disassembly you just built:

  upstream/pokeyellow.sym              rgblink's symbol dump: sGameData, sPlayerName,
                                       sMainDataCheckSum, wMainDataStart, ...
  upstream/ram/sram.asm                the SRAM layout (which s* region mirrors which w*)
  upstream/constants/charmap.asm       the game's own text encoding
  upstream/constants/text_constants.asm  NAME_LENGTH, PLAYER_NAME_LENGTH
  upstream/constants/map_constants.asm   map ids, so wCurMap decodes to a name

This is what lets the browser test say "the game was actually played" rather than
"the screenshots looked like gameplay". Yellow's opening is a long scripted sequence
-- the GAME FREAK logo, the Pikachu animation, Oak's speech, and two naming
screens -- that is screenshot-indistinguishable from play. It cannot write a save
whose checksum is valid over a player name you chose.

Gen 1 differs from Gen 2 here, and in our favour: Gen 2 gates a save on two magic
constants (SAVE_CHECK_VALUE_1/_2), which are fixed bytes. Gen 1 gates it on a real
checksum over the whole save block (engine/menus/save.asm: CalcCheckSum, which is
the complement of the 8-bit sum of sGameData..sGameDataEnd, compared against
sMainDataCheckSum). Reproducing that is a much stronger statement than finding two
known bytes in the right places.
"""

import os
import re


class Symbols:
    # Each entry mirrors one `call CopyData` in SaveMainData / SavePartyAndDexData
    # (engine/menus/save.asm): an SRAM destination label and the WRAM source label
    # whose address it is an image of. Field lookups below walk these.
    REGIONS = [
        ("sPlayerName", "wPlayerName"),
        ("sMainData", "wMainDataStart"),
        ("sSpriteData", "wSpriteDataStart"),
        ("sPartyData", "wPartyDataStart"),
        ("sCurBoxData", "wBoxDataStart"),
    ]

    def __init__(self, upstream, rom="pokeyellow"):
        self.upstream = upstream
        self.sym = {}
        path = os.path.join(upstream, "%s.sym" % rom)
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith(";"):
                    continue
                parts = line.split()
                if len(parts) != 2 or ":" not in parts[0]:
                    continue
                bank, addr = parts[0].split(":")
                try:
                    self.sym[parts[1]] = (int(bank, 16), int(addr, 16))
                except ValueError:
                    continue
        for need in ("sGameData", "sGameDataEnd", "sMainDataCheckSum",
                     "sPlayerName", "sMainData", "wMainDataStart"):
            if need not in self.sym:
                raise RuntimeError("no %s in %s -- wrong or stale symbol file "
                                   "(did you `make DEBUG=1`?)" % (need, path))

        # the game's own character encoding
        self.charmap = {}
        with open(os.path.join(upstream, "constants", "charmap.asm")) as f:
            for line in f:
                m = re.match(r'\s*charmap\s+"(.*?)",\s*\$([0-9a-fA-F]+)', line)
                if m:
                    self.charmap.setdefault(int(m.group(2), 16), m.group(1))
        if self.charmap.get(0x80) != "A":
            raise RuntimeError("charmap.asm did not parse: $80 is %r, expected 'A'"
                               % self.charmap.get(0x80))
        # "@" is the terminator, per charmap.asm itself -- find it, do not assume $50
        self.terminator = next(b for b, s in self.charmap.items() if s == "@")

        # NAME_LENGTH, from the game's own constants
        self.consts = {}
        with open(os.path.join(upstream, "constants", "text_constants.asm")) as f:
            for line in f:
                m = re.match(r"\s*DEF\s+([A-Z_0-9]+)\s+EQU\s+(\d+)", line)
                if m:
                    self.consts[m.group(1)] = int(m.group(2))
        for need in ("NAME_LENGTH", "PLAYER_NAME_LENGTH"):
            if need not in self.consts:
                raise RuntimeError("%s not found in constants/text_constants.asm" % need)
        self.name_length = self.consts["NAME_LENGTH"]
        # engine/menus/naming_screen.asm: a player/rival name fills
        # PLAYER_NAME_LENGTH - 1 cells, and when they are all full the cursor is
        # forced onto "ED", so mashing A yields exactly that many characters.
        self.player_name_length = self.consts["PLAYER_NAME_LENGTH"] - 1

        # Map ids, so a decoded save can say WHERE the player is in the game's own
        # vocabulary. constants/map_constants.asm is one `const_def` (which defaults
        # to 0) followed by `map_const NAME, w, h` lines; nothing else in the file
        # consumes an index, so the id is just the 0-based position in that sequence.
        self.maps = {}
        seen_const_def = False
        idx = 0
        with open(os.path.join(upstream, "constants", "map_constants.asm")) as f:
            for line in f:
                if re.match(r"\s*const_def\b", line):
                    seen_const_def = True
                    m = re.match(r"\s*const_def\s+(-?\d+)", line)
                    idx = int(m.group(1)) if m else 0
                    continue
                m = re.match(r"\s*map_const\s+([A-Z0-9_]+)\s*,", line)
                if m and seen_const_def:
                    self.maps[idx] = m.group(1)
                    idx += 1
        if not self.maps:
            raise RuntimeError("no map ids parsed from constants/map_constants.asm")

    # ---- SRAM addressing -------------------------------------------------- #
    def srm_offset(self, name):
        """Byte offset of a symbol inside a flat 32 KiB .srm image.

        The cartridge maps an 8 KiB SRAM bank at $a000-$bfff and the .srm is the
        four banks laid end to end, so bank*0x2000 + (addr - 0xa000).
        """
        bank, addr = self.sym[name]
        if not (0xA000 <= addr <= 0xBFFF):
            raise ValueError("%s at %02x:%04x is not in SRAM" % (name, bank, addr))
        return bank * 0x2000 + (addr - 0xA000)

    def field_offset(self, wram_field):
        """Offset of a WRAM field inside the .srm, via whichever saved region
        mirrors it -- i.e. the same arithmetic save.asm does."""
        if wram_field not in self.sym:
            raise KeyError("%s is not in the symbol table" % wram_field)
        want = self.sym[wram_field][1]
        for sbase, wbase in self.REGIONS:
            if sbase not in self.sym or wbase not in self.sym:
                continue
            start = self.sym[wbase][1]
            if sbase == "sPlayerName":
                end = start + self.name_length
            else:
                end = self._region_end(wbase)
            if start <= want < end:
                return self.srm_offset(sbase) + (want - start)
        raise KeyError("%s (%04x) is not inside any saved region" % (wram_field, want))

    def _region_end(self, wbase):
        # wMainDataStart -> wMainDataEnd, wPartyDataStart -> wPartyDataEnd, ...
        end = wbase.replace("Start", "End")
        if end in self.sym:
            return self.sym[end][1]
        raise KeyError("no end symbol for %s" % wbase)

    # ---- the game's own save validity test -------------------------------- #
    def checksum(self, srm):
        """engine/menus/save.asm CalcCheckSum: complement of the 8-bit sum of
        sGameData..sGameDataEnd. Written to sMainDataCheckSum by SaveMainData."""
        a = self.srm_offset("sGameData")
        b = self.srm_offset("sGameDataEnd")
        total = 0
        for byte in srm[a:b]:
            total = (total + byte) & 0xFF
        return (~total) & 0xFF

    # ---- decoding --------------------------------------------------------- #
    def text(self, data, limit=None):
        out = ""
        for b in data[: (limit if limit is not None else len(data))]:
            if b == self.terminator:
                break
            out += self.charmap.get(b, "?")
        return out

    def read_save(self, srm):
        """Return what the game itself would consider the state of this save."""
        if len(srm) != 32768:
            return {"error": "srm is %d bytes, expected 32768 (4 x 8 KiB SRAM banks)"
                             % len(srm)}
        name_at = self.srm_offset("sPlayerName")
        rival_at = self.field_offset("wRivalName")
        id_at = self.field_offset("wPlayerID")
        map_at = self.field_offset("wCurMap")
        money_at = self.field_offset("wPlayerMoney")
        pika_at = self.field_offset("wPikachuHappiness")
        hours_at = self.field_offset("wPlayTimeHours")
        mins_at = self.field_offset("wPlayTimeMinutes")

        stored = srm[self.srm_offset("sMainDataCheckSum")]
        computed = self.checksum(srm)

        name_bytes = srm[name_at:name_at + self.name_length]
        # The main menu's own "is there a save file" test, CheckForPlayerNameInSRAM
        # (engine/menus/main_menu.asm): scan NAME_LENGTH bytes for the terminator.
        has_file = self.terminator in name_bytes

        return {
            "checksum_stored": stored,
            "checksum_computed": computed,
            "checksum_ok": stored == computed,
            # the main menu's CONTINUE gate
            "player_name_terminated": has_file,
            "player_name": self.text(name_bytes),
            "player_name_bytes": " ".join("%02x" % b for b in name_bytes),
            "rival_name": self.text(srm[rival_at:rival_at + self.name_length]),
            "player_id": (srm[id_at] << 8) | srm[id_at + 1],
            "cur_map": srm[map_at],
            "cur_map_name": self.maps.get(srm[map_at]),
            # wPlayerMoney is 3 bytes of big-endian BCD
            "money": self._bcd(srm[money_at:money_at + 3]),
            "pikachu_happiness": srm[pika_at],
            "play_time": "%d:%02d" % (srm[hours_at], srm[mins_at]),
            "offsets": {
                "sGameData": self.srm_offset("sGameData"),
                "sGameDataEnd": self.srm_offset("sGameDataEnd"),
                "sMainDataCheckSum": self.srm_offset("sMainDataCheckSum"),
                "sPlayerName": name_at,
                "wPlayerID": id_at,
                "wCurMap": map_at,
            },
        }

    @staticmethod
    def _bcd(b):
        v = 0
        for byte in b:
            v = v * 100 + (byte >> 4) * 10 + (byte & 0x0F)
        return v


if __name__ == "__main__":
    import json
    import sys

    here = os.path.dirname(os.path.abspath(__file__))
    up = os.path.join(here, "..", "upstream")
    s = Symbols(up)
    if len(sys.argv) > 1:
        with open(sys.argv[1], "rb") as f:
            print(json.dumps(s.read_save(f.read()), indent=2))
    else:
        print(json.dumps({
            "name_length": s.name_length,
            "player_name_length": s.player_name_length,
            "maps_parsed": len(s.maps),
            "map_38": s.maps.get(38),
            "terminator": "0x%02x" % s.terminator,
            "offsets": {k: "0x%04x" % s.srm_offset(k) for k in
                        ("sGameData", "sGameDataEnd", "sPlayerName", "sMainData",
                         "sMainDataCheckSum")},
            "fields": {k: "0x%04x" % s.field_offset(k) for k in
                       ("wPlayerID", "wCurMap", "wPlayerMoney", "wRivalName",
                        "wPikachuHappiness", "wPlayTimeHours")},
        }, indent=2))
