# spf13 labs NES Emulator - Accelerated Core
# Optimized for RP2350 (ARM Thumb-2) via @micropython.viper with desktop CPython fallback

import array
import os

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
            if hasattr(obj, "b"):
                obj = obj.b
            if isinstance(obj, memoryview):
                self.b = obj.cast("B").cast("i")
            elif isinstance(obj, (bytearray, bytes)):
                self.b = memoryview(obj).cast("i")
            else:
                self.b = obj
        def __getitem__(self, i):
            return self.b[i]
        def __setitem__(self, i, v):
            self.b[i] = v

# Register indexes (st array)
REG_A = const(0)
REG_X = const(1)
REG_Y = const(2)
REG_PC = const(3)
REG_SP = const(4)
REG_P = const(5)
REG_CYCLES = const(6)
REG_NMI_PENDING = const(7)

REG_PPU_CTRL = const(8)
REG_PPU_MASK = const(9)
REG_PPU_STATUS = const(10)
REG_OAM_ADDR = const(11)
REG_PPU_LATCH_W = const(12)
REG_PPU_V = const(13)
REG_PPU_T = const(14)
REG_PPU_FINE_X = const(15)
REG_PPU_DATA_BUF = const(16)
REG_SCROLL_X = const(17)
REG_SCROLL_Y = const(18)
REG_SPLIT_Y = const(19)
REG_SPLIT_SCROLL_X = const(20)
REG_SPLIT_NT = const(21)

REG_PAD1_STATE = const(22)
REG_PAD1_SHIFT = const(23)
REG_PAD1_STROBE = const(24)

REG_MAPPER_ID = const(25)
REG_MIRRORING = const(26)
REG_HAS_CHR_RAM = const(27)
REG_PRG_MASK = const(28)
REG_CHR_MASK = const(29)

REG_PRG_B0_OFF = const(30)
REG_PRG_B1_OFF = const(31)

REG_MMC1_SHIFT = const(32)
REG_MMC1_COUNT = const(33)
REG_MMC1_CTRL = const(34)
REG_MMC1_CHR0 = const(35)
REG_MMC1_CHR1 = const(36)
REG_MMC1_PRG = const(37)

REG_CHR_DIRTY = const(38)
REG_SCANLINE = const(39)
REG_FRAME_COUNT = const(40)
REG_TOP_SCROLL_X = const(41)
REG_TOP_SCROLL_Y = const(42)
REG_TOP_NT = const(43)
REG_SPLIT_SCROLL_Y = const(44)

# Addressing modes
AM_IMP = const(0)
AM_IMM = const(1)
AM_ZP  = const(2)
AM_ZPX = const(3)
AM_ZPY = const(4)
AM_ABS = const(5)
AM_ABX = const(6)
AM_ABY = const(7)
AM_IND = const(8)
AM_IZX = const(9)
AM_IZY = const(10)
AM_REL = const(11)

# Opcode IDs
OP_NOP = const(0)
OP_LDA = const(1)
OP_LDX = const(2)
OP_LDY = const(3)
OP_STA = const(4)
OP_STX = const(5)
OP_STY = const(6)
OP_TAX = const(7)
OP_TAY = const(8)
OP_TXA = const(9)
OP_TYA = const(10)
OP_TSX = const(11)
OP_TXS = const(12)
OP_PHA = const(13)
OP_PLA = const(14)
OP_PHP = const(15)
OP_PLP = const(16)
OP_AND = const(17)
OP_ORA = const(18)
OP_EOR = const(19)
OP_ADC = const(20)
OP_SBC = const(21)
OP_CMP = const(22)
OP_CPX = const(23)
OP_CPY = const(24)
OP_INC = const(25)
OP_DEC = const(26)
OP_INX = const(27)
OP_INY = const(28)
OP_DEX = const(29)
OP_DEY = const(30)
OP_ASL = const(31)
OP_LSR = const(32)
OP_ROL = const(33)
OP_ROR = const(34)
OP_JMP = const(35)
OP_JSR = const(36)
OP_RTS = const(37)
OP_RTI = const(38)
OP_BCC = const(39)
OP_BCS = const(40)
OP_BNE = const(41)
OP_BEQ = const(42)
OP_BPL = const(43)
OP_BMI = const(44)
OP_BVC = const(45)
OP_BVS = const(46)
OP_CLC = const(47)
OP_SEC = const(48)
OP_CLI = const(49)
OP_SEI = const(50)
OP_CLV = const(51)
OP_CLD = const(52)
OP_SED = const(53)
OP_BIT = const(54)
OP_BRK = const(55)

