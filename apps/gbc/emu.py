# spf13 labs - Game Boy & Game Boy Color (DMG/CGB) Emulator Core
# Pure @micropython.viper (ARM Thumb-2 native emitter) for RP2350 (GitHub Universe 2026 Badge)
# Written from scratch with Gemini 4 Argon

import array
import gc
import os
import struct

try:
    import micropython
    from micropython import const
except ImportError:
    def const(x):
        return x

    class _micropython_mock:
        @staticmethod
        def viper(fn):
            return fn

    micropython = _micropython_mock()

if not hasattr(micropython, "mem_info"):
    class ptr8:
        def __init__(self, obj):
            self.b = memoryview(obj).cast("B")
        def __getitem__(self, i):
            return self.b[i]
        def __setitem__(self, i, v):
            self.b[i] = v & 0xFF

    class ptr16:
        def __init__(self, obj):
            self.b = memoryview(obj).cast("H") if isinstance(obj, (bytearray, bytes)) else obj
        def __getitem__(self, i):
            return self.b[i]
        def __setitem__(self, i, v):
            self.b[i] = v & 0xFFFF

    class ptr32:
        def __init__(self, obj):
            self.b = memoryview(obj).cast("i") if isinstance(obj, (bytearray, bytes)) else obj
        def __getitem__(self, i):
            return self.b[i]
        def __setitem__(self, i, v):
            self.b[i] = v

# State Array Indices (self.st = array.array('i', [0] * 64))
REG_PC          = const(0)
REG_SP          = const(1)
REG_F           = const(2)   # Z=0x80, N=0x40, H=0x20, C=0x10
REG_IME         = const(3)
REG_HALT        = const(4)
REG_CYCLES      = const(5)
REG_SCANLINE    = const(6)
REG_MAPPER      = const(7)   # 0=ROM, 1=MBC1, 2=MBC2, 3=MBC3, 5=MBC5
REG_ROM_BANK    = const(8)
REG_ROM_HI      = const(9)
REG_RAM_BANK    = const(10)
REG_RAM_ENABLE  = const(11)
REG_MBC1_MODE   = const(12)
REG_ROM_B1_OFF  = const(13)
REG_ROM_MASK    = const(14)
REG_ROM_MISS    = const(15)
REG_IS_CGB      = const(16)
REG_VRAM_BANK   = const(17)
REG_WRAM_BANK   = const(18)
REG_PAD_STATE   = const(19)  # bit 0=Right,1=Left,2=Up,3=Down,4=A,5=B,6=Select,7=Start
REG_DIV_CNT     = const(20)
REG_TIMA_CNT    = const(21)
REG_PAL_DIRTY   = const(22)
REG_FRAME_COUNT = const(23)
REG_SCALE_MODE  = const(24)  # 1 = 1.5x (240x216), 0 = 1:1 (160x144)
REG_ROM_B0_OFF  = const(25)
REG_WIN_LINE    = const(26)
REG_DOUBLE_SPD  = const(27)

# st[32..47] holds 4KB page offsets into self.mem for addresses 0x8000..0xEFFF:
#   st[40] = 0x8000..0x8FFF (VRAM lo)
#   st[41] = 0x9000..0x9FFF (VRAM hi)
#   st[42] = 0xA000..0xAFFF (ERAM lo)
#   st[43] = 0xB000..0xBFFF (ERAM hi)
#   st[44] = 0xC000..0xCFFF (WRAM0)
#   st[45] = 0xD000..0xDFFF (WRAM banked)
#   st[46] = 0xE000..0xEFFF (Echo WRAM0)

# Contiguous Memory Layout (self.mem = bytearray(0x14A00) = 84,480 bytes):
#   0x00000..0x01FFF : VRAM Bank 0 (8 KB)
#   0x02000..0x03FFF : VRAM Bank 1 (8 KB, CGB attributes & tile bank 1)
#   0x04000..0x0BFFF : WRAM Banks 0..7 (32 KB)
#   0x0C000..0x13FFF : Cartridge Battery SRAM Banks 0..3 (32 KB)
#   0x14000..0x1409F : OAM Sprite Attribute Table (160 B)
#   0x14100..0x141FF : IO Registers ($FF00..$FF7F) + HRAM ($FF80..$FFFE) + IE ($FFFF)
#   0x14200..0x1423F : CGB Background Palette RAM (64 B)
#   0x14240..0x1427F : CGB Sprite/OBJ Palette RAM (64 B)
#   0x14300..0x144FF : ROM Bank -> Cache Slot Lookup Table (512 B, 0xFF = uncached)
#   0x14500..0x14507 : CPU 8-bit Register File (0=B, 1=C, 2=D, 3=E, 4=H, 5=L, 6=pad, 7=A)
#   0x14600..0x149FF : Per-Scanline PPU Latch Buffer (144 * 4B: LCDC, SCY, SCX, WX)

DMG_PALETTE_RGB = (
    # Classic DMG / Pocket Gold-Green high-contrast palette for original .gb games
    (232, 248, 208),
    (136, 192, 112),
    (52, 104, 86),
    (8, 24, 32),
)


@micropython.viper
def cpu_step_scanline(st_buf, mem_buf, rom_buf) -> int:
    st = ptr32(st_buf)
    mem = ptr8(mem_buf)
    rom = ptr8(rom_buf)

    sl: int = st[REG_SCANLINE]
    cur_pc: int = st[REG_PC]
    reg_sp: int = st[REG_SP]
    reg_f: int = st[REG_F]
    ime: int = st[REG_IME]
    halted: int = st[REG_HALT]
    cyc_acc: int = st[REG_CYCLES]
    b0_off: int = st[REG_ROM_B0_OFF]
    b1_off: int = st[REG_ROM_B1_OFF]
    div_cnt: int = st[REG_DIV_CNT]
    tima_cnt: int = st[REG_TIMA_CNT]
    target_cyc: int = 128 if st[REG_DOUBLE_SPD] else 114

    while sl < 154:
        mem[0x14144] = sl
        lcdc: int = int(mem[0x14140])
        stat: int = int(mem[0x14141])
        lyc: int = int(mem[0x14145])

        # Update STAT mode & LY==LYC coincidence flag
        mode: int = 1 if sl >= 144 else 0
        coinc: int = 4 if sl == lyc else 0
        prev_stat: int = stat
        stat = (stat & 0x78) | coinc | mode | 0x80
        mem[0x14141] = stat

        if lcdc & 0x80:
            if sl == 144:
                mem[0x1410F] = int(mem[0x1410F]) | 1  # VBlank IF bit 0
                if stat & 0x10:
                    mem[0x1410F] = int(mem[0x1410F]) | 2
            if coinc and (stat & 0x40):
                mem[0x1410F] = int(mem[0x1410F]) | 2
            elif sl < 144 and (stat & 0x08):
                # Mode 0 HBlank STAT interrupt enabled
                mem[0x1410F] = int(mem[0x1410F]) | 2

        while cyc_acc < target_cyc:
            # Check interrupts only when IME or HALT is active
            if ime or halted:
                if_reg: int = int(mem[0x1410F])
                ie_reg: int = int(mem[0x141FF])
                pend: int = if_reg & ie_reg & 0x1F
                if pend:
                    halted = 0
                    if ime:
                        ime = 0
                        vec: int = 0x40
                        if pend & 1:
                            mem[0x1410F] = if_reg & 0xFE
                            vec = 0x40
                        elif pend & 2:
                            mem[0x1410F] = if_reg & 0xFD
                            vec = 0x48
                        elif pend & 4:
                            mem[0x1410F] = if_reg & 0xFB
                            vec = 0x50
                        elif pend & 8:
                            mem[0x1410F] = if_reg & 0xF7
                            vec = 0x58
                        else:
                            mem[0x1410F] = if_reg & 0xEF
                            vec = 0x60

                        reg_sp = (reg_sp - 1) & 0xFFFF
                        if reg_sp >= 0xFF80:
                            mem[0x14100 + (reg_sp & 0xFF)] = (cur_pc >> 8) & 0xFF
                        elif reg_sp >= 0x8000 and reg_sp < 0xF000:
                            mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = (cur_pc >> 8) & 0xFF
                        reg_sp = (reg_sp - 1) & 0xFFFF
                        if reg_sp >= 0xFF80:
                            mem[0x14100 + (reg_sp & 0xFF)] = cur_pc & 0xFF
                        elif reg_sp >= 0x8000 and reg_sp < 0xF000:
                            mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = cur_pc & 0xFF
                        cur_pc = vec
                        cyc_acc = cyc_acc + 5

            if halted:
                cyc_acc = target_cyc
                break

            # Fetch opcode at cur_pc
            op: int = 0
            p0: int = 0
            p1: int = 0
            if cur_pc < 0x3FFD:
                off0: int = b0_off + cur_pc
                op = int(rom[off0])
                p0 = int(rom[off0 + 1])
                p1 = int(rom[off0 + 2])
            elif cur_pc >= 0x4000 and cur_pc < 0x7FFD:
                off1: int = b1_off + (cur_pc - 0x4000)
                op = int(rom[off1])
                p0 = int(rom[off1 + 1])
                p1 = int(rom[off1 + 2])
            elif cur_pc >= 0x8000 and cur_pc < 0xEFFD:
                offm: int = st[32 + (cur_pc >> 12)] + (cur_pc & 0xFFF)
                op = int(mem[offm])
                p0 = int(mem[offm + 1])
                p1 = int(mem[offm + 2])
            elif cur_pc >= 0xFF00:
                offh: int = 0x14100 + (cur_pc & 0xFF)
                op = int(mem[offh])
                p0 = int(mem[offh + 1])
                p1 = int(mem[offh + 2])
            else:
                # Boundary-straddling fetch fallback
                for k_f in range(3):
                    ak: int = (cur_pc + k_f) & 0xFFFF
                    vk: int = 0
                    if ak < 0x8000:
                        vk = int(rom[(b1_off + (ak - 0x4000)) if ak >= 0x4000 else (b0_off + ak)])
                    elif ak < 0xF000:
                        vk = int(mem[st[32 + (ak >> 12)] + (ak & 0xFFF)])
                    elif ak >= 0xFF00:
                        vk = int(mem[0x14100 + (ak & 0xFF)])
                    else:
                        vk = int(mem[0x14000 + (ak & 0xFF)]) if ak >= 0xFE00 else int(mem[st[45] + (ak & 0xFFF)])
                    if k_f == 0: op = vk
                    elif k_f == 1: p0 = vk
                    else: p1 = vk

            cur_pc = (cur_pc + 1) & 0xFFFF
            inst_cyc: int = 1
            write_ea: int = -1
            write_val: int = 0

            x: int = op >> 6
            y: int = (op >> 3) & 7
            z: int = op & 7

            if x == 1:
                # 0x40..0x7F: LD r[y], r[z] or HALT (0x76)
                if op == 0x76:
                    halted = 1
                else:
                    vr1: int = 0
                    if z == 6:
                        hl1: int = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                        inst_cyc = 2
                        if hl1 < 0x8000:
                            vr1 = int(rom[(b1_off + (hl1 - 0x4000)) if hl1 >= 0x4000 else (b0_off + hl1)])
                        elif hl1 < 0xF000:
                            vr1 = int(mem[st[32 + (hl1 >> 12)] + (hl1 & 0xFFF)])
                        elif hl1 >= 0xFF00:
                            vr1 = int(mem[0x14100 + (hl1 & 0xFF)])
                        else:
                            vr1 = int(mem[0x14000 + (hl1 & 0xFF)]) if hl1 >= 0xFE00 else int(mem[st[45] + (hl1 & 0xFFF)])
                    else:
                        vr1 = int(mem[0x14500 + z])

                    if y == 6:
                        write_ea = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                        write_val = vr1
                        inst_cyc = 2
                    else:
                        mem[0x14500 + y] = vr1

            elif x == 2:
                # 0x80..0xBF: 8-bit ALU[y] A, r[z]
                vr2: int = 0
                if z == 6:
                    hl2: int = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                    inst_cyc = 2
                    if hl2 < 0x8000:
                        vr2 = int(rom[(b1_off + (hl2 - 0x4000)) if hl2 >= 0x4000 else (b0_off + hl2)])
                    elif hl2 < 0xF000:
                        vr2 = int(mem[st[32 + (hl2 >> 12)] + (hl2 & 0xFFF)])
                    elif hl2 >= 0xFF00:
                        vr2 = int(mem[0x14100 + (hl2 & 0xFF)])
                    else:
                        vr2 = int(mem[0x14000 + (hl2 & 0xFF)]) if hl2 >= 0xFE00 else int(mem[st[45] + (hl2 & 0xFFF)])
                else:
                    vr2 = int(mem[0x14500 + z])

                ra2: int = int(mem[0x14507])
                if y == 0:  # ADD A, r
                    res2: int = ra2 + vr2
                    reg_f = (0x80 if (res2 & 0xFF) == 0 else 0) | (0x20 if ((ra2 & 0xF) + (vr2 & 0xF)) > 15 else 0) | (0x10 if res2 > 255 else 0)
                    mem[0x14507] = res2 & 0xFF
                elif y == 1:  # ADC A, r
                    cy2: int = (reg_f >> 4) & 1
                    res2 = ra2 + vr2 + cy2
                    reg_f = (0x80 if (res2 & 0xFF) == 0 else 0) | (0x20 if ((ra2 & 0xF) + (vr2 & 0xF) + cy2) > 15 else 0) | (0x10 if res2 > 255 else 0)
                    mem[0x14507] = res2 & 0xFF
                elif y == 2:  # SUB A, r
                    res2 = (ra2 - vr2) & 0xFF
                    reg_f = 0x40 | (0x80 if res2 == 0 else 0) | (0x20 if (ra2 & 0xF) < (vr2 & 0xF) else 0) | (0x10 if ra2 < vr2 else 0)
                    mem[0x14507] = res2
                elif y == 3:  # SBC A, r
                    cy2 = (reg_f >> 4) & 1
                    res2 = ra2 - vr2 - cy2
                    reg_f = 0x40 | (0x80 if (res2 & 0xFF) == 0 else 0) | (0x20 if ((ra2 & 0xF) - (vr2 & 0xF) - cy2) < 0 else 0) | (0x10 if res2 < 0 else 0)
                    mem[0x14507] = res2 & 0xFF
                elif y == 4:  # AND A, r
                    res2 = ra2 & vr2
                    reg_f = 0x20 | (0x80 if res2 == 0 else 0)
                    mem[0x14507] = res2
                elif y == 5:  # XOR A, r
                    res2 = ra2 ^ vr2
                    reg_f = 0x80 if res2 == 0 else 0
                    mem[0x14507] = res2
                elif y == 6:  # OR A, r
                    res2 = ra2 | vr2
                    reg_f = 0x80 if res2 == 0 else 0
                    mem[0x14507] = res2
                else:         # CP A, r
                    res2 = (ra2 - vr2) & 0xFF
                    reg_f = 0x40 | (0x80 if res2 == 0 else 0) | (0x20 if (ra2 & 0xF) < (vr2 & 0xF) else 0) | (0x10 if ra2 < vr2 else 0)

            elif x == 0:
                # 0x00..0x3F
                if z == 0:
                    if y == 0:  # NOP
                        pass
                    elif y == 1:  # LD (a16), SP
                        a16_0: int = p0 | (p1 << 8)
                        cur_pc = (cur_pc + 2) & 0xFFFF
                        inst_cyc = 5
                        if a16_0 >= 0x8000 and a16_0 < 0xEFFD:
                            off_s: int = st[32 + (a16_0 >> 12)] + (a16_0 & 0xFFF)
                            mem[off_s] = reg_sp & 0xFF
                            mem[off_s + 1] = (reg_sp >> 8) & 0xFF
                        elif a16_0 >= 0xFF80:
                            mem[0x14100 + (a16_0 & 0xFF)] = reg_sp & 0xFF
                            mem[0x14100 + ((a16_0 + 1) & 0xFF)] = (reg_sp >> 8) & 0xFF
                    elif y == 2:  # STOP
                        cur_pc = (cur_pc + 1) & 0xFFFF
                        if st[REG_IS_CGB] and (int(mem[0x1414D]) & 1):
                            spd: int = st[REG_DOUBLE_SPD] ^ 1
                            st[REG_DOUBLE_SPD] = spd
                            mem[0x1414D] = (spd << 7) | 0x7E
                            target_cyc = 128 if spd else 114
                    else:  # JR d8 / JR cc, d8
                        cur_pc = (cur_pc + 1) & 0xFFFF
                        take0: int = 1
                        if y == 4: take0 = 1 if (reg_f & 0x80) == 0 else 0
                        elif y == 5: take0 = 1 if (reg_f & 0x80) != 0 else 0
                        elif y == 6: take0 = 1 if (reg_f & 0x10) == 0 else 0
                        elif y == 7: take0 = 1 if (reg_f & 0x10) != 0 else 0
                        if take0:
                            rel0: int = (p0 - 256) if p0 >= 128 else p0
                            cur_pc = (cur_pc + rel0) & 0xFFFF
                            inst_cyc = 3
                            # Fast-forward tight polling spin-loops within visible scanlines only
                            if sl < 144:
                                if rel0 == -2 and y == 3:
                                    cyc_acc = target_cyc
                                elif (rel0 == -4 or rel0 == -5) and y >= 4:
                                    top_op: int = 0
                                    if cur_pc < 0x4000:
                                        top_op = int(rom[b0_off + cur_pc])
                                    elif cur_pc < 0x8000:
                                        top_op = int(rom[b1_off + (cur_pc - 0x4000)])
                                    elif cur_pc >= 0xFF80:
                                        top_op = int(mem[0x14100 + (cur_pc & 0xFF)])
                                    if top_op == 0xF0 or top_op == 0xFA:
                                        cyc_acc = target_cyc
                        else:
                            inst_cyc = 2
                elif z == 1:
                    p_pair: int = y >> 1
                    if (y & 1) == 0:  # LD rp[p], d16
                        cur_pc = (cur_pc + 2) & 0xFFFF
                        inst_cyc = 3
                        if p_pair == 3:
                            reg_sp = p0 | (p1 << 8)
                        else:
                            mem[0x14500 + (p_pair << 1)] = p1
                            mem[0x14501 + (p_pair << 1)] = p0
                    else:  # ADD HL, rp[p]
                        inst_cyc = 2
                        hl_v: int = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                        rp_v: int = reg_sp if p_pair == 3 else ((int(mem[0x14500 + (p_pair << 1)]) << 8) | int(mem[0x14501 + (p_pair << 1)]))
                        sum16: int = hl_v + rp_v
                        reg_f = (reg_f & 0x80) | (0x20 if ((hl_v & 0xFFF) + (rp_v & 0xFFF)) > 0xFFF else 0) | (0x10 if sum16 > 0xFFFF else 0)
                        mem[0x14504] = (sum16 >> 8) & 0xFF
                        mem[0x14505] = sum16 & 0xFF
                elif z == 2:
                    inst_cyc = 2
                    a_ind: int = 0
                    if y < 2:
                        a_ind = (int(mem[0x14500]) << 8) | int(mem[0x14501])
                    elif y < 4:
                        a_ind = (int(mem[0x14502]) << 8) | int(mem[0x14503])
                    else:
                        a_ind = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                        hl_n: int = (a_ind + 1) & 0xFFFF if y < 6 else (a_ind - 1) & 0xFFFF
                        mem[0x14504] = (hl_n >> 8) & 0xFF
                        mem[0x14505] = hl_n & 0xFF
                    if (y & 1) == 0:
                        write_ea = a_ind
                        write_val = int(mem[0x14507])
                    else:
                        if a_ind < 0x8000:
                            mem[0x14507] = rom[(b1_off + (a_ind - 0x4000)) if a_ind >= 0x4000 else (b0_off + a_ind)]
                        elif a_ind < 0xF000:
                            mem[0x14507] = mem[st[32 + (a_ind >> 12)] + (a_ind & 0xFFF)]
                        elif a_ind >= 0xFF00:
                            mem[0x14507] = mem[0x14100 + (a_ind & 0xFF)]
                        else:
                            mem[0x14507] = mem[0x14000 + (a_ind & 0xFF)] if a_ind >= 0xFE00 else mem[st[45] + (a_ind & 0xFFF)]
                elif z == 3:
                    inst_cyc = 2
                    p3: int = y >> 1
                    delta3: int = -1 if (y & 1) else 1
                    if p3 == 3:
                        reg_sp = (reg_sp + delta3) & 0xFFFF
                    else:
                        v16_3: int = (((int(mem[0x14500 + (p3 << 1)]) << 8) | int(mem[0x14501 + (p3 << 1)])) + delta3) & 0xFFFF
                        mem[0x14500 + (p3 << 1)] = (v16_3 >> 8) & 0xFF
                        mem[0x14501 + (p3 << 1)] = v16_3 & 0xFF
                elif z == 4 or z == 5:
                    # INC r[y] / DEC r[y]
                    v45: int = 0
                    if y == 6:
                        inst_cyc = 3
                        hl45: int = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                        if hl45 < 0x8000:
                            v45 = int(rom[(b1_off + (hl45 - 0x4000)) if hl45 >= 0x4000 else (b0_off + hl45)])
                        elif hl45 < 0xF000:
                            v45 = int(mem[st[32 + (hl45 >> 12)] + (hl45 & 0xFFF)])
                        elif hl45 >= 0xFF00:
                            v45 = int(mem[0x14100 + (hl45 & 0xFF)])
                        else:
                            v45 = int(mem[0x14000 + (hl45 & 0xFF)]) if hl45 >= 0xFE00 else int(mem[st[45] + (hl45 & 0xFFF)])
                    else:
                        v45 = int(mem[0x14500 + y])

                    if z == 4:
                        res45: int = (v45 + 1) & 0xFF
                        reg_f = (reg_f & 0x10) | (0x80 if res45 == 0 else 0) | (0x20 if (v45 & 0xF) == 0xF else 0)
                    else:
                        res45 = (v45 - 1) & 0xFF
                        reg_f = (reg_f & 0x10) | 0x40 | (0x80 if res45 == 0 else 0) | (0x20 if (v45 & 0xF) == 0 else 0)

                    if y == 6:
                        write_ea = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                        write_val = res45
                    else:
                        mem[0x14500 + y] = res45
                elif z == 6:
                    # LD r[y], d8
                    cur_pc = (cur_pc + 1) & 0xFFFF
                    if y == 6:
                        inst_cyc = 3
                        write_ea = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                        write_val = p0
                    else:
                        inst_cyc = 2
                        mem[0x14500 + y] = p0
                else:
                    # z == 7: RLCA, RRCA, RLA, RRA, DAA, CPL, SCF, CCF
                    ra7: int = int(mem[0x14507])
                    if y == 0:  # RLCA
                        c7: int = (ra7 >> 7) & 1
                        mem[0x14507] = ((ra7 << 1) | c7) & 0xFF
                        reg_f = c7 << 4
                    elif y == 1:  # RRCA
                        c7 = ra7 & 1
                        mem[0x14507] = (ra7 >> 1) | (c7 << 7)
                        reg_f = c7 << 4
                    elif y == 2:  # RLA
                        c7 = (reg_f >> 4) & 1
                        nc7: int = (ra7 >> 7) & 1
                        mem[0x14507] = ((ra7 << 1) | c7) & 0xFF
                        reg_f = nc7 << 4
                    elif y == 3:  # RRA
                        c7 = (reg_f >> 4) & 1
                        nc7 = ra7 & 1
                        mem[0x14507] = (ra7 >> 1) | (c7 << 7)
                        reg_f = nc7 << 4
                    elif y == 4:  # DAA
                        adj: int = 0
                        fc: int = reg_f & 0x10
                        if reg_f & 0x40:
                            if reg_f & 0x20: adj = adj | 0x06
                            if fc: adj = adj | 0x60
                            ra7 = (ra7 - adj) & 0xFF
                        else:
                            if (reg_f & 0x20) or ((ra7 & 0x0F) > 9): adj = adj | 0x06
                            if fc or (ra7 > 0x99):
                                adj = adj | 0x60
                                fc = 0x10
                            ra7 = (ra7 + adj) & 0xFF
                        mem[0x14507] = ra7
                        reg_f = (reg_f & 0x40) | (0x80 if ra7 == 0 else 0) | fc
                    elif y == 5:  # CPL
                        mem[0x14507] = ra7 ^ 0xFF
                        reg_f = (reg_f & 0x90) | 0x60
                    elif y == 6:  # SCF
                        reg_f = (reg_f & 0x80) | 0x10
                    else:         # CCF
                        reg_f = (reg_f & 0x80) | ((reg_f ^ 0x10) & 0x10)

            else:
                # x == 3 (0xC0..0xFF)
                if z == 0:
                    if y < 4:  # RET cc
                        take_r: int = 0
                        if y == 0: take_r = 1 if (reg_f & 0x80) == 0 else 0
                        elif y == 1: take_r = 1 if (reg_f & 0x80) != 0 else 0
                        elif y == 2: take_r = 1 if (reg_f & 0x10) == 0 else 0
                        else: take_r = 1 if (reg_f & 0x10) != 0 else 0
                        if take_r:
                            inst_cyc = 5
                            lo_r: int = int(mem[0x14100 + (reg_sp & 0xFF)]) if reg_sp >= 0xFF00 else int(mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)])
                            reg_sp = (reg_sp + 1) & 0xFFFF
                            hi_r: int = int(mem[0x14100 + (reg_sp & 0xFF)]) if reg_sp >= 0xFF00 else int(mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)])
                            reg_sp = (reg_sp + 1) & 0xFFFF
                            cur_pc = lo_r | (hi_r << 8)
                        else:
                            inst_cyc = 2
                    elif y == 4:  # LDH (a8), A
                        cur_pc = (cur_pc + 1) & 0xFFFF
                        inst_cyc = 3
                        write_ea = 0xFF00 | p0
                        write_val = int(mem[0x14507])
                    elif y == 5:  # ADD SP, d8
                        cur_pc = (cur_pc + 1) & 0xFFFF
                        inst_cyc = 4
                        rel_sp: int = (p0 - 256) if p0 >= 128 else p0
                        reg_f = (0x20 if ((reg_sp & 0xF) + (p0 & 0xF)) > 15 else 0) | (0x10 if ((reg_sp & 0xFF) + p0) > 255 else 0)
                        reg_sp = (reg_sp + rel_sp) & 0xFFFF
                    elif y == 6:  # LDH A, (a8)
                        cur_pc = (cur_pc + 1) & 0xFFFF
                        inst_cyc = 3
                        mem[0x14507] = mem[0x14100 + p0]
                    else:  # LD HL, SP+d8
                        cur_pc = (cur_pc + 1) & 0xFFFF
                        inst_cyc = 3
                        rel_hl: int = (p0 - 256) if p0 >= 128 else p0
                        reg_f = (0x20 if ((reg_sp & 0xF) + (p0 & 0xF)) > 15 else 0) | (0x10 if ((reg_sp & 0xFF) + p0) > 255 else 0)
                        res_hl: int = (reg_sp + rel_hl) & 0xFFFF
                        mem[0x14504] = (res_hl >> 8) & 0xFF
                        mem[0x14505] = res_hl & 0xFF
                elif z == 1:
                    p_31: int = y >> 1
                    if (y & 1) == 0:  # POP rp2[p]
                        inst_cyc = 3
                        lo_p: int = 0
                        if reg_sp < 0x8000:
                            lo_p = int(rom[(b1_off + (reg_sp - 0x4000)) if reg_sp >= 0x4000 else (b0_off + reg_sp)])
                        elif reg_sp < 0xF000:
                            lo_p = int(mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)])
                        elif reg_sp >= 0xFF00:
                            lo_p = int(mem[0x14100 + (reg_sp & 0xFF)])
                        else:
                            lo_p = int(mem[st[45] + (reg_sp & 0xFFF)])
                        reg_sp = (reg_sp + 1) & 0xFFFF
                        hi_p: int = 0
                        if reg_sp < 0x8000:
                            hi_p = int(rom[(b1_off + (reg_sp - 0x4000)) if reg_sp >= 0x4000 else (b0_off + reg_sp)])
                        elif reg_sp < 0xF000:
                            hi_p = int(mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)])
                        elif reg_sp >= 0xFF00:
                            hi_p = int(mem[0x14100 + (reg_sp & 0xFF)])
                        else:
                            hi_p = int(mem[st[45] + (reg_sp & 0xFFF)])
                        reg_sp = (reg_sp + 1) & 0xFFFF
                        if p_31 == 3:
                            mem[0x14507] = hi_p
                            reg_f = lo_p & 0xF0
                        else:
                            mem[0x14500 + (p_31 << 1)] = hi_p
                            mem[0x14501 + (p_31 << 1)] = lo_p
                    else:
                        if p_31 == 0 or p_31 == 1:  # RET / RETI
                            inst_cyc = 4
                            lo_ret: int = int(mem[0x14100 + (reg_sp & 0xFF)]) if reg_sp >= 0xFF00 else int(mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)])
                            reg_sp = (reg_sp + 1) & 0xFFFF
                            hi_ret: int = int(mem[0x14100 + (reg_sp & 0xFF)]) if reg_sp >= 0xFF00 else int(mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)])
                            reg_sp = (reg_sp + 1) & 0xFFFF
                            cur_pc = lo_ret | (hi_ret << 8)
                            if p_31 == 1:
                                ime = 1
                        elif p_31 == 2:  # JP HL
                            cur_pc = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                        else:  # LD SP, HL
                            inst_cyc = 2
                            reg_sp = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                elif z == 2:
                    if y < 4:  # JP cc, a16
                        cur_pc = (cur_pc + 2) & 0xFFFF
                        take_j: int = 0
                        if y == 0: take_j = 1 if (reg_f & 0x80) == 0 else 0
                        elif y == 1: take_j = 1 if (reg_f & 0x80) != 0 else 0
                        elif y == 2: take_j = 1 if (reg_f & 0x10) == 0 else 0
                        else: take_j = 1 if (reg_f & 0x10) != 0 else 0
                        if take_j:
                            cur_pc = p0 | (p1 << 8)
                            inst_cyc = 4
                        else:
                            inst_cyc = 3
                    elif y == 4:  # LD ($FF00+C), A
                        inst_cyc = 2
                        write_ea = 0xFF00 | int(mem[0x14501])
                        write_val = int(mem[0x14507])
                    elif y == 5:  # LD (a16), A
                        cur_pc = (cur_pc + 2) & 0xFFFF
                        inst_cyc = 4
                        write_ea = p0 | (p1 << 8)
                        write_val = int(mem[0x14507])
                    elif y == 6:  # LD A, ($FF00+C)
                        inst_cyc = 2
                        mem[0x14507] = mem[0x14100 + int(mem[0x14501])]
                    else:  # LD A, (a16)
                        cur_pc = (cur_pc + 2) & 0xFFFF
                        inst_cyc = 4
                        a16_l: int = p0 | (p1 << 8)
                        if a16_l < 0x8000:
                            mem[0x14507] = rom[(b1_off + (a16_l - 0x4000)) if a16_l >= 0x4000 else (b0_off + a16_l)]
                        elif a16_l < 0xF000:
                            mem[0x14507] = mem[st[32 + (a16_l >> 12)] + (a16_l & 0xFFF)]
                        elif a16_l >= 0xFF00:
                            mem[0x14507] = mem[0x14100 + (a16_l & 0xFF)]
                        else:
                            mem[0x14507] = mem[0x14000 + (a16_l & 0xFF)] if a16_l >= 0xFE00 else mem[st[45] + (a16_l & 0xFFF)]
                elif z == 3:
                    if y == 0:  # JP a16
                        cur_pc = p0 | (p1 << 8)
                        inst_cyc = 4
                    elif y == 6:  # DI
                        ime = 0
                    elif y == 7:  # EI
                        ime = 1
                    elif y == 1:  # 0xCB Prefix!
                        cur_pc = (cur_pc + 1) & 0xFFFF
                        inst_cyc = 2
                        cb_x: int = p0 >> 6
                        cb_y: int = (p0 >> 3) & 7
                        cb_z: int = p0 & 7
                        v_cb: int = 0
                        hl_cb: int = (int(mem[0x14504]) << 8) | int(mem[0x14505])
                        if cb_z == 6:
                            inst_cyc = 3 if cb_x == 1 else 4
                            if hl_cb < 0x8000:
                                v_cb = int(rom[(b1_off + (hl_cb - 0x4000)) if hl_cb >= 0x4000 else (b0_off + hl_cb)])
                            elif hl_cb < 0xF000:
                                v_cb = int(mem[st[32 + (hl_cb >> 12)] + (hl_cb & 0xFFF)])
                            elif hl_cb >= 0xFF00:
                                v_cb = int(mem[0x14100 + (hl_cb & 0xFF)])
                            else:
                                v_cb = int(mem[0x14000 + (hl_cb & 0xFF)]) if hl_cb >= 0xFE00 else int(mem[st[45] + (hl_cb & 0xFFF)])
                        else:
                            v_cb = int(mem[0x14500 + cb_z])

                        if cb_x == 1:  # BIT cb_y, r
                            reg_f = (reg_f & 0x10) | 0x20 | (0x80 if (v_cb & (1 << cb_y)) == 0 else 0)
                        else:
                            if cb_x == 2:  # RES cb_y, r
                                v_cb = v_cb & ((1 << cb_y) ^ 0xFF)
                            elif cb_x == 3:  # SET cb_y, r
                                v_cb = v_cb | (1 << cb_y)
                            else:
                                c_cb: int = 0
                                if cb_y == 0:  # RLC
                                    c_cb = (v_cb >> 7) & 1
                                    v_cb = ((v_cb << 1) | c_cb) & 0xFF
                                elif cb_y == 1:  # RRC
                                    c_cb = v_cb & 1
                                    v_cb = (v_cb >> 1) | (c_cb << 7)
                                elif cb_y == 2:  # RL
                                    c_cb = (v_cb >> 7) & 1
                                    v_cb = ((v_cb << 1) | ((reg_f >> 4) & 1)) & 0xFF
                                elif cb_y == 3:  # RR
                                    c_cb = v_cb & 1
                                    v_cb = (v_cb >> 1) | (((reg_f >> 4) & 1) << 7)
                                elif cb_y == 4:  # SLA
                                    c_cb = (v_cb >> 7) & 1
                                    v_cb = (v_cb << 1) & 0xFF
                                elif cb_y == 5:  # SRA
                                    c_cb = v_cb & 1
                                    v_cb = (v_cb >> 1) | (v_cb & 0x80)
                                elif cb_y == 6:  # SWAP
                                    v_cb = ((v_cb & 0x0F) << 4) | (v_cb >> 4)
                                else:  # SRL
                                    c_cb = v_cb & 1
                                    v_cb = v_cb >> 1
                                reg_f = (0x80 if v_cb == 0 else 0) | (c_cb << 4)
                            if cb_z == 6:
                                write_ea = hl_cb
                                write_val = v_cb
                            else:
                                mem[0x14500 + cb_z] = v_cb
                elif z == 4:
                    # CALL cc, a16
                    cur_pc = (cur_pc + 2) & 0xFFFF
                    take_c: int = 0
                    if y == 0: take_c = 1 if (reg_f & 0x80) == 0 else 0
                    elif y == 1: take_c = 1 if (reg_f & 0x80) != 0 else 0
                    elif y == 2: take_c = 1 if (reg_f & 0x10) == 0 else 0
                    elif y == 3: take_c = 1 if (reg_f & 0x10) != 0 else 0
                    if take_c:
                        inst_cyc = 6
                        reg_sp = (reg_sp - 1) & 0xFFFF
                        if reg_sp >= 0xFF80: mem[0x14100 + (reg_sp & 0xFF)] = (cur_pc >> 8) & 0xFF
                        elif reg_sp >= 0x8000 and reg_sp < 0xF000: mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = (cur_pc >> 8) & 0xFF
                        reg_sp = (reg_sp - 1) & 0xFFFF
                        if reg_sp >= 0xFF80: mem[0x14100 + (reg_sp & 0xFF)] = cur_pc & 0xFF
                        elif reg_sp >= 0x8000 and reg_sp < 0xF000: mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = cur_pc & 0xFF
                        cur_pc = p0 | (p1 << 8)
                    else:
                        inst_cyc = 3
                elif z == 5:
                    p_35: int = y >> 1
                    if (y & 1) == 0:  # PUSH rp2[p]
                        inst_cyc = 4
                        hi_pu: int = int(mem[0x14507]) if p_35 == 3 else int(mem[0x14500 + (p_35 << 1)])
                        lo_pu: int = (reg_f & 0xF0) if p_35 == 3 else int(mem[0x14501 + (p_35 << 1)])
                        reg_sp = (reg_sp - 1) & 0xFFFF
                        if reg_sp >= 0xFF80: mem[0x14100 + (reg_sp & 0xFF)] = hi_pu
                        elif reg_sp >= 0x8000 and reg_sp < 0xF000: mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = hi_pu
                        reg_sp = (reg_sp - 1) & 0xFFFF
                        if reg_sp >= 0xFF80: mem[0x14100 + (reg_sp & 0xFF)] = lo_pu
                        elif reg_sp >= 0x8000 and reg_sp < 0xF000: mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = lo_pu
                    elif p_35 == 0:  # CALL a16
                        cur_pc = (cur_pc + 2) & 0xFFFF
                        inst_cyc = 6
                        reg_sp = (reg_sp - 1) & 0xFFFF
                        if reg_sp >= 0xFF80: mem[0x14100 + (reg_sp & 0xFF)] = (cur_pc >> 8) & 0xFF
                        elif reg_sp >= 0x8000 and reg_sp < 0xF000: mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = (cur_pc >> 8) & 0xFF
                        reg_sp = (reg_sp - 1) & 0xFFFF
                        if reg_sp >= 0xFF80: mem[0x14100 + (reg_sp & 0xFF)] = cur_pc & 0xFF
                        elif reg_sp >= 0x8000 and reg_sp < 0xF000: mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = cur_pc & 0xFF
                        cur_pc = p0 | (p1 << 8)
                elif z == 6:
                    # ALU[y] A, d8
                    cur_pc = (cur_pc + 1) & 0xFFFF
                    inst_cyc = 2
                    ra36: int = int(mem[0x14507])
                    if y == 0:
                        res36: int = ra36 + p0
                        reg_f = (0x80 if (res36 & 0xFF) == 0 else 0) | (0x20 if ((ra36 & 0xF) + (p0 & 0xF)) > 15 else 0) | (0x10 if res36 > 255 else 0)
                        mem[0x14507] = res36 & 0xFF
                    elif y == 1:
                        cy36: int = (reg_f >> 4) & 1
                        res36 = ra36 + p0 + cy36
                        reg_f = (0x80 if (res36 & 0xFF) == 0 else 0) | (0x20 if ((ra36 & 0xF) + (p0 & 0xF) + cy36) > 15 else 0) | (0x10 if res36 > 255 else 0)
                        mem[0x14507] = res36 & 0xFF
                    elif y == 2:
                        res36 = (ra36 - p0) & 0xFF
                        reg_f = 0x40 | (0x80 if res36 == 0 else 0) | (0x20 if (ra36 & 0xF) < (p0 & 0xF) else 0) | (0x10 if ra36 < p0 else 0)
                        mem[0x14507] = res36
                    elif y == 3:
                        cy36 = (reg_f >> 4) & 1
                        res36 = ra36 - p0 - cy36
                        reg_f = 0x40 | (0x80 if (res36 & 0xFF) == 0 else 0) | (0x20 if ((ra36 & 0xF) - (p0 & 0xF) - cy36) < 0 else 0) | (0x10 if res36 < 0 else 0)
                        mem[0x14507] = res36 & 0xFF
                    elif y == 4:
                        res36 = ra36 & p0
                        reg_f = 0x20 | (0x80 if res36 == 0 else 0)
                        mem[0x14507] = res36
                    elif y == 5:
                        res36 = ra36 ^ p0
                        reg_f = 0x80 if res36 == 0 else 0
                        mem[0x14507] = res36
                    elif y == 6:
                        res36 = ra36 | p0
                        reg_f = 0x80 if res36 == 0 else 0
                        mem[0x14507] = res36
                    else:
                        res36 = (ra36 - p0) & 0xFF
                        reg_f = 0x40 | (0x80 if res36 == 0 else 0) | (0x20 if (ra36 & 0xF) < (p0 & 0xF) else 0) | (0x10 if ra36 < p0 else 0)
                else:
                    # z == 7: RST y*8
                    inst_cyc = 4
                    reg_sp = (reg_sp - 1) & 0xFFFF
                    if reg_sp >= 0xFF80: mem[0x14100 + (reg_sp & 0xFF)] = (cur_pc >> 8) & 0xFF
                    elif reg_sp >= 0x8000 and reg_sp < 0xF000: mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = (cur_pc >> 8) & 0xFF
                    reg_sp = (reg_sp - 1) & 0xFFFF
                    if reg_sp >= 0xFF80: mem[0x14100 + (reg_sp & 0xFF)] = cur_pc & 0xFF
                    elif reg_sp >= 0x8000 and reg_sp < 0xF000: mem[st[32 + (reg_sp >> 12)] + (reg_sp & 0xFFF)] = cur_pc & 0xFF
                    cur_pc = y << 3

            # Commit memory write if any
            if write_ea >= 0:
                aw: int = write_ea & 0xFFFF
                vw: int = write_val & 0xFF
                if aw >= 0x8000 and aw < 0xF000:
                    mem[st[32 + (aw >> 12)] + (aw & 0xFFF)] = vw
                elif aw >= 0xFF80:
                    mem[0x14100 + (aw & 0xFF)] = vw
                elif aw >= 0xFF00:
                    rio: int = aw & 0xFF
                    mem[0x14100 + rio] = vw
                    if rio == 0x00:
                        ps: int = st[REG_PAD_STATE]
                        joy_lo: int = 0x0F
                        if (vw & 0x10) == 0:
                            joy_lo = joy_lo & ((ps & 0x0F) ^ 0x0F)
                        if (vw & 0x20) == 0:
                            joy_lo = joy_lo & (((ps >> 4) & 0x0F) ^ 0x0F)
                        mem[0x14100] = 0xC0 | (vw & 0x30) | joy_lo
                    elif rio == 0x04:
                        mem[0x14104] = 0
                        div_cnt = 0
                    elif rio == 0x26:
                        mem[0x14126] = (vw & 0x80) | 0x70
                    elif rio == 0x41:
                        mem[0x14141] = (vw & 0x78) | (int(mem[0x14141]) & 0x07) | 0x80
                    elif rio == 0x44:
                        mem[0x14144] = 0
                    elif rio == 0x45:
                        st41: int = int(mem[0x14141])
                        if vw == sl:
                            if (st41 & 0x44) == 0x40:
                                mem[0x1410F] = int(mem[0x1410F]) | 2
                            mem[0x14141] = st41 | 4
                        else:
                            mem[0x14141] = st41 & 0xFB
                    elif rio == 0x46:
                        dma_src: int = vw << 8
                        for i_oam in range(160):
                            ad_o: int = dma_src + i_oam
                            vo: int = 0
                            if ad_o < 0x8000:
                                vo = int(rom[(b1_off + (ad_o - 0x4000)) if ad_o >= 0x4000 else (b0_off + ad_o)])
                            elif ad_o < 0xF000:
                                vo = int(mem[st[32 + (ad_o >> 12)] + (ad_o & 0xFFF)])
                            else:
                                vo = int(mem[st[45] + (ad_o & 0xFFF)])
                            mem[0x14000 + i_oam] = vo
                    elif rio >= 0x47 and rio <= 0x49:
                        st[REG_PAL_DIRTY] = 1
                    elif rio == 0x4F and st[REG_IS_CGB]:
                        vb: int = vw & 1
                        st[REG_VRAM_BANK] = vb
                        st[40] = 0x2000 if vb else 0x0000
                        st[41] = 0x3000 if vb else 0x1000
                        mem[0x1414F] = 0xFE | vb
                    elif rio == 0x55 and st[REG_IS_CGB]:
                        # CGB General-Purpose / HBlank HDMA transfer
                        h_src: int = ((int(mem[0x14151]) << 8) | int(mem[0x14152])) & 0xFFF0
                        h_dst: int = ((int(mem[0x14153]) << 8) | int(mem[0x14154])) & 0x1FF0
                        h_len: int = ((vw & 0x7F) + 1) << 4
                        v_base: int = st[40]
                        for i_hd in range(h_len):
                            as_h: int = (h_src + i_hd) & 0xFFFF
                            vh: int = 0
                            if as_h < 0x8000:
                                vh = int(rom[(b1_off + (as_h - 0x4000)) if as_h >= 0x4000 else (b0_off + as_h)])
                            elif as_h < 0xF000:
                                vh = int(mem[st[32 + (as_h >> 12)] + (as_h & 0xFFF)])
                            mem[v_base + ((h_dst + i_hd) & 0x1FFF)] = vh
                        ns_h: int = (h_src + h_len) & 0xFFFF
                        nd_h: int = (h_dst + h_len) & 0x1FFF
                        mem[0x14151] = (ns_h >> 8) & 0xFF
                        mem[0x14152] = ns_h & 0xFF
                        mem[0x14153] = 0x80 | ((nd_h >> 8) & 0x1F)
                        mem[0x14154] = nd_h & 0xF0
                        mem[0x14155] = 0xFF
                    elif rio == 0x68:
                        mem[0x14169] = mem[0x14200 + (vw & 0x3F)]
                    elif rio == 0x69:
                        bi: int = int(mem[0x14168])
                        mem[0x14200 + (bi & 0x3F)] = vw
                        if bi & 0x80:
                            bi = 0x80 | ((bi + 1) & 0x3F)
                            mem[0x14168] = bi
                        mem[0x14169] = mem[0x14200 + (bi & 0x3F)]
                        st[REG_PAL_DIRTY] = 1
                    elif rio == 0x6A:
                        mem[0x1416B] = mem[0x14240 + (vw & 0x3F)]
                    elif rio == 0x6B:
                        oi: int = int(mem[0x1416A])
                        mem[0x14240 + (oi & 0x3F)] = vw
                        if oi & 0x80:
                            oi = 0x80 | ((oi + 1) & 0x3F)
                            mem[0x1416A] = oi
                        mem[0x1416B] = mem[0x14240 + (oi & 0x3F)]
                        st[REG_PAL_DIRTY] = 1
                    elif rio == 0x70 and st[REG_IS_CGB]:
                        wb: int = vw & 7
                        if wb == 0: wb = 1
                        st[REG_WRAM_BANK] = wb
                        st[45] = 0x4000 + (wb << 12)
                        mem[0x14170] = 0xF8 | wb
                elif aw >= 0xFE00 and aw < 0xFEA0:
                    mem[0x14000 + (aw & 0xFF)] = vw
                elif aw >= 0xF000 and aw < 0xFE00:
                    mem[st[45] + (aw & 0xFFF)] = vw
                elif aw < 0x8000:
                    mp: int = st[REG_MAPPER]
                    if aw < 0x2000:
                        if mp != 2 or (aw & 0x0100) == 0:
                            st[REG_RAM_ENABLE] = 1 if (vw & 0x0F) == 0x0A else 0
                    elif aw < 0x4000:
                        nb: int = st[REG_ROM_BANK]
                        if mp == 1:
                            lo5: int = vw & 0x1F
                            if lo5 == 0: lo5 = 1
                            nb = (st[REG_ROM_HI] << 5) | lo5
                        elif mp == 2:
                            if aw & 0x0100:
                                nb = vw & 0x0F
                                if nb == 0: nb = 1
                        elif mp == 3:
                            nb = vw & 0x7F
                            if nb == 0: nb = 1
                        elif mp == 5:
                            if aw < 0x3000:
                                nb = (st[REG_ROM_HI] << 8) | vw
                            else:
                                st[REG_ROM_HI] = vw & 1
                                nb = (st[REG_ROM_HI] << 8) | (nb & 0xFF)
                        nb = nb & st[REG_ROM_MASK]
                        st[REG_ROM_BANK] = nb
                        slot_b: int = int(mem[0x14300 + (nb & 0x1FF)])
                        if slot_b != 0xFF:
                            b1_off = slot_b << 14
                            st[REG_ROM_B1_OFF] = b1_off
                        else:
                            st[REG_ROM_MISS] = nb
                    elif aw < 0x6000:
                        if mp == 1:
                            st[REG_ROM_HI] = vw & 3
                            if st[REG_MBC1_MODE] == 0:
                                nb1: int = ((vw & 3) << 5) | (st[REG_ROM_BANK] & 0x1F)
                                nb1 = nb1 & st[REG_ROM_MASK]
                                st[REG_ROM_BANK] = nb1
                                sb1: int = int(mem[0x14300 + (nb1 & 0x1FF)])
                                if sb1 != 0xFF:
                                    b1_off = sb1 << 14
                                    st[REG_ROM_B1_OFF] = b1_off
                                else:
                                    st[REG_ROM_MISS] = nb1
                            else:
                                rb1: int = vw & 3
                                st[REG_RAM_BANK] = rb1
                                st[42] = 0xC000 + (rb1 << 13)
                                st[43] = st[42] + 0x1000
                        elif mp == 3 or mp == 5:
                            rb35: int = vw & 3
                            st[REG_RAM_BANK] = rb35
                            st[42] = 0xC000 + (rb35 << 13)
                            st[43] = st[42] + 0x1000
                    else:
                        if mp == 1:
                            st[REG_MBC1_MODE] = vw & 1

            # Advance instruction cycle counter
            cyc_acc = cyc_acc + inst_cyc

            if st[REG_ROM_MISS] >= 0:
                break

        if st[REG_ROM_MISS] >= 0:
            break

        # Advance DIV & TIMA timers once per scanline (114x fewer timer updates per frame)
        div_cnt = div_cnt + target_cyc
        if div_cnt >= 64:
            mem[0x14104] = (int(mem[0x14104]) + (div_cnt >> 6)) & 0xFF
            div_cnt = div_cnt & 63
        tac: int = int(mem[0x14107])
        if tac & 4:
            tima_cnt = tima_cnt + target_cyc
            sh_t: int = 8
            tsel: int = tac & 3
            if tsel == 1: sh_t = 2
            elif tsel == 2: sh_t = 4
            elif tsel == 3: sh_t = 6
            while tima_cnt >= (1 << sh_t):
                tima_cnt = tima_cnt - (1 << sh_t)
                tv: int = int(mem[0x14105]) + 1
                if tv > 255:
                    mem[0x14105] = mem[0x14106]
                    mem[0x1410F] = int(mem[0x1410F]) | 4
                else:
                    mem[0x14105] = tv

        # Snapshot per-scanline LCDC, SCY, SCX, WX, WY for visible lines 0..143
        if sl < 144:
            sl_off: int = 0x14600 + (sl << 2)
            mem[sl_off] = mem[0x14140]
            mem[sl_off + 1] = mem[0x14142]
            mem[sl_off + 2] = mem[0x14143]
            mem[sl_off + 3] = mem[0x1414B]
            mem[0x14840 + sl] = mem[0x1414A]

        cyc_acc = cyc_acc - target_cyc
        sl = sl + 1

    st[REG_PC] = cur_pc
    st[REG_SP] = reg_sp
    st[REG_F] = reg_f
    st[REG_IME] = ime
    st[REG_HALT] = halted
    st[REG_CYCLES] = cyc_acc
    st[REG_SCANLINE] = sl
    st[REG_DIV_CNT] = div_cnt
    st[REG_TIMA_CNT] = tima_cnt
    return sl