def _build_op_table():
    tab = bytearray(1024)
    for i in range(256):
        tab[i * 4 + 0] = OP_NOP
        tab[i * 4 + 1] = AM_IMP
        tab[i * 4 + 2] = 2
        tab[i * 4 + 3] = 0

    defs = [
        (0x69, OP_ADC, AM_IMM, 2, 0), (0x65, OP_ADC, AM_ZP, 3, 0), (0x75, OP_ADC, AM_ZPX, 4, 0),
        (0x6D, OP_ADC, AM_ABS, 4, 0), (0x7D, OP_ADC, AM_ABX, 4, 1), (0x79, OP_ADC, AM_ABY, 4, 1),
        (0x61, OP_ADC, AM_IZX, 6, 0), (0x71, OP_ADC, AM_IZY, 5, 1),
        (0x29, OP_AND, AM_IMM, 2, 0), (0x25, OP_AND, AM_ZP, 3, 0), (0x35, OP_AND, AM_ZPX, 4, 0),
        (0x2D, OP_AND, AM_ABS, 4, 0), (0x3D, OP_AND, AM_ABX, 4, 1), (0x39, OP_AND, AM_ABY, 4, 1),
        (0x21, OP_AND, AM_IZX, 6, 0), (0x31, OP_AND, AM_IZY, 5, 1),
        (0x0A, OP_ASL, AM_IMP, 2, 0), (0x06, OP_ASL, AM_ZP, 5, 0), (0x16, OP_ASL, AM_ZPX, 6, 0),
        (0x0E, OP_ASL, AM_ABS, 6, 0), (0x1E, OP_ASL, AM_ABX, 7, 0),
        (0x90, OP_BCC, AM_REL, 2, 0), (0xB0, OP_BCS, AM_REL, 2, 0), (0xF0, OP_BEQ, AM_REL, 2, 0),
        (0x24, OP_BIT, AM_ZP, 3, 0), (0x2C, OP_BIT, AM_ABS, 4, 0),
        (0x30, OP_BMI, AM_REL, 2, 0), (0xD0, OP_BNE, AM_REL, 2, 0), (0x10, OP_BPL, AM_REL, 2, 0),
        (0x00, OP_BRK, AM_IMP, 7, 0), (0x50, OP_BVC, AM_REL, 2, 0), (0x70, OP_BVS, AM_REL, 2, 0),
        (0x18, OP_CLC, AM_IMP, 2, 0), (0x38, OP_SEC, AM_IMP, 2, 0), (0x58, OP_CLI, AM_IMP, 2, 0),
        (0x78, OP_SEI, AM_IMP, 2, 0), (0xB8, OP_CLV, AM_IMP, 2, 0), (0xD8, OP_CLD, AM_IMP, 2, 0),
        (0xF8, OP_SED, AM_IMP, 2, 0),
        (0xC9, OP_CMP, AM_IMM, 2, 0), (0xC5, OP_CMP, AM_ZP, 3, 0), (0xD5, OP_CMP, AM_ZPX, 4, 0),
        (0xCD, OP_CMP, AM_ABS, 4, 0), (0xDD, OP_CMP, AM_ABX, 4, 1), (0xD9, OP_CMP, AM_ABY, 4, 1),
        (0xC1, OP_CMP, AM_IZX, 6, 0), (0xD1, OP_CMP, AM_IZY, 5, 1),
        (0xE0, OP_CPX, AM_IMM, 2, 0), (0xE4, OP_CPX, AM_ZP, 3, 0), (0xEC, OP_CPX, AM_ABS, 4, 0),
        (0xC0, OP_CPY, AM_IMM, 2, 0), (0xC4, OP_CPY, AM_ZP, 3, 0), (0xCC, OP_CPY, AM_ABS, 4, 0),
        (0xC6, OP_DEC, AM_ZP, 5, 0), (0xD6, OP_DEC, AM_ZPX, 6, 0), (0xCE, OP_DEC, AM_ABS, 6, 0),
        (0xDE, OP_DEC, AM_ABX, 7, 0), (0xCA, OP_DEX, AM_IMP, 2, 0), (0x88, OP_DEY, AM_IMP, 2, 0),
        (0x49, OP_EOR, AM_IMM, 2, 0), (0x45, OP_EOR, AM_ZP, 3, 0), (0x55, OP_EOR, AM_ZPX, 4, 0),
        (0x4D, OP_EOR, AM_ABS, 4, 0), (0x5D, OP_EOR, AM_ABX, 4, 1), (0x59, OP_EOR, AM_ABY, 4, 1),
        (0x41, OP_EOR, AM_IZX, 6, 0), (0x51, OP_EOR, AM_IZY, 5, 1),
        (0xE6, OP_INC, AM_ZP, 5, 0), (0xF6, OP_INC, AM_ZPX, 6, 0), (0xEE, OP_INC, AM_ABS, 6, 0),
        (0xFE, OP_INC, AM_ABX, 7, 0), (0xE8, OP_INX, AM_IMP, 2, 0), (0xC8, OP_INY, AM_IMP, 2, 0),
        (0x4C, OP_JMP, AM_ABS, 3, 0), (0x6C, OP_JMP, AM_IND, 5, 0), (0x20, OP_JSR, AM_ABS, 6, 0),
        (0xA9, OP_LDA, AM_IMM, 2, 0), (0xA5, OP_LDA, AM_ZP, 3, 0), (0xB5, OP_LDA, AM_ZPX, 4, 0),
        (0xAD, OP_LDA, AM_ABS, 4, 0), (0xBD, OP_LDA, AM_ABX, 4, 1), (0xB9, OP_LDA, AM_ABY, 4, 1),
        (0xA1, OP_LDA, AM_IZX, 6, 0), (0xB1, OP_LDA, AM_IZY, 5, 1),
        (0xA2, OP_LDX, AM_IMM, 2, 0), (0xA6, OP_LDX, AM_ZP, 3, 0), (0xB6, OP_LDX, AM_ZPY, 4, 0),
        (0xAE, OP_LDX, AM_ABS, 4, 0), (0xBE, OP_LDX, AM_ABY, 4, 1),
        (0xA0, OP_LDY, AM_IMM, 2, 0), (0xA4, OP_LDY, AM_ZP, 3, 0), (0xB4, OP_LDY, AM_ZPX, 4, 0),
        (0xAC, OP_LDY, AM_ABS, 4, 0), (0xBC, OP_LDY, AM_ABX, 4, 1),
        (0x4A, OP_LSR, AM_IMP, 2, 0), (0x46, OP_LSR, AM_ZP, 5, 0), (0x56, OP_LSR, AM_ZPX, 6, 0),
        (0x4E, OP_LSR, AM_ABS, 6, 0), (0x5E, OP_LSR, AM_ABX, 7, 0),
        (0xEA, OP_NOP, AM_IMP, 2, 0),
        (0x09, OP_ORA, AM_IMM, 2, 0), (0x05, OP_ORA, AM_ZP, 3, 0), (0x15, OP_ORA, AM_ZPX, 4, 0),
        (0x0D, OP_ORA, AM_ABS, 4, 0), (0x1D, OP_ORA, AM_ABX, 4, 1), (0x19, OP_ORA, AM_ABY, 4, 1),
        (0x01, OP_ORA, AM_IZX, 6, 0), (0x11, OP_ORA, AM_IZY, 5, 1),
        (0x48, OP_PHA, AM_IMP, 3, 0), (0x08, OP_PHP, AM_IMP, 3, 0),
        (0x68, OP_PLA, AM_IMP, 4, 0), (0x28, OP_PLP, AM_IMP, 4, 0),
        (0x2A, OP_ROL, AM_IMP, 2, 0), (0x26, OP_ROL, AM_ZP, 5, 0), (0x36, OP_ROL, AM_ZPX, 6, 0),
        (0x2E, OP_ROL, AM_ABS, 6, 0), (0x3E, OP_ROL, AM_ABX, 7, 0),
        (0x6A, OP_ROR, AM_IMP, 2, 0), (0x66, OP_ROR, AM_ZP, 5, 0), (0x76, OP_ROR, AM_ZPX, 6, 0),
        (0x6E, OP_ROR, AM_ABS, 6, 0), (0x7E, OP_ROR, AM_ABX, 7, 0),
        (0x40, OP_RTI, AM_IMP, 6, 0), (0x60, OP_RTS, AM_IMP, 6, 0),
        (0xE9, OP_SBC, AM_IMM, 2, 0), (0xEB, OP_SBC, AM_IMM, 2, 0),
        (0xE5, OP_SBC, AM_ZP, 3, 0), (0xF5, OP_SBC, AM_ZPX, 4, 0),
        (0xED, OP_SBC, AM_ABS, 4, 0), (0xFD, OP_SBC, AM_ABX, 4, 1), (0xF9, OP_SBC, AM_ABY, 4, 1),
        (0xE1, OP_SBC, AM_IZX, 6, 0), (0xF1, OP_SBC, AM_IZY, 5, 1),
        (0x85, OP_STA, AM_ZP, 3, 0), (0x95, OP_STA, AM_ZPX, 4, 0),
        (0x8D, OP_STA, AM_ABS, 4, 0), (0x9D, OP_STA, AM_ABX, 5, 0), (0x99, OP_STA, AM_ABY, 5, 0),
        (0x81, OP_STA, AM_IZX, 6, 0), (0x91, OP_STA, AM_IZY, 6, 0),
        (0x86, OP_STX, AM_ZP, 3, 0), (0x96, OP_STX, AM_ZPY, 4, 0), (0x8E, OP_STX, AM_ABS, 4, 0),
        (0x84, OP_STY, AM_ZP, 3, 0), (0x94, OP_STY, AM_ZPX, 4, 0), (0x8C, OP_STY, AM_ABS, 4, 0),
        (0xAA, OP_TAX, AM_IMP, 2, 0), (0xA8, OP_TAY, AM_IMP, 2, 0),
        (0xBA, OP_TSX, AM_IMP, 2, 0), (0x8A, OP_TXA, AM_IMP, 2, 0),
        (0x9A, OP_TXS, AM_IMP, 2, 0), (0x98, OP_TYA, AM_IMP, 2, 0),
        # Unofficial NOPs
        (0x1A, OP_NOP, AM_IMP, 2, 0), (0x3A, OP_NOP, AM_IMP, 2, 0), (0x5A, OP_NOP, AM_IMP, 2, 0),
        (0x7A, OP_NOP, AM_IMP, 2, 0), (0xDA, OP_NOP, AM_IMP, 2, 0), (0xFA, OP_NOP, AM_IMP, 2, 0),
        (0x04, OP_NOP, AM_ZP, 3, 0), (0x44, OP_NOP, AM_ZP, 3, 0), (0x64, OP_NOP, AM_ZP, 3, 0),
        (0x14, OP_NOP, AM_ZPX, 4, 0), (0x34, OP_NOP, AM_ZPX, 4, 0), (0x54, OP_NOP, AM_ZPX, 4, 0),
        (0x74, OP_NOP, AM_ZPX, 4, 0), (0xD4, OP_NOP, AM_ZPX, 4, 0), (0xF4, OP_NOP, AM_ZPX, 4, 0),
        (0x0C, OP_NOP, AM_ABS, 4, 0), (0x1C, OP_NOP, AM_ABX, 4, 1), (0x3C, OP_NOP, AM_ABX, 4, 1),
        (0x5C, OP_NOP, AM_ABX, 4, 1), (0x7C, OP_NOP, AM_ABX, 4, 1), (0xDC, OP_NOP, AM_ABX, 4, 1),
        (0xFC, OP_NOP, AM_ABX, 4, 1),
        (0x80, OP_NOP, AM_IMM, 2, 0), (0x82, OP_NOP, AM_IMM, 2, 0), (0x89, OP_NOP, AM_IMM, 2, 0),
        (0xC2, OP_NOP, AM_IMM, 2, 0), (0xE2, OP_NOP, AM_IMM, 2, 0),
    ]
    for op, oid, am, cyc, pc in defs:
        tab[op * 4 + 0] = oid
        tab[op * 4 + 1] = am
        tab[op * 4 + 2] = cyc
        tab[op * 4 + 3] = pc
    return tab

OP_TABLE = _build_op_table()