@micropython.viper
def render_bg_win_16(st: ptr32, mem: ptr8, fb: ptr16, fpal: ptr16):
    scale: int = st[REG_SCALE_MODE]
    x_base: int = 120 if scale else 80
    y_base: int = 84 if scale else 48
    is_cgb: int = st[REG_IS_CGB]
    win_line: int = 0

    for y in range(144):
        sl_off: int = 0x14600 + (y << 2)
        lcdc: int = int(mem[sl_off])
        row_dst: int = (y_base + y) * 320 + x_base
        mem[0x14900 + y] = 0xFF

        if (lcdc & 0x80) == 0:
            c_off: int = int(fpal[0])
            for x_c in range(160):
                fb[row_dst + x_c] = c_off
            continue

        scy: int = int(mem[sl_off + 1])
        scx: int = int(mem[sl_off + 2])
        wx: int = int(mem[sl_off + 3]) - 7
        wy: int = int(mem[0x14840 + y])

        tile_data_unsigned: int = 1 if (lcdc & 0x10) else 0
        bg_map_base: int = 0x1C00 if (lcdc & 0x08) else 0x1800

        # 1. Render Background scanline (tile-by-tile: 8x fewer VRAM lookups + empty-tile fast path)
        if is_cgb or (lcdc & 0x01):
            eff_y: int = (y + scy) & 0xFF
            tile_row: int = (eff_y >> 3) << 5
            fine_y: int = eff_y & 7
            x: int = 0
            while x < 160:
                eff_x: int = (x + scx) & 0xFF
                fx: int = eff_x & 7
                map_addr: int = bg_map_base + tile_row + (eff_x >> 3)
                tile_id: int = int(mem[map_addr])
                attr: int = int(mem[0x2000 + map_addr]) if is_cgb else 0

                fy: int = (7 - fine_y) if (attr & 0x40) else fine_y
                t_addr: int = 0
                if tile_data_unsigned:
                    t_addr = (tile_id << 4) + (fy << 1)
                else:
                    sig_id: int = (tile_id - 256) if tile_id >= 128 else tile_id
                    t_addr = 0x1000 + (sig_id << 4) + (fy << 1)
                if attr & 0x08:
                    t_addr = t_addr + 0x2000

                b0: int = int(mem[t_addr])
                b1: int = int(mem[t_addr + 1])
                pal_base: int = (attr & 7) << 2
                dst: int = row_dst + x

                if fx == 0 and x <= 152:
                    if (b0 | b1) == 0:
                        c_bg0: int = int(fpal[pal_base])
                        fb[dst] = c_bg0
                        fb[dst + 1] = c_bg0
                        fb[dst + 2] = c_bg0
                        fb[dst + 3] = c_bg0
                        fb[dst + 4] = c_bg0
                        fb[dst + 5] = c_bg0
                        fb[dst + 6] = c_bg0
                        fb[dst + 7] = c_bg0
                    elif (attr & 0x20) == 0:
                        fb[dst]     = fpal[pal_base | ((b0 >> 7) & 1) | ((b1 >> 6) & 2)]
                        fb[dst + 1] = fpal[pal_base | ((b0 >> 6) & 1) | ((b1 >> 5) & 2)]
                        fb[dst + 2] = fpal[pal_base | ((b0 >> 5) & 1) | ((b1 >> 4) & 2)]
                        fb[dst + 3] = fpal[pal_base | ((b0 >> 4) & 1) | ((b1 >> 3) & 2)]
                        fb[dst + 4] = fpal[pal_base | ((b0 >> 3) & 1) | ((b1 >> 2) & 2)]
                        fb[dst + 5] = fpal[pal_base | ((b0 >> 2) & 1) | ((b1 >> 1) & 2)]
                        fb[dst + 6] = fpal[pal_base | ((b0 >> 1) & 1) | (b1 & 2)]
                        fb[dst + 7] = fpal[pal_base | (b0 & 1) | ((b1 & 1) << 1)]
                    else:
                        fb[dst]     = fpal[pal_base | (b0 & 1) | ((b1 & 1) << 1)]
                        fb[dst + 1] = fpal[pal_base | ((b0 >> 1) & 1) | (b1 & 2)]
                        fb[dst + 2] = fpal[pal_base | ((b0 >> 2) & 1) | ((b1 >> 1) & 2)]
                        fb[dst + 3] = fpal[pal_base | ((b0 >> 3) & 1) | ((b1 >> 2) & 2)]
                        fb[dst + 4] = fpal[pal_base | ((b0 >> 4) & 1) | ((b1 >> 3) & 2)]
                        fb[dst + 5] = fpal[pal_base | ((b0 >> 5) & 1) | ((b1 >> 4) & 2)]
                        fb[dst + 6] = fpal[pal_base | ((b0 >> 6) & 1) | ((b1 >> 5) & 2)]
                        fb[dst + 7] = fpal[pal_base | ((b0 >> 7) & 1) | ((b1 >> 6) & 2)]
                    x = x + 8
                else:
                    rem: int = 8 - fx
                    if x + rem > 160:
                        rem = 160 - x
                    for k in range(rem):
                        bit: int = (fx + k) if (attr & 0x20) else (7 - (fx + k))
                        c_idx: int = ((b0 >> bit) & 1) | (((b1 >> bit) & 1) << 1)
                        fb[dst + k] = fpal[pal_base | c_idx]
                    x = x + rem
        else:
            c0: int = int(fpal[0])
            for x0 in range(160):
                fb[row_dst + x0] = c0

        # 2. Render Window overlay on scanline if Window enabled and active (tile-by-tile)
        if (lcdc & 0x20) and y >= wy and wx < 160:
            mem[0x14900 + y] = win_line
            win_map_base: int = 0x1C00 if (lcdc & 0x40) else 0x1800
            w_tile_row: int = (win_line >> 3) << 5
            w_fine_y: int = win_line & 7
            xw: int = wx if wx > 0 else 0

            while xw < 160:
                wx_off: int = xw - wx
                wfx: int = wx_off & 7
                w_map_addr: int = win_map_base + w_tile_row + (wx_off >> 3)
                w_tid: int = int(mem[w_map_addr])
                w_attr: int = int(mem[0x2000 + w_map_addr]) if is_cgb else 0
                w_fy: int = (7 - w_fine_y) if (w_attr & 0x40) else w_fine_y
                wt_addr: int = 0
                if tile_data_unsigned:
                    wt_addr = (w_tid << 4) + (w_fy << 1)
                else:
                    w_sig: int = (w_tid - 256) if w_tid >= 128 else w_tid
                    wt_addr = 0x1000 + (w_sig << 4) + (w_fy << 1)
                if w_attr & 0x08:
                    wt_addr = wt_addr + 0x2000

                wb0: int = int(mem[wt_addr])
                wb1: int = int(mem[wt_addr + 1])
                wpal_base: int = (w_attr & 7) << 2
                wdst: int = row_dst + xw

                if wfx == 0 and xw <= 152:
                    if (wb0 | wb1) == 0:
                        wc0: int = int(fpal[wpal_base])
                        fb[wdst] = wc0
                        fb[wdst + 1] = wc0
                        fb[wdst + 2] = wc0
                        fb[wdst + 3] = wc0
                        fb[wdst + 4] = wc0
                        fb[wdst + 5] = wc0
                        fb[wdst + 6] = wc0
                        fb[wdst + 7] = wc0
                    elif (w_attr & 0x20) == 0:
                        fb[wdst]     = fpal[wpal_base | ((wb0 >> 7) & 1) | ((wb1 >> 6) & 2)]
                        fb[wdst + 1] = fpal[wpal_base | ((wb0 >> 6) & 1) | ((wb1 >> 5) & 2)]
                        fb[wdst + 2] = fpal[wpal_base | ((wb0 >> 5) & 1) | ((wb1 >> 4) & 2)]
                        fb[wdst + 3] = fpal[wpal_base | ((wb0 >> 4) & 1) | ((wb1 >> 3) & 2)]
                        fb[wdst + 4] = fpal[wpal_base | ((wb0 >> 3) & 1) | ((wb1 >> 2) & 2)]
                        fb[wdst + 5] = fpal[wpal_base | ((wb0 >> 2) & 1) | ((wb1 >> 1) & 2)]
                        fb[wdst + 6] = fpal[wpal_base | ((wb0 >> 1) & 1) | (wb1 & 2)]
                        fb[wdst + 7] = fpal[wpal_base | (wb0 & 1) | ((wb1 & 1) << 1)]
                    else:
                        fb[wdst]     = fpal[wpal_base | (wb0 & 1) | ((wb1 & 1) << 1)]
                        fb[wdst + 1] = fpal[wpal_base | ((wb0 >> 1) & 1) | (wb1 & 2)]
                        fb[wdst + 2] = fpal[wpal_base | ((wb0 >> 2) & 1) | ((wb1 >> 1) & 2)]
                        fb[wdst + 3] = fpal[wpal_base | ((wb0 >> 3) & 1) | ((wb1 >> 2) & 2)]
                        fb[wdst + 4] = fpal[wpal_base | ((wb0 >> 4) & 1) | ((wb1 >> 3) & 2)]
                        fb[wdst + 5] = fpal[wpal_base | ((wb0 >> 5) & 1) | ((wb1 >> 4) & 2)]
                        fb[wdst + 6] = fpal[wpal_base | ((wb0 >> 6) & 1) | ((wb1 >> 5) & 2)]
                        fb[wdst + 7] = fpal[wpal_base | ((wb0 >> 7) & 1) | ((wb1 >> 6) & 2)]
                    xw = xw + 8
                else:
                    wrem: int = 8 - wfx
                    if xw + wrem > 160:
                        wrem = 160 - xw
                    for wk in range(wrem):
                        wbit: int = (wfx + wk) if (w_attr & 0x20) else (7 - (wfx + wk))
                        wc_idx: int = ((wb0 >> wbit) & 1) | (((wb1 >> wbit) & 1) << 1)
                        fb[wdst + wk] = fpal[wpal_base | wc_idx]
                    xw = xw + wrem
            win_line = win_line + 1