NES_PALETTE = (
    (0x7C, 0x7C, 0x7C), (0x00, 0x00, 0xFC), (0x00, 0x00, 0xBC), (0x44, 0x28, 0xBC),
    (0x94, 0x00, 0x84), (0xA8, 0x00, 0x20), (0xA8, 0x10, 0x00), (0x88, 0x14, 0x00),
    (0x50, 0x30, 0x00), (0x00, 0x78, 0x00), (0x00, 0x68, 0x00), (0x00, 0x58, 0x00),
    (0x00, 0x40, 0x58), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00),
    (0xBC, 0xBC, 0xBC), (0x00, 0x78, 0xF8), (0x00, 0x58, 0xF8), (0x68, 0x44, 0xFC),
    (0xD8, 0x00, 0xCC), (0xE4, 0x00, 0x58), (0xF8, 0x38, 0x00), (0xE4, 0x5C, 0x10),
    (0xAC, 0x7C, 0x00), (0x00, 0xB8, 0x00), (0x00, 0xA8, 0x00), (0x00, 0xA8, 0x44),
    (0x00, 0x88, 0x88), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00),
    (0xF8, 0xF8, 0xF8), (0x3C, 0xBC, 0xFC), (0x68, 0x88, 0xFC), (0x98, 0x78, 0xF8),
    (0xF8, 0x78, 0xF8), (0xF8, 0x58, 0x98), (0xF8, 0x78, 0x58), (0xFC, 0xA0, 0x44),
    (0xF8, 0xB8, 0x00), (0xB8, 0xF8, 0x18), (0x58, 0xD8, 0x54), (0x58, 0xF8, 0x98),
    (0x00, 0xE8, 0xD8), (0x78, 0x78, 0x78), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00),
    (0xFC, 0xFC, 0xFC), (0xA4, 0xE4, 0xFC), (0xB8, 0xB8, 0xF8), (0xD8, 0xB8, 0xF8),
    (0xF8, 0xB8, 0xF8), (0xF8, 0xA4, 0xC0), (0xF0, 0xD0, 0xB0), (0xFC, 0xE0, 0xA8),
    (0xF8, 0xD8, 0x78), (0xD8, 0xF8, 0x78), (0xB8, 0xF8, 0xB8), (0xB8, 0xF8, 0xD8),
    (0x00, 0xFC, 0xFC), (0xF8, 0xD8, 0xF8), (0x00, 0x00, 0x00), (0x00, 0x00, 0x00),
)