@micropython.viper
def render_sprites_16(st: ptr32, mem: ptr8, fb: ptr16, fpal: ptr16):
    scale: int = st[REG_SCALE_MODE]
    x_base: int = 120 if scale else 80
    y_base: int = 84 if scale else 48
    is_cgb: int = st[REG_IS_CGB]

    # Iterate 39 down to 0 via positive range(40)
    for step_i in range(40):
        idx: int = (39 - step_i) << 2
        sy: int = int(mem[0x14000 + idx]) - 16
        sx: int = int(mem[0x14001 + idx]) - 8
        if sy <= -16 or sy >= 144 or sx <= -8 or sx >= 160:
            continue

        tile: int = int(mem[0x14002 + idx])
        attr: int = int(mem[0x14003 + idx])

        pal_base: int = 32 + ((attr & 7) << 2) if is_cgb else (36 if (attr & 0x10) else 32)
        vbank_off: int = 0x2000 if (is_cgb and (attr & 0x08)) else 0
        flip_x: int = attr & 0x20
        flip_y: int = attr & 0x40
        bg_prio: int = attr & 0x80

        for row in range(16):
            py: int = sy + row
            if py < 0 or py >= 144:
                continue
            sl_off: int = 0x14600 + (py << 2)
            lcdc_y: int = int(mem[sl_off])
            if (lcdc_y & 0x82) != 0x82:
                continue
            spr_h: int = 16 if (lcdc_y & 0x04) else 8
            if row >= spr_h:
                continue
            t_use: int = (tile & 0xFE) if spr_h == 16 else tile
            r_idx: int = (spr_h - 1 - row) if flip_y else row
            t_addr: int = vbank_off + (t_use << 4) + (r_idx << 1)
            b0: int = int(mem[t_addr])
            b1: int = int(mem[t_addr + 1])
            if (b0 | b1) == 0:
                continue

            fb_row: int = (y_base + py) * 320 + x_base
            for col in range(8):
                px: int = sx + col
                if px < 0 or px >= 160:
                    continue
                bit: int = col if flip_x else (7 - col)
                c_idx: int = ((b0 >> bit) & 1) | (((b1 >> bit) & 1) << 1)
                if c_idx == 0:
                    continue
                dst: int = fb_row + px
                if (lcdc_y & 1) and (int(fb[dst]) & 0x0020):
                    if bg_prio:
                        continue
                    if is_cgb:
                        wline: int = int(mem[0x14900 + py])
                        wx_y: int = int(mem[sl_off + 3]) - 7
                        if wline != 0xFF and px >= wx_y:
                            w_attr_addr: int = (0x3C00 if (lcdc_y & 0x40) else 0x3800) + ((wline & 0xF8) << 2) + ((px - wx_y) >> 3)
                            if int(mem[w_attr_addr]) & 0x80:
                                continue
                        else:
                            scy_y: int = int(mem[sl_off + 1])
                            scx_y: int = int(mem[sl_off + 2])
                            b_attr_addr: int = (0x3C00 if (lcdc_y & 0x08) else 0x3800) + (((py + scy_y) & 0xF8) << 2) + (((px + scx_y) & 0xFF) >> 3)
                            if int(mem[b_attr_addr]) & 0x80:
                                continue
                fb[dst] = fpal[pal_base + c_idx]