@micropython.viper
def cpu_step_scanline(st_buf, mem_buf, prg_buf, op_tab_buf) -> int:
    st = ptr32(st_buf)
    mem = ptr8(mem_buf)
    prg = ptr8(prg_buf)
    op_tab = ptr8(op_tab_buf)

    sl: int = st[REG_SCANLINE]
    target_cycles: int = 113

    # Cache hot CPU registers in local ARM registers
    reg_a: int = st[REG_A]
    reg_x: int = st[REG_X]
    reg_y: int = st[REG_Y]
    cur_pc: int = st[REG_PC]
    reg_sp: int = st[REG_SP]
    reg_p: int = st[REG_P]
    cyc_acc: int = st[REG_CYCLES]
    b0_off: int = st[REG_PRG_B0_OFF]
    b1_off: int = st[REG_PRG_B1_OFF]

    while sl < 262:
        if sl == 0:
            st[REG_SPLIT_Y] = -1
        if sl < 240:
            s0_y: int = int(mem[0x0800]) + 1
            if sl >= s0_y and sl < s0_y + 8:
                if (st[REG_PPU_MASK] & 0x18) == 0x18:
                    st[REG_PPU_STATUS] = st[REG_PPU_STATUS] | 0x40
        elif sl == 241:
            st[REG_PPU_STATUS] = st[REG_PPU_STATUS] | 0x80
            if st[REG_PPU_CTRL] & 0x80:
                st[REG_NMI_PENDING] = 1
        elif sl == 261:
            st[REG_PPU_STATUS] = 0

        if st[REG_NMI_PENDING]:
            mem[0x0100 + reg_sp] = (cur_pc >> 8) & 0xFF
            reg_sp = (reg_sp - 1) & 0xFF
            mem[0x0100 + reg_sp] = cur_pc & 0xFF
            reg_sp = (reg_sp - 1) & 0xFF
            mem[0x0100 + reg_sp] = (reg_p & 0xEF) | 0x20
            reg_sp = (reg_sp - 1) & 0xFF
            reg_p = reg_p | 0x04
            st[REG_NMI_PENDING] = 0
            lo_nmi: int = int(prg[b1_off + 0x3FFA])
            hi_nmi: int = int(prg[b1_off + 0x3FFB])
            cur_pc = (hi_nmi << 8) | lo_nmi
            cyc_acc = cyc_acc + 7

        while cyc_acc < target_cycles:
            op: int = 0
            op_b0: int = 0
            op_b1: int = 0
            npc: int = (cur_pc + 1) & 0xFFFF
            if cur_pc >= 0xC000:
                pc_off: int = b1_off + (cur_pc - 0xC000)
                op = int(prg[pc_off])
                op_b0 = int(prg[pc_off + 1])
                op_b1 = int(prg[pc_off + 2])
            elif cur_pc >= 0x8000 and cur_pc < 0xBFFE:
                pc_off2: int = b0_off + (cur_pc - 0x8000)
                op = int(prg[pc_off2])
                op_b0 = int(prg[pc_off2 + 1])
                op_b1 = int(prg[pc_off2 + 2])
            else:
                if cur_pc >= 0x8000:
                    op = int(prg[b0_off + (cur_pc - 0x8000)])
                elif cur_pc < 0x2000:
                    op = int(mem[cur_pc & 0x07FF])
                elif cur_pc >= 0x6000:
                    op = int(mem[0x4000 + (cur_pc - 0x6000)])
                if npc >= 0xC000:
                    op_b0 = int(prg[b1_off + (npc - 0xC000)])
                elif npc >= 0x8000:
                    op_b0 = int(prg[b0_off + (npc - 0x8000)])
                elif npc < 0x2000:
                    op_b0 = int(mem[npc & 0x07FF])
                elif npc >= 0x6000:
                    op_b0 = int(mem[0x4000 + (npc - 0x6000)])
                npc2: int = (npc + 1) & 0xFFFF
                if npc2 >= 0xC000:
                    op_b1 = int(prg[b1_off + (npc2 - 0xC000)])
                elif npc2 >= 0x8000:
                    op_b1 = int(prg[b0_off + (npc2 - 0x8000)])
                elif npc2 < 0x2000:
                    op_b1 = int(mem[npc2 & 0x07FF])
                elif npc2 >= 0x6000:
                    op_b1 = int(mem[0x4000 + (npc2 - 0x6000)])

            tab_off: int = op << 2
            op_id: int = int(op_tab[tab_off])
            am: int = int(op_tab[tab_off + 1])
            cyc_acc = cyc_acc + int(op_tab[tab_off + 2])
            pcross: int = int(op_tab[tab_off + 3])

            ea: int = 0
            rel: int = 0

            if am <= 4:
                if am == 1:
                    ea = npc
                    cur_pc = (npc + 1) & 0xFFFF
                elif am == 2:
                    ea = op_b0
                    cur_pc = (npc + 1) & 0xFFFF
                elif am == 3:
                    ea = (op_b0 + reg_x) & 0xFF
                    cur_pc = (npc + 1) & 0xFFFF
                elif am == 4:
                    ea = (op_b0 + reg_y) & 0xFF
                    cur_pc = (npc + 1) & 0xFFFF
                else:
                    cur_pc = npc
            else:
                if am == 5:
                    ea = (op_b1 << 8) | op_b0
                    cur_pc = (npc + 2) & 0xFFFF
                elif am == 6:
                    base6: int = (op_b1 << 8) | op_b0
                    ea = (base6 + reg_x) & 0xFFFF
                    if pcross and ((base6 ^ ea) & 0xFF00):
                        cyc_acc = cyc_acc + 1
                    cur_pc = (npc + 2) & 0xFFFF
                elif am == 7:
                    base7: int = (op_b1 << 8) | op_b0
                    ea = (base7 + reg_y) & 0xFFFF
                    if pcross and ((base7 ^ ea) & 0xFF00):
                        cyc_acc = cyc_acc + 1
                    cur_pc = (npc + 2) & 0xFFFF
                elif am == 8:
                    ptr: int = (op_b1 << 8) | op_b0
                    ptr2: int = (ptr & 0xFF00) | ((ptr + 1) & 0xFF)
                    ea_lo: int = 0
                    if ptr >= 0xC000:
                        ea_lo = int(prg[b1_off + (ptr - 0xC000)])
                    elif ptr >= 0x8000:
                        ea_lo = int(prg[b0_off + (ptr - 0x8000)])
                    elif ptr < 0x2000:
                        ea_lo = int(mem[ptr & 0x07FF])
                    elif ptr >= 0x6000:
                        ea_lo = int(mem[0x4000 + (ptr - 0x6000)])
                    ea_hi: int = 0
                    if ptr2 >= 0xC000:
                        ea_hi = int(prg[b1_off + (ptr2 - 0xC000)])
                    elif ptr2 >= 0x8000:
                        ea_hi = int(prg[b0_off + (ptr2 - 0x8000)])
                    elif ptr2 < 0x2000:
                        ea_hi = int(mem[ptr2 & 0x07FF])
                    elif ptr2 >= 0x6000:
                        ea_hi = int(mem[0x4000 + (ptr2 - 0x6000)])
                    ea = (ea_hi << 8) | ea_lo
                    cur_pc = (npc + 2) & 0xFFFF
                elif am == 9:
                    zp9: int = (op_b0 + reg_x) & 0xFF
                    ea = int(mem[zp9]) | (int(mem[(zp9 + 1) & 0xFF]) << 8)
                    cur_pc = (npc + 1) & 0xFFFF
                elif am == 10:
                    zp10: int = op_b0
                    base10: int = int(mem[zp10]) | (int(mem[(zp10 + 1) & 0xFF]) << 8)
                    ea = (base10 + reg_y) & 0xFFFF
                    if pcross and ((base10 ^ ea) & 0xFF00):
                        cyc_acc = cyc_acc + 1
                    cur_pc = (npc + 1) & 0xFFFF
                elif am == 11:
                    rel = op_b0
                    if rel >= 128:
                        rel = rel - 256
                    cur_pc = (npc + 1) & 0xFFFF

            val: int = 0
            if (op_id >= 1 and op_id <= 3) or (op_id >= 17 and op_id <= 26) or (op_id >= 31 and op_id <= 34) or op_id == 54:
                if am == 0:
                    val = reg_a
                elif am == 1:
                    val = op_b0
                elif ea < 0x2000:
                    val = int(mem[ea & 0x07FF])
                elif ea >= 0x8000:
                    if ea >= 0xC000:
                        val = int(prg[b1_off + (ea - 0xC000)])
                    else:
                        val = int(prg[b0_off + (ea - 0x8000)])
                elif ea < 0x4000:
                    reg: int = ea & 7
                    if reg == 2:
                        val = st[REG_PPU_STATUS]
                        st[REG_PPU_STATUS] = val & 0x7F
                        st[REG_PPU_LATCH_W] = 0
                    elif reg == 4:
                        val = int(mem[0x0800 + (st[REG_OAM_ADDR] & 0xFF)])
                    elif reg == 7:
                        v: int = st[REG_PPU_V] & 0x3FFF
                        val = st[REG_PPU_DATA_BUF]
                        nxt: int = 0
                        if v < 0x2000:
                            nxt = int(mem[0x2000 + v])
                        elif v < 0x3F00:
                            nt: int = 0
                            mir: int = st[REG_MIRRORING]
                            if mir == 0:
                                nt = (v >> 11) & 1
                            elif mir == 1:
                                nt = (v >> 10) & 1
                            elif mir == 3:
                                nt = 1
                            nxt = int(mem[0x1000 + (nt << 10) + (v & 0x3FF)])
                        else:
                            p_off: int = v & 0x1F
                            if (p_off & 0x13) == 0x10:
                                p_off = p_off & 0x0F
                            val = int(mem[0x0900 + p_off])
                            nxt = int(mem[0x1000 + (v & 0x3FF)])
                        st[REG_PPU_DATA_BUF] = nxt
                        step: int = 32 if (st[REG_PPU_CTRL] & 4) else 1
                        st[REG_PPU_V] = (st[REG_PPU_V] + step) & 0x7FFF
                elif ea == 0x4016:
                    val = (st[REG_PAD1_SHIFT] & 1) | 0x40
                    if (st[REG_PAD1_STROBE] & 1) == 0:
                        st[REG_PAD1_SHIFT] = (st[REG_PAD1_SHIFT] >> 1) | 0x80
                elif ea >= 0x6000:
                    val = int(mem[0x4000 + (ea - 0x6000)])

            write_val: int = -1

            if op_id < 27:
                if op_id < 13:
                    if op_id < 7:
                        if op_id == 1:
                            reg_a = val
                            reg_p = (reg_p & 0x7D) | (0x02 if val == 0 else 0) | (val & 0x80)
                        elif op_id == 2:
                            reg_x = val
                            reg_p = (reg_p & 0x7D) | (0x02 if val == 0 else 0) | (val & 0x80)
                        elif op_id == 3:
                            reg_y = val
                            reg_p = (reg_p & 0x7D) | (0x02 if val == 0 else 0) | (val & 0x80)
                        elif op_id == 4:
                            write_val = reg_a
                        elif op_id == 5:
                            write_val = reg_x
                        elif op_id == 6:
                            write_val = reg_y
                    else:
                        if op_id == 7:
                            reg_x = reg_a
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_x == 0 else 0) | (reg_x & 0x80)
                        elif op_id == 8:
                            reg_y = reg_a
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_y == 0 else 0) | (reg_y & 0x80)
                        elif op_id == 9:
                            reg_a = reg_x
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_a == 0 else 0) | (reg_a & 0x80)
                        elif op_id == 10:
                            reg_a = reg_y
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_a == 0 else 0) | (reg_a & 0x80)
                        elif op_id == 11:
                            reg_x = reg_sp
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_x == 0 else 0) | (reg_x & 0x80)
                        elif op_id == 12:
                            reg_sp = reg_x
                else:
                    if op_id < 20:
                        if op_id == 13:
                            mem[0x0100 + reg_sp] = reg_a
                            reg_sp = (reg_sp - 1) & 0xFF
                        elif op_id == 14:
                            reg_sp = (reg_sp + 1) & 0xFF
                            reg_a = int(mem[0x0100 + reg_sp])
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_a == 0 else 0) | (reg_a & 0x80)
                        elif op_id == 15:
                            mem[0x0100 + reg_sp] = reg_p | 0x30
                            reg_sp = (reg_sp - 1) & 0xFF
                        elif op_id == 16:
                            reg_sp = (reg_sp + 1) & 0xFF
                            reg_p = (int(mem[0x0100 + reg_sp]) | 0x20) & 0xEF
                        elif op_id == 17:
                            reg_a = reg_a & val
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_a == 0 else 0) | (reg_a & 0x80)
                        elif op_id == 18:
                            reg_a = reg_a | val
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_a == 0 else 0) | (reg_a & 0x80)
                        elif op_id == 19:
                            reg_a = reg_a ^ val
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_a == 0 else 0) | (reg_a & 0x80)
                    else:
                        if op_id == 20:
                            c_val: int = reg_p & 1
                            res_adc: int = reg_a + val + c_val
                            np_adc: int = reg_p & 0x3C
                            if res_adc > 255: np_adc = np_adc | 1
                            if (((reg_a ^ val) & 0x80) == 0) and ((reg_a ^ res_adc) & 0x80): np_adc = np_adc | 0x40
                            res_adc = res_adc & 0xFF
                            if res_adc == 0: np_adc = np_adc | 2
                            if res_adc & 0x80: np_adc = np_adc | 0x80
                            reg_a = res_adc
                            reg_p = np_adc
                        elif op_id == 21:
                            v_sbc: int = val ^ 0xFF
                            c_sbc: int = reg_p & 1
                            res_sbc: int = reg_a + v_sbc + c_sbc
                            np_sbc: int = reg_p & 0x3C
                            if res_sbc > 255: np_sbc = np_sbc | 1
                            if (((reg_a ^ v_sbc) & 0x80) == 0) and ((reg_a ^ res_sbc) & 0x80): np_sbc = np_sbc | 0x40
                            res_sbc = res_sbc & 0xFF
                            if res_sbc == 0: np_sbc = np_sbc | 2
                            if res_sbc & 0x80: np_sbc = np_sbc | 0x80
                            reg_a = res_sbc
                            reg_p = np_sbc
                        elif op_id == 22:
                            res_cmp: int = reg_a - val
                            reg_p = (reg_p & 0x7C) | (1 if reg_a >= val else 0) | (2 if (res_cmp & 0xFF) == 0 else 0) | (res_cmp & 0x80)
                        elif op_id == 23:
                            res_cpx: int = reg_x - val
                            reg_p = (reg_p & 0x7C) | (1 if reg_x >= val else 0) | (2 if (res_cpx & 0xFF) == 0 else 0) | (res_cpx & 0x80)
                        elif op_id == 24:
                            res_cpy: int = reg_y - val
                            reg_p = (reg_p & 0x7C) | (1 if reg_y >= val else 0) | (2 if (res_cpy & 0xFF) == 0 else 0) | (res_cpy & 0x80)
                        elif op_id == 25:
                            res_inc: int = (val + 1) & 0xFF
                            reg_p = (reg_p & 0x7D) | (0x02 if res_inc == 0 else 0) | (res_inc & 0x80)
                            write_val = res_inc
                        elif op_id == 26:
                            res_dec: int = (val - 1) & 0xFF
                            reg_p = (reg_p & 0x7D) | (0x02 if res_dec == 0 else 0) | (res_dec & 0x80)
                            write_val = res_dec
            else:
                if op_id < 39:
                    if op_id < 33:
                        if op_id == 27:
                            reg_x = (reg_x + 1) & 0xFF
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_x == 0 else 0) | (reg_x & 0x80)
                        elif op_id == 28:
                            reg_y = (reg_y + 1) & 0xFF
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_y == 0 else 0) | (reg_y & 0x80)
                        elif op_id == 29:
                            reg_x = (reg_x - 1) & 0xFF
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_x == 0 else 0) | (reg_x & 0x80)
                        elif op_id == 30:
                            reg_y = (reg_y - 1) & 0xFF
                            reg_p = (reg_p & 0x7D) | (0x02 if reg_y == 0 else 0) | (reg_y & 0x80)
                        elif op_id == 31:
                            c31: int = (val >> 7) & 1
                            res31: int = (val << 1) & 0xFF
                            reg_p = (reg_p & 0x7C) | c31 | (2 if res31 == 0 else 0) | (res31 & 0x80)
                            if am == 0: reg_a = res31
                            else: write_val = res31
                        elif op_id == 32:
                            c32: int = val & 1
                            res32: int = val >> 1
                            reg_p = (reg_p & 0x7C) | c32 | (2 if res32 == 0 else 0)
                            if am == 0: reg_a = res32
                            else: write_val = res32
                    else:
                        if op_id == 33:
                            c33: int = reg_p & 1
                            nc33: int = (val >> 7) & 1
                            res33: int = ((val << 1) | c33) & 0xFF
                            reg_p = (reg_p & 0x7C) | nc33 | (2 if res33 == 0 else 0) | (res33 & 0x80)
                            if am == 0: reg_a = res33
                            else: write_val = res33
                        elif op_id == 34:
                            c34: int = (reg_p & 1) << 7
                            nc34: int = val & 1
                            res34: int = (val >> 1) | c34
                            reg_p = (reg_p & 0x7C) | nc34 | (2 if res34 == 0 else 0) | (res34 & 0x80)
                            if am == 0: reg_a = res34
                            else: write_val = res34
                        elif op_id == 35:
                            cur_pc = ea
                        elif op_id == 36:
                            ret: int = (cur_pc - 1) & 0xFFFF
                            mem[0x0100 + reg_sp] = (ret >> 8) & 0xFF
                            reg_sp = (reg_sp - 1) & 0xFF
                            mem[0x0100 + reg_sp] = ret & 0xFF
                            reg_sp = (reg_sp - 1) & 0xFF
                            cur_pc = ea
                        elif op_id == 37:
                            reg_sp = (reg_sp + 1) & 0xFF
                            lo37: int = int(mem[0x0100 + reg_sp])
                            reg_sp = (reg_sp + 1) & 0xFF
                            hi37: int = int(mem[0x0100 + reg_sp])
                            cur_pc = (((hi37 << 8) | lo37) + 1) & 0xFFFF
                        elif op_id == 38:
                            reg_sp = (reg_sp + 1) & 0xFF
                            reg_p = (int(mem[0x0100 + reg_sp]) | 0x20) & 0xEF
                            reg_sp = (reg_sp + 1) & 0xFF
                            lo38: int = int(mem[0x0100 + reg_sp])
                            reg_sp = (reg_sp + 1) & 0xFF
                            hi38: int = int(mem[0x0100 + reg_sp])
                            cur_pc = (hi38 << 8) | lo38
                else:
                    if op_id <= 46:
                        take: int = 0
                        if op_id == 39: take = 1 if (reg_p & 0x01) == 0 else 0
                        elif op_id == 40: take = 1 if (reg_p & 0x01) != 0 else 0
                        elif op_id == 41: take = 1 if (reg_p & 0x02) == 0 else 0
                        elif op_id == 42: take = 1 if (reg_p & 0x02) != 0 else 0
                        elif op_id == 43: take = 1 if (reg_p & 0x80) == 0 else 0
                        elif op_id == 44: take = 1 if (reg_p & 0x80) != 0 else 0
                        elif op_id == 45: take = 1 if (reg_p & 0x40) == 0 else 0
                        elif op_id == 46: take = 1 if (reg_p & 0x40) != 0 else 0
                        if take:
                            new_pc: int = (cur_pc + rel) & 0xFFFF
                            cyc_acc = cyc_acc + (2 if ((cur_pc ^ new_pc) & 0xFF00) else 1)
                            cur_pc = new_pc
                            # Fast-forward tight 1-instruction VBlank/NMI/Sprite-0 wait loops within scanline
                            if rel == -4:
                                prev_op4: int = 0
                                if new_pc >= 0xC000:
                                    prev_op4 = int(prg[b1_off + (new_pc - 0xC000)])
                                elif new_pc >= 0x8000:
                                    prev_op4 = int(prg[b0_off + (new_pc - 0x8000)])
                                if prev_op4 == 0xA5 or prev_op4 == 0xA6 or prev_op4 == 0xA4 or prev_op4 == 0x24 or prev_op4 == 0xC5 or prev_op4 == 0xE4 or prev_op4 == 0xC4:
                                    cyc_acc = target_cycles
                            elif rel == -5:
                                prev_op5: int = 0
                                if new_pc >= 0xC000:
                                    prev_op5 = int(prg[b1_off + (new_pc - 0xC000)])
                                elif new_pc >= 0x8000:
                                    prev_op5 = int(prg[b0_off + (new_pc - 0x8000)])
                                if prev_op5 == 0xAD or prev_op5 == 0x2C or prev_op5 == 0xCD:
                                    cyc_acc = target_cycles
                    else:
                        if op_id == 47: reg_p = reg_p & 0xFE
                        elif op_id == 48: reg_p = reg_p | 0x01
                        elif op_id == 49: reg_p = reg_p & 0xFB
                        elif op_id == 50: reg_p = reg_p | 0x04
                        elif op_id == 51: reg_p = reg_p & 0xBF
                        elif op_id == 52: reg_p = reg_p & 0xF7
                        elif op_id == 53: reg_p = reg_p | 0x08
                        elif op_id == 54:
                            reg_p = (reg_p & 0x3D) | (val & 0xC0) | (2 if (reg_a & val) == 0 else 0)

            if write_val >= 0:
                a_w: int = ea & 0xFFFF
                val_w: int = write_val & 0xFF
                if a_w < 0x2000:
                    mem[a_w & 0x07FF] = val_w
                elif a_w < 0x4000:
                    reg_w: int = a_w & 7
                    if reg_w == 0:
                        st[REG_PPU_CTRL] = val_w
                        st[REG_PPU_T] = (st[REG_PPU_T] & 0x73FF) | ((val_w & 3) << 10)
                        if sl < 240 and (st[REG_PPU_STATUS] & 0x40) and sl < 80:
                            st[REG_SPLIT_NT] = val_w & 3
                        elif sl >= 240 or (st[REG_SPLIT_Y] < 0 and sl < 32):
                            st[REG_TOP_NT] = val_w & 3
                    elif reg_w == 1:
                        st[REG_PPU_MASK] = val_w
                    elif reg_w == 3:
                        st[REG_OAM_ADDR] = val_w
                    elif reg_w == 4:
                        oam_idx: int = st[REG_OAM_ADDR] & 0xFF
                        mem[0x0800 + oam_idx] = val_w
                        st[REG_OAM_ADDR] = (oam_idx + 1) & 0xFF
                    elif reg_w == 5:
                        if st[REG_PPU_LATCH_W] == 0:
                            st[REG_PPU_T] = (st[REG_PPU_T] & 0x7FE0) | ((val_w >> 3) & 0x1F)
                            st[REG_PPU_FINE_X] = val_w & 7
                            st[REG_SCROLL_X] = val_w
                            st[REG_PPU_LATCH_W] = 1
                            if sl < 240 and (st[REG_PPU_STATUS] & 0x40) and sl < 80:
                                if st[REG_SPLIT_Y] < 0:
                                    st[REG_SPLIT_Y] = sl
                                    st[REG_SPLIT_SCROLL_Y] = -1
                                st[REG_SPLIT_SCROLL_X] = val_w
                                st[REG_SPLIT_NT] = st[REG_PPU_CTRL] & 3
                            elif sl >= 240 or (st[REG_SPLIT_Y] < 0 and sl < 32):
                                st[REG_TOP_SCROLL_X] = val_w
                        else:
                            st[REG_PPU_T] = (st[REG_PPU_T] & 0x0C1F) | ((val_w & 7) << 12) | (((val_w >> 3) & 0x1F) << 5)
                            st[REG_SCROLL_Y] = val_w
                            st[REG_PPU_LATCH_W] = 0
                            if sl < 240 and (st[REG_PPU_STATUS] & 0x40) and sl < 80:
                                if st[REG_SPLIT_Y] < 0:
                                    st[REG_SPLIT_Y] = sl
                                st[REG_SPLIT_SCROLL_X] = st[REG_SCROLL_X]
                                st[REG_SPLIT_SCROLL_Y] = val_w
                                st[REG_SPLIT_NT] = st[REG_PPU_CTRL] & 3
                            elif sl >= 240 or (st[REG_SPLIT_Y] < 0 and sl < 32):
                                st[REG_TOP_SCROLL_Y] = val_w
                    elif reg_w == 6:
                        if st[REG_PPU_LATCH_W] == 0:
                            st[REG_PPU_T] = (st[REG_PPU_T] & 0x00FF) | ((val_w & 0x3F) << 8)
                            st[REG_PPU_LATCH_W] = 1
                        else:
                            st[REG_PPU_T] = (st[REG_PPU_T] & 0x7F00) | val_w
                            st[REG_PPU_V] = st[REG_PPU_T]
                            st[REG_PPU_LATCH_W] = 0
                    elif reg_w == 7:
                        v_w: int = st[REG_PPU_V] & 0x3FFF
                        if v_w < 0x2000:
                            if st[REG_HAS_CHR_RAM]:
                                mem[0x2000 + v_w] = val_w
                        elif v_w < 0x3F00:
                            nt_w: int = 0
                            mir_w: int = st[REG_MIRRORING]
                            if mir_w == 0:
                                nt_w = (v_w >> 11) & 1
                            elif mir_w == 1:
                                nt_w = (v_w >> 10) & 1
                            elif mir_w == 3:
                                nt_w = 1
                            mem[0x1000 + (nt_w << 10) + (v_w & 0x3FF)] = val_w
                        else:
                            p_off_w: int = v_w & 0x1F
                            if (p_off_w & 0x13) == 0x10:
                                p_off_w = p_off_w & 0x0F
                            mem[0x0900 + p_off_w] = val_w
                        step_w: int = 32 if (st[REG_PPU_CTRL] & 4) else 1
                        st[REG_PPU_V] = (st[REG_PPU_V] + step_w) & 0x7FFF
                elif a_w == 0x4014:
                    src_page: int = (val_w << 8) & 0x07FF
                    for i_dma in range(256):
                        mem[0x0800 + i_dma] = mem[src_page + i_dma]
                    cyc_acc = cyc_acc + (64 if sl >= 240 else 513)
                elif a_w == 0x4016:
                    st[REG_PAD1_STROBE] = val_w & 1
                    if val_w & 1:
                        st[REG_PAD1_SHIFT] = st[REG_PAD1_STATE]
                elif a_w >= 0x6000 and a_w < 0x8000:
                    mem[0x4000 + (a_w - 0x6000)] = val_w
                elif a_w >= 0x8000:
                    map_id: int = st[REG_MAPPER_ID]
                    if map_id == 2:
                        b0_off = (val_w & st[REG_PRG_MASK]) << 14
                        st[REG_PRG_B0_OFF] = b0_off
                    elif map_id == 3:
                        st[REG_CHR_DIRTY] = ((val_w & st[REG_CHR_MASK]) << 13) | 1
                    elif map_id == 1:
                        if val_w & 0x80:
                            st[REG_MMC1_SHIFT] = 0
                            st[REG_MMC1_COUNT] = 0
                            st[REG_MMC1_CTRL] = st[REG_MMC1_CTRL] | 0x0C
                        else:
                            sh: int = st[REG_MMC1_SHIFT] | ((val_w & 1) << st[REG_MMC1_COUNT])
                            cnt: int = st[REG_MMC1_COUNT] + 1
                            if cnt == 5:
                                st[REG_MMC1_SHIFT] = 0
                                st[REG_MMC1_COUNT] = 0
                                reg_sel: int = (a_w >> 13) & 3
                                if reg_sel == 0:
                                    st[REG_MMC1_CTRL] = sh
                                    m: int = sh & 3
                                    if m == 0: st[REG_MIRRORING] = 2
                                    elif m == 1: st[REG_MIRRORING] = 3
                                    elif m == 2: st[REG_MIRRORING] = 1
                                    else: st[REG_MIRRORING] = 0
                                elif reg_sel == 1:
                                    st[REG_MMC1_CHR0] = sh
                                    if st[REG_HAS_CHR_RAM] == 0:
                                        st[REG_CHR_DIRTY] = 1
                                elif reg_sel == 2:
                                    st[REG_MMC1_CHR1] = sh
                                    if st[REG_HAS_CHR_RAM] == 0:
                                        st[REG_CHR_DIRTY] = 1
                                else:
                                    st[REG_MMC1_PRG] = sh & 0x0F
                                    prg_m: int = (st[REG_MMC1_CTRL] >> 2) & 3
                                    if prg_m < 2:
                                        b: int = (sh & 0x0E) & st[REG_PRG_MASK]
                                        b0_off = b << 14
                                        b1_off = (b + 1) << 14
                                    elif prg_m == 2:
                                        b0_off = 0
                                        b1_off = (sh & 0x0F & st[REG_PRG_MASK]) << 14
                                    else:
                                        b0_off = (sh & 0x0F & st[REG_PRG_MASK]) << 14
                                        b1_off = st[REG_PRG_MASK] << 14
                                    st[REG_PRG_B0_OFF] = b0_off
                                    st[REG_PRG_B1_OFF] = b1_off
                            else:
                                st[REG_MMC1_SHIFT] = sh
                                st[REG_MMC1_COUNT] = cnt

        cyc_acc = cyc_acc - target_cycles
        sl = sl + 1
        if st[REG_CHR_DIRTY] and st[REG_HAS_CHR_RAM] == 0:
            break

    st[REG_A] = reg_a
    st[REG_X] = reg_x
    st[REG_Y] = reg_y
    st[REG_PC] = cur_pc
    st[REG_SP] = reg_sp
    st[REG_P] = reg_p
    st[REG_CYCLES] = cyc_acc
    st[REG_SCANLINE] = sl
    return sl


@micropython.viper
def render_bg_16(st: ptr32, mem: ptr8, fb: ptr16, fpal: ptr16):
    c0: int = int(fpal[0])
    if not (st[REG_PPU_MASK] & 0x08):
        for y in range(240):
            row_c: int = y * 320 + 32
            for x in range(256):
                fb[row_c + x] = c0
        return

    fpal[4] = c0
    fpal[8] = c0
    fpal[12] = c0

    split_y: int = st[REG_SPLIT_Y]
    if split_y > 0:
        split_y = (split_y + 7) & 0xF8
    mir: int = st[REG_MIRRORING]
    bg_pat_base: int = 0x3000 if (st[REG_PPU_CTRL] & 0x10) else 0x2000
    scroll_x0: int = st[REG_TOP_SCROLL_X]
    scroll_y0: int = st[REG_TOP_SCROLL_Y]
    nt0: int = st[REG_TOP_NT]
    split_sx: int = st[REG_SPLIT_SCROLL_X]
    split_nt: int = st[REG_SPLIT_NT]
    cached_key: int = -1

    for y in range(240):
        row: int = y * 320 + 32
        cur_scroll_x: int = scroll_x0
        cur_nt: int = nt0
        eff_y: int = y + scroll_y0
        if eff_y >= 240:
            eff_y = eff_y - 240

        if split_y >= 0 and y >= split_y:
            cur_scroll_x = split_sx
            cur_nt = split_nt

        tile_y: int = eff_y >> 3
        if tile_y >= 30:
            tile_y = tile_y - 30
        fine_y: int = eff_y & 7

        row_key: int = (tile_y << 10) | (cur_nt << 8) | cur_scroll_x
        if row_key != cached_key:
            cached_key = row_key
            base_nt_x: int = cur_nt & 1
            base_nt_y: int = (cur_nt >> 1) & 1
            ty_off: int = tile_y << 5
            attr_row_off: int = 0x03C0 + ((tile_y >> 2) << 3)
            ty_shift: int = (tile_y & 2) << 1
            for c_init in range(32):
                eff_x: int = (c_init << 3) + cur_scroll_x
                tile_x: int = (eff_x >> 3) & 31
                nt_x: int = (base_nt_x + ((eff_x >> 8) & 1)) & 1
                pnt: int = 0
                if mir == 0: pnt = base_nt_y
                elif mir == 1: pnt = nt_x
                elif mir == 3: pnt = 1
                nt_base: int = 0x1000 + (pnt << 10)
                mem[0x0920 + c_init] = mem[nt_base + ty_off + tile_x]
                attr_byte: int = int(mem[nt_base + attr_row_off + (tile_x >> 2)])
                shift: int = ty_shift | (tile_x & 2)
                mem[0x0940 + c_init] = ((attr_byte >> shift) & 3) << 2

        bg_pat_row: int = bg_pat_base + fine_y
        for col in range(32):
            t_addr: int = bg_pat_row + (int(mem[0x0920 + col]) << 4)
            p0: int = int(mem[t_addr])
            p1: int = int(mem[t_addr + 8])
            dst: int = row + (col << 3)

            if (p0 | p1) == 0:
                fb[dst] = c0
                fb[dst + 1] = c0
                fb[dst + 2] = c0
                fb[dst + 3] = c0
                fb[dst + 4] = c0
                fb[dst + 5] = c0
                fb[dst + 6] = c0
                fb[dst + 7] = c0
            else:
                pal_base: int = int(mem[0x0940 + col])
                fb[dst]     = fpal[pal_base | ((p0 >> 7) & 1) | ((p1 >> 6) & 2)]
                fb[dst + 1] = fpal[pal_base | ((p0 >> 6) & 1) | ((p1 >> 5) & 2)]
                fb[dst + 2] = fpal[pal_base | ((p0 >> 5) & 1) | ((p1 >> 4) & 2)]
                fb[dst + 3] = fpal[pal_base | ((p0 >> 4) & 1) | ((p1 >> 3) & 2)]
                fb[dst + 4] = fpal[pal_base | ((p0 >> 3) & 1) | ((p1 >> 2) & 2)]
                fb[dst + 5] = fpal[pal_base | ((p0 >> 2) & 1) | ((p1 >> 1) & 2)]
                fb[dst + 6] = fpal[pal_base | ((p0 >> 1) & 1) | (p1 & 2)]
                fb[dst + 7] = fpal[pal_base | (p0 & 1) | ((p1 & 1) << 1)]