@micropython.viper
def upscale_15x_16(fb: ptr16):
    # Expand 160x144 at (x=120, y=84) -> 240x216 at (x=40, y=12) in-place (multiply-free single inner loop)
    sy0: int = 84 * 320 + 120
    dy0: int = 12 * 320 + 40
    for _ in range(72):
        sy1: int = sy0 + 320
        dy1: int = dy0 + 320
        dy2: int = dy1 + 320
        sx: int = 0
        dd: int = 0
        for _ in range(80):
            p0: int = int(fb[sy0 + sx])
            p1: int = int(fb[sy0 + sx + 1])
            q0: int = int(fb[sy1 + sx])
            q1: int = int(fb[sy1 + sx + 1])
            fb[dy0 + dd] = p0
            fb[dy0 + dd + 1] = p0
            fb[dy0 + dd + 2] = p1
            fb[dy1 + dd] = p0
            fb[dy1 + dd + 1] = p0
            fb[dy1 + dd + 2] = p1
            fb[dy2 + dd] = q0
            fb[dy2 + dd + 1] = q0
            fb[dy2 + dd + 2] = q1
            sx = sx + 2
            dd = dd + 3
        sy0 = sy0 + 640
        dy0 = dy0 + 960


@micropython.viper
def apply_scanlines_gbc_16(fb: ptr16, scale_mode: int):
    x0: int = 40 if scale_mode else 80
    y0: int = 12 if scale_mode else 48
    w: int = 240 if scale_mode else 160
    h: int = 216 if scale_mode else 144
    for y in range(y0, y0 + h, 2):
        row: int = y * 320 + x0
        for x in range(w):
            idx: int = row + x
            val: int = int(fb[idx])
            fb[idx] = (val >> 1) & 0x7BEF


@micropython.viper
def update_palettes_cgb_16(st: ptr32, mem: ptr8, fpal: ptr16):
    st[REG_PAL_DIRTY] = 0
    for i in range(32):
        i2: int = i << 1
        bgr: int = int(mem[0x14200 + i2]) | (int(mem[0x14201 + i2]) << 8)
        r5: int = bgr & 0x1F
        g5: int = (bgr >> 5) & 0x1F
        b5: int = (bgr >> 10) & 0x1F
        col_bg: int = (r5 << 11) | (g5 << 6) | b5
        if i & 3:
            col_bg = col_bg | 0x0020
        fpal[i] = col_bg

        bgro: int = int(mem[0x14240 + i2]) | (int(mem[0x14241 + i2]) << 8)
        ro5: int = bgro & 0x1F
        go5: int = (bgro >> 5) & 0x1F
        bo5: int = (bgro >> 10) & 0x1F
        fpal[32 + i] = (ro5 << 11) | (go5 << 6) | bo5


class GBC:
    def __init__(self, rom_path, sram_path=None, state_path=None):
        self.rom_path = rom_path
        self.sram_path = sram_path
        self.state_path = state_path
        self.mem = bytearray(0x14A00)
        self.st = array.array("i", [0] * 64)
        self.st[REG_ROM_MISS] = -1
        self.st[REG_SCALE_MODE] = 0

        # Initialize bank_to_slot LUT to 0xFF (uncached)
        for i in range(512):
            self.mem[0x14300 + i] = 0xFF

        self.rom_fp = open(rom_path, "rb")
        try:
            self.rom_size = os.stat(rom_path)[6]
        except Exception:
            self.rom_fp.seek(0, 2)
            self.rom_size = self.rom_fp.tell()
            self.rom_fp.seek(0)
        self.num_banks = max(2, self.rom_size // 16384)
        p2 = 2
        while p2 < self.num_banks:
            p2 <<= 1
        self.st[REG_ROM_MASK] = p2 - 1

        # If PSRAM is available (up to 2 MB ROM), allocate full ROM buffer and lazy-load 16 KB banks with zero eviction
        self.direct_rom = False
        self.max_slots = 18
        self.next_slot = 2
        self.slot_to_bank = [-1] * 32
        try:
            gc.collect()
        except Exception:
            pass
        if self.rom_size <= 262144:
            try:
                self.rom = bytearray(self.rom_fp.read())
                for b in range(self.num_banks):
                    self.mem[0x14300 + b] = b
                self.max_slots = self.num_banks
                self.direct_rom = True
            except MemoryError:
                self._init_paged_rom()
        else:
            self._init_paged_rom()

        # Parse Cartridge Header (0x0134..0x014F)
        cgb_flag = self.rom[0x0143]
        ctype = self.rom[0x0147]
        self.st[REG_IS_CGB] = 1 if (cgb_flag & 0x80) else 0

        if ctype == 0x00:
            self.st[REG_MAPPER] = 0
        elif ctype in (0x01, 0x02, 0x03):
            self.st[REG_MAPPER] = 1
        elif ctype in (0x05, 0x06):
            self.st[REG_MAPPER] = 2
        elif ctype in (0x0F, 0x10, 0x11, 0x12, 0x13):
            self.st[REG_MAPPER] = 3
        else:
            self.st[REG_MAPPER] = 5

        self.has_battery = ctype in (0x03, 0x06, 0x09, 0x0D, 0x0F, 0x10, 0x13, 0x1B, 0x1E, 0xFF)
        if self.has_battery and sram_path:
            base_rom = rom_path[:-4] if rom_path.lower().endswith(".gbc") else rom_path[:-3]
            for cand in (sram_path, base_rom + ".sav"):
                try:
                    with open(cand, "rb") as sfp:
                        sdata = sfp.read(0x8000)
                        self.mem[0xC000 : 0xC000 + len(sdata)] = sdata
                        break
                except Exception:
                    pass

        self.reset_console()

    def _init_paged_rom(self):
        self.direct_rom = False
        if self.num_banks <= 254:
            try:
                gc.collect()
                self.rom = bytearray(self.num_banks * 16384)
                self.max_slots = self.num_banks
                self.direct_rom = True
            except MemoryError:
                self.direct_rom = False
        if not self.direct_rom:
            for candidate_slots in (20, 18, 16, 14, 12):
                try:
                    gc.collect()
                    self.rom = bytearray(candidate_slots * 16384)
                    self.max_slots = candidate_slots
                    break
                except MemoryError:
                    continue
        # Only pre-load Bank 0 and Bank 1 at launch (remaining banks load lazily on first access)
        self.rom_fp.seek(0)
        n_init = min(self.num_banks, 2)
        try:
            mv = memoryview(self.rom)
            self.rom_fp.readinto(mv[: n_init * 16384])
        except Exception:
            self.rom_fp.seek(0)
            chunk = self.rom_fp.read(n_init * 16384)
            self.rom[: len(chunk)] = chunk
        for b in range(n_init):
            self.mem[0x14300 + b] = b
            if not self.direct_rom:
                self.slot_to_bank[b] = b
        self.next_slot = 2

    def _page_in_bank(self, bank):
        bank = bank & self.st[REG_ROM_MASK]
        if bank >= self.num_banks:
            bank = bank % self.num_banks
        slot = self.mem[0x14300 + bank]
        if slot == 0xFF:
            if self.direct_rom:
                slot = bank
            else:
                slot = self.next_slot
                self.next_slot += 1
                if self.next_slot >= self.max_slots:
                    self.next_slot = 2
                old_b = self.slot_to_bank[slot]
                if old_b >= 0:
                    self.mem[0x14300 + old_b] = 0xFF
                self.slot_to_bank[slot] = bank
            self.rom_fp.seek(bank * 16384)
            try:
                mv = memoryview(self.rom)
                self.rom_fp.readinto(mv[slot * 16384 : (slot + 1) * 16384])
            except Exception:
                self.rom_fp.seek(bank * 16384)
                chunk = self.rom_fp.read(16384)
                self.rom[slot * 16384 : slot * 16384 + len(chunk)] = chunk
            self.mem[0x14300 + bank] = slot
        self.st[REG_ROM_B1_OFF] = slot << 14
        self.st[REG_ROM_MISS] = -1

    def reset_console(self):
        for i in range(0xC000):
            self.mem[i] = 0
        for i in range(0x14000, 0x14280):
            self.mem[i] = 0

        # Post-boot DMG / CGB CPU & IO state (skipping boot ROM directly to 0x0100)
        is_cgb = self.st[REG_IS_CGB]
        self.mem[0x14507] = 0x11 if is_cgb else 0x01  # A
        self.st[REG_F] = 0x80                         # F
        self.mem[0x14500] = 0x00                      # B
        self.mem[0x14501] = 0x00 if is_cgb else 0x13  # C
        self.mem[0x14502] = 0xFF if is_cgb else 0x00  # D
        self.mem[0x14503] = 0x56 if is_cgb else 0xD8  # E
        self.mem[0x14504] = 0x00 if is_cgb else 0x01  # H
        self.mem[0x14505] = 0x0D if is_cgb else 0x4D  # L
        self.st[REG_PC] = 0x0100
        self.st[REG_SP] = 0xFFFE
        self.st[REG_IME] = 0
        self.st[REG_HALT] = 0
        self.st[REG_CYCLES] = 0
        self.st[REG_ROM_BANK] = 1
        self.st[REG_ROM_HI] = 0
        self.st[REG_RAM_BANK] = 0
        self.st[REG_RAM_ENABLE] = 0
        self.st[REG_VRAM_BANK] = 0
        self.st[REG_WRAM_BANK] = 1
        self.st[REG_DOUBLE_SPD] = 0
        self.st[REG_ROM_B0_OFF] = 0
        self._page_in_bank(1)

        # Page table pointers into self.mem for 0x8000..0xEFFF
        self.st[40] = 0x0000  # 0x8000 VRAM lo
        self.st[41] = 0x1000  # 0x9000 VRAM hi
        self.st[42] = 0xC000  # 0xA000 ERAM lo
        self.st[43] = 0xD000  # 0xB000 ERAM hi
        self.st[44] = 0x4000  # 0xC000 WRAM0
        self.st[45] = 0x5000  # 0xD000 WRAM1
        self.st[46] = 0x4000  # 0xE000 Echo WRAM0

        # Post-boot IO registers
        self.mem[0x14100] = 0xCF  # P1
        self.mem[0x1410F] = 0xE1  # IF
        self.mem[0x14140] = 0x91  # LCDC (LCD on, BG on)
        self.mem[0x14141] = 0x85  # STAT
        self.mem[0x14147] = 0xFC  # BGP
        self.mem[0x14148] = 0xFF  # OBP0
        self.mem[0x14149] = 0xFF  # OBP1
        self.mem[0x1414D] = 0x7E  # KEY1
        self.mem[0x1414F] = 0xFE  # VBK
        self.mem[0x14155] = 0xFF  # HDMA5
        self.mem[0x14170] = 0xF9  # SVBK
        for i in range(64):
            self.mem[0x14200 + i] = 0xFF
            self.mem[0x14240 + i] = 0xFF
        for y in range(144):
            off = 0x14600 + (y << 2)
            self.mem[off] = 0x91
            self.mem[off + 1] = 0
            self.mem[off + 2] = 0
            self.mem[off + 3] = 0
            self.mem[0x14840 + y] = 0
            self.mem[0x14900 + y] = 0xFF
        self.st[REG_PAL_DIRTY] = 1

    def update_palettes(self, fpal, rgb_to_hw_fn=None):
        if self.st[REG_IS_CGB]:
            update_palettes_cgb_16(self.st, self.mem, fpal)
        else:
            self.st[REG_PAL_DIRTY] = 0
            bgp = self.mem[0x14147]
            obp0 = self.mem[0x14148]
            obp1 = self.mem[0x14149]
            for c in range(4):
                r, g, b = DMG_PALETTE_RGB[(bgp >> (c << 1)) & 3]
                col_b = ((r & 0xF8) << 8) | ((g & 0xF8) << 3) | (b >> 3)
                if c:
                    col_b |= 0x0020
                fpal[c] = col_b
                r0, g0, b0 = DMG_PALETTE_RGB[(obp0 >> (c << 1)) & 3]
                fpal[32 + c] = ((r0 & 0xF8) << 8) | ((g0 & 0xF8) << 3) | (b0 >> 3)
                r1, g1, b1 = DMG_PALETTE_RGB[(obp1 >> (c << 1)) & 3]
                fpal[36 + c] = ((r1 & 0xF8) << 8) | ((g1 & 0xF8) << 3) | (b1 >> 3)

    def step_frame(self, pad_state=0):
        st = self.st
        mem = self.mem
        rom = self.rom
        st[REG_PAD_STATE] = pad_state

        # Refresh P1 ($FF00) for current pad_state
        p1 = mem[0x14100]
        joy_lo = 0x0F
        if (p1 & 0x10) == 0:
            joy_lo &= (pad_state & 0x0F) ^ 0x0F
        if (p1 & 0x20) == 0:
            joy_lo &= ((pad_state >> 4) & 0x0F) ^ 0x0F
        mem[0x14100] = 0xC0 | (p1 & 0x30) | joy_lo

        st[REG_SCANLINE] = 0
        while st[REG_SCANLINE] < 154:
            cpu_step_scanline(st, mem, rom)
            if st[REG_ROM_MISS] >= 0:
                self._page_in_bank(st[REG_ROM_MISS])

        st[REG_FRAME_COUNT] += 1

    def render(self, fb, fpal, scanlines=False):
        render_bg_win_16(self.st, self.mem, fb, fpal)
        render_sprites_16(self.st, self.mem, fb, fpal)
        if self.st[REG_SCALE_MODE]:
            upscale_15x_16(fb)
        if scanlines:
            apply_scanlines_gbc_16(fb, self.st[REG_SCALE_MODE])

    def save_state(self, sync_sram=True):
        if not self.state_path:
            return False
        try:
            try:
                os.mkdir("/state")
            except OSError:
                pass
            mv = memoryview(self.mem)
            with open(self.state_path, "wb") as fp:
                fp.write(b"GBC2")
                fp.write(self.st)
                fp.write(mv[0x00000:0x14280])
                fp.write(mv[0x14500:0x14508])
            if sync_sram and self.has_battery and self.sram_path:
                with open(self.sram_path, "wb") as sfp:
                    sfp.write(mv[0xC000:0x14000])
            return True
        except Exception:
            return False

    def load_state(self):
        if not self.state_path:
            return False
        try:
            with open(self.state_path, "rb") as fp:
                if fp.read(4) != b"GBC2":
                    return False
                st_bytes = fp.read(256)
                if len(st_bytes) != 256:
                    return False
                vals = struct.unpack("<64i", st_bytes)
                for i in range(64):
                    self.st[i] = vals[i]
                mv = memoryview(self.mem)
                fp.readinto(mv[0x00000:0x14280])
                fp.readinto(mv[0x14500:0x14508])
                self.st[REG_ROM_B0_OFF] = 0
                self.st[REG_ROM_MISS] = -1
                self._page_in_bank(self.st[REG_ROM_BANK])
                self.st[REG_PAL_DIRTY] = 1
            return True
        except Exception:
            return False