@micropython.viper
def render_sprites_16(st: ptr32, mem: ptr8, fb: ptr16, fpal: ptr16):
    if not (st[REG_PPU_MASK] & 0x10):
        return
    is_8x16: int = 1 if (st[REG_PPU_CTRL] & 0x20) else 0
    height: int = 16 if is_8x16 else 8
    bg_col: int = int(fpal[0])
    pat_8x8: int = 0x3000 if (st[REG_PPU_CTRL] & 0x08) else 0x2000

    for spr in range(63, -1, -1):
        oam_off: int = 0x0800 + (spr << 2)
        sy: int = int(mem[oam_off]) + 1
        if sy >= 240:
            continue
        tile_id: int = int(mem[oam_off + 1])
        attr: int = int(mem[oam_off + 2])
        sx: int = int(mem[oam_off + 3])
        pal_high: int = 0x10 | ((attr & 3) << 2)
        flip_h: int = attr & 0x40
        flip_v: int = attr & 0x80
        behind_bg: int = attr & 0x20

        for r in range(height):
            screen_y: int = sy + r
            if screen_y >= 240:
                break
            row: int = (height - 1 - r) if flip_v else r

            t: int = tile_id
            pat_base: int = pat_8x8
            if is_8x16:
                t = (tile_id & 0xFE) if row < 8 else ((tile_id & 0xFE) + 1)
                pat_base = 0x3000 if (tile_id & 1) else 0x2000
                row = row & 7

            t_addr: int = pat_base + (t << 4) + row
            p0: int = int(mem[t_addr])
            p1: int = int(mem[t_addr + 8])
            if (p0 | p1) == 0:
                continue

            row_off: int = screen_y * 320 + 32
            for b in range(8):
                screen_x: int = sx + b
                if screen_x >= 256:
                    break
                bit: int = b if flip_h else (7 - b)
                p_val: int = ((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1)
                if p_val == 0:
                    continue

                target_px: int = row_off + screen_x
                if behind_bg and fb[target_px] != bg_col:
                    continue
                fb[target_px] = fpal[pal_high | p_val]

@micropython.viper
def render_bg_32(st: ptr32, mem: ptr8, fb: ptr32, fpal: ptr32):
    c0: int = int(fpal[0])
    if not (st[REG_PPU_MASK] & 0x08):
        for y in range(240):
            row_c: int = y * 320 + 32
            for x in range(256):
                fb[row_c + x] = c0
        return

    fpal[4] = c0
    fpal[8] = c0
    fpal[12] = c0

    split_y: int = st[REG_SPLIT_Y]
    if split_y > 0:
        split_y = (split_y + 7) & 0xF8
    mir: int = st[REG_MIRRORING]
    bg_pat_base: int = 0x3000 if (st[REG_PPU_CTRL] & 0x10) else 0x2000

    for y in range(240):
        row: int = y * 320 + 32
        cur_scroll_x: int = st[REG_SCROLL_X]
        cur_nt: int = st[REG_PPU_CTRL] & 3
        eff_y: int = y + st[REG_SCROLL_Y]

        if split_y >= 0 and y >= split_y:
            cur_scroll_x = st[REG_SPLIT_SCROLL_X]
            cur_nt = st[REG_SPLIT_NT]
            eff_y = y - split_y
        else:
            if eff_y >= 240:
                eff_y = eff_y - 240

        tile_y: int = eff_y >> 3
        if tile_y >= 30:
            tile_y = tile_y - 30
        fine_y: int = eff_y & 7
        base_nt_x: int = cur_nt & 1
        base_nt_y: int = (cur_nt >> 1) & 1
        ty_off: int = tile_y << 5
        attr_row_off: int = 0x03C0 + ((tile_y >> 2) << 3)
        ty_shift: int = (tile_y & 2) << 1

        for col in range(32):
            px_start: int = col << 3
            eff_x: int = px_start + cur_scroll_x
            tile_x: int = (eff_x >> 3) & 31
            nt_x: int = (base_nt_x + ((eff_x >> 8) & 1)) & 1

            pnt: int = 0
            if mir == 0: pnt = base_nt_y
            elif mir == 1: pnt = nt_x
            elif mir == 3: pnt = 1

            nt_base: int = 0x1000 + (pnt << 10)
            tile_id: int = int(mem[nt_base + ty_off + tile_x])
            attr_byte: int = int(mem[nt_base + attr_row_off + (tile_x >> 2)])
            shift: int = ty_shift | (tile_x & 2)
            pal_base: int = ((attr_byte >> shift) & 3) << 2

            t_addr: int = bg_pat_base + (tile_id << 4) + fine_y
            p0: int = int(mem[t_addr])
            p1: int = int(mem[t_addr + 8])

            for b in range(8):
                px: int = px_start + b
                bit: int = 7 - b
                p_val: int = ((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1)
                fb[row + px] = fpal[pal_base | p_val]

@micropython.viper
def render_sprites_32(st: ptr32, mem: ptr8, fb: ptr32, fpal: ptr32):
    if not (st[REG_PPU_MASK] & 0x10):
        return
    is_8x16: int = 1 if (st[REG_PPU_CTRL] & 0x20) else 0
    height: int = 16 if is_8x16 else 8
    bg_col: int = int(fpal[0])

    for spr in range(63, -1, -1):
        oam_off: int = 0x0800 + (spr << 2)
        sy: int = int(mem[oam_off]) + 1
        if sy >= 240: continue
        tile_id: int = int(mem[oam_off + 1])
        attr: int = int(mem[oam_off + 2])
        sx: int = int(mem[oam_off + 3])

        for r in range(height):
            screen_y: int = sy + r
            if screen_y >= 240: break
            row: int = (height - 1 - r) if (attr & 0x80) else r

            t: int = tile_id
            pat_base: int = 0x3000 if (st[REG_PPU_CTRL] & 0x08) else 0x2000
            if is_8x16:
                t = (tile_id & 0xFE) if row < 8 else ((tile_id & 0xFE) + 1)
                pat_base = 0x3000 if (tile_id & 1) else 0x2000
                row = row & 7

            t_addr: int = pat_base + (t << 4) + row
            p0: int = int(mem[t_addr])
            p1: int = int(mem[t_addr + 8])
            if (p0 | p1) == 0: continue

            row_off: int = screen_y * 320 + 32
            for b in range(8):
                screen_x: int = sx + b
                if screen_x >= 256: break
                bit: int = b if (attr & 0x40) else (7 - b)
                p_val: int = ((p0 >> bit) & 1) | (((p1 >> bit) & 1) << 1)
                if p_val == 0: continue

                target_px: int = row_off + screen_x
                if (attr & 0x20) and fb[target_px] != bg_col:
                    continue
                pal_entry: int = 0x10 | ((attr & 3) << 2) | p_val
                fb[target_px] = fpal[pal_entry]

@micropython.viper
def apply_scanlines_16(fb: ptr16, start_row: int, height: int, width: int):
    for y in range(start_row, start_row + height, 2):
        row: int = y * 320 + 32
        for x in range(width):
            idx: int = row + x
            val: int = int(fb[idx])
            fb[idx] = (val >> 1) & 0x7BEF

@micropython.viper
def apply_scanlines_32(fb: ptr32, start_row: int, height: int, width: int):
    for y in range(start_row, start_row + height, 2):
        row: int = y * 320 + 32
        for x in range(width):
            idx: int = row + x
            val: int = int(fb[idx])
            fb[idx] = (val >> 1) & 0x7F7F7F

class NES:
    def __init__(self, rom_path, sram_path=None, state_path=None):
        self.rom_path = rom_path
        self.sram_path = sram_path
        self.state_path = state_path
        self.mem = bytearray(0x8000)
        self.st = array.array("i", [0] * 64)
        self.st[REG_SPLIT_Y] = -1

        with open(rom_path, "rb") as fp:
            hdr = fp.read(16)
            data = fp.read()

        prg_len = hdr[4] * 16384
        self.prg_len = prg_len
        self.chr_len = hdr[5] * 8192
        self.prg = bytearray(prg_len + 4)
        self.prg[:prg_len] = data[:prg_len]
        self.chr_rom = bytearray(data[prg_len : prg_len + self.chr_len]) if self.chr_len > 0 else bytearray()

        f6 = hdr[6]
        f7 = hdr[7]
        self.st[REG_MAPPER_ID] = (f7 & 0xF0) | (f6 >> 4)
        self.st[REG_MIRRORING] = f6 & 1
        self.st[REG_HAS_CHR_RAM] = 1 if self.chr_len == 0 else 0
        self.st[REG_PRG_MASK] = max(1, prg_len // 16384) - 1
        self.st[REG_CHR_MASK] = max(1, self.chr_len // 4096) - 1 if self.chr_len > 0 else 0

        self.st[REG_PRG_B0_OFF] = 0
        self.st[REG_PRG_B1_OFF] = (prg_len - 16384) if prg_len >= 16384 else 0

        if self.chr_len >= 8192:
            self.mem[0x2000:0x4000] = self.chr_rom[:8192]

        self.has_battery = bool(f6 & 2)
        if self.has_battery and sram_path:
            for candidate_sav in (sram_path, rom_path.replace(".nes", ".sav")):
                try:
                    with open(candidate_sav, "rb") as sfp:
                        sdata = sfp.read()
                        self.mem[0x4000 : 0x4000 + len(sdata)] = sdata
                        break
                except Exception:
                    pass

        self.st[REG_PC] = self.prg[self.st[REG_PRG_B1_OFF] + 0x3FFC] | (self.prg[self.st[REG_PRG_B1_OFF] + 0x3FFD] << 8)
        self.st[REG_SP] = 0xFD
        self.st[REG_P] = 0x24

    def reset_console(self):
        prg_len = self.prg_len
        for i in range(0x2000):
            self.mem[i] = 0
        self.st[REG_PPU_CTRL] = 0
        self.st[REG_PPU_MASK] = 0
        self.st[REG_PPU_STATUS] = 0
        self.st[REG_NMI_PENDING] = 0
        self.st[REG_SPLIT_Y] = -1
        self.st[REG_PRG_B0_OFF] = 0
        self.st[REG_PRG_B1_OFF] = (prg_len - 16384) if prg_len >= 16384 else 0
        self.st[REG_PC] = self.prg[self.st[REG_PRG_B1_OFF] + 0x3FFC] | (self.prg[self.st[REG_PRG_B1_OFF] + 0x3FFD] << 8)
        self.st[REG_SP] = 0xFD
        self.st[REG_P] = 0x24

    def save_sram(self):
        try:
            os.mkdir("/state")
        except OSError:
            pass
        if self.has_battery and self.sram_path:
            try:
                with open(self.sram_path, "wb") as fp:
                    fp.write(self.mem[0x4000:0x6000])
            except Exception:
                pass
        if self.state_path:
            self.save_state(sync_sram=False)

    def save_state(self, sync_sram=True):
        if not self.state_path:
            return False
        try:
            try:
                os.mkdir("/state")
            except OSError:
                pass
            mv = memoryview(self.mem)
            # Only write the dynamic slices needed for this cartridge type
            end_off = 0x6000 if self.has_battery else (0x4000 if self.chr_len == 0 else 0x2000)
            with open(self.state_path, "wb") as fp:
                fp.write(b"NES2")
                fp.write(self.st)
                fp.write(mv[0x0000:end_off])
            if sync_sram and self.has_battery and self.sram_path:
                with open(self.sram_path, "wb") as sfp:
                    sfp.write(mv[0x4000:0x6000])
            return True
        except Exception:
            return False

    def load_state(self):
        if not self.state_path:
            return False
        try:
            with open(self.state_path, "rb") as fp:
                magic = fp.read(4)
                if magic not in (b"NES1", b"NES2"):
                    return False
                st_bytes = fp.read(256)
                if len(st_bytes) != 256:
                    return False
                import struct
                vals = struct.unpack("<64i", st_bytes)
                for i in range(64):
                    self.st[i] = vals[i]
                mdata = fp.read()
                if len(mdata) > 0:
                    self.mem[0x0000 : len(mdata)] = mdata
                # Restore CHR-ROM window if game uses CHR-ROM
                if self.chr_len > 0:
                    map_id = self.st[REG_MAPPER_ID]
                    if map_id == 1:
                        chr_m = (self.st[REG_MMC1_CTRL] >> 4) & 1
                        if chr_m == 0:
                            b = (self.st[REG_MMC1_CHR0] & 0x1E) & self.st[REG_CHR_MASK]
                            self.mem[0x2000:0x4000] = self.chr_rom[b * 4096 : (b + 2) * 4096]
                        else:
                            b0 = (self.st[REG_MMC1_CHR0] & 0x1F) & self.st[REG_CHR_MASK]
                            b1 = (self.st[REG_MMC1_CHR1] & 0x1F) & self.st[REG_CHR_MASK]
                            self.mem[0x2000:0x3000] = self.chr_rom[b0 * 4096 : (b0 + 1) * 4096]
                            self.mem[0x3000:0x4000] = self.chr_rom[b1 * 4096 : (b1 + 1) * 4096]
                    elif self.chr_len >= 8192:
                        self.mem[0x2000:0x4000] = self.chr_rom[:8192]
            return True
        except Exception:
            return False

    def step_frame(self, pad_state=0):
        st = self.st
        mem = self.mem
        prg = self.prg
        st[REG_PAD1_STATE] = pad_state
        st[REG_SCANLINE] = 0

        while st[REG_SCANLINE] < 262:
            cpu_step_scanline(st, mem, prg, OP_TABLE)
            if st[REG_CHR_DIRTY]:
                dirty_val = st[REG_CHR_DIRTY]
                st[REG_CHR_DIRTY] = 0
                if self.chr_len > 0:
                    map_id = st[REG_MAPPER_ID]
                    if map_id == 3:
                        bank = (dirty_val >> 13) & st[REG_CHR_MASK]
                        mem[0x2000:0x4000] = self.chr_rom[bank * 8192 : (bank + 1) * 8192]
                    elif map_id == 1:
                        chr_m = (st[REG_MMC1_CTRL] >> 4) & 1
                        if chr_m == 0:
                            b = (st[REG_MMC1_CHR0] & 0x1E) & st[REG_CHR_MASK]
                            mem[0x2000:0x4000] = self.chr_rom[b * 4096 : (b + 2) * 4096]
                        else:
                            b0 = (st[REG_MMC1_CHR0] & 0x1F) & st[REG_CHR_MASK]
                            b1 = (st[REG_MMC1_CHR1] & 0x1F) & st[REG_CHR_MASK]
                            mem[0x2000:0x3000] = self.chr_rom[b0 * 4096 : (b0 + 1) * 4096]
                            mem[0x3000:0x4000] = self.chr_rom[b1 * 4096 : (b1 + 1) * 4096]

        st[REG_FRAME_COUNT] += 1

    def render(self, fb, fpal, pixel_bytes=2, scanlines=False):
        if pixel_bytes == 2:
            render_bg_16(self.st, self.mem, fb, fpal)
            render_sprites_16(self.st, self.mem, fb, fpal)
            if scanlines:
                apply_scanlines_16(fb, 0, 240, 256)
        else:
            render_bg_32(self.st, self.mem, fb, fpal)
            render_sprites_32(self.st, self.mem, fb, fpal)
            if scanlines:
                apply_scanlines_32(fb, 0, 240, 256)
