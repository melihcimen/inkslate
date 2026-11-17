"""A small QR code encoder (byte mode, error correction level M, versions 1–10).

Enough for the tablet address with its access code; written here to avoid a dependency.
Follows ISO/IEC 18004, structured like Project Nayuki's reference implementation.
"""

# Error correction level M: codewords per block and number of blocks, versions 1..10.
_ECC_PER_BLOCK = [None, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26]
_NUM_BLOCKS = [None, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5]
_ALIGN = [None, [], [6, 18], [6, 22], [6, 26], [6, 30], [6, 34],
          [6, 22, 38], [6, 24, 42], [6, 26, 46], [6, 28, 50]]
_FORMAT_ECL_M = 0


def _raw_modules(ver):
    n = (16 * ver + 128) * ver + 64
    if ver >= 2:
        align = ver // 7 + 2
        n -= (25 * align - 10) * align - 55
        if ver >= 7:
            n -= 36
    return n


def _data_codewords(ver):
    return _raw_modules(ver) // 8 - _ECC_PER_BLOCK[ver] * _NUM_BLOCKS[ver]


def _gf_mul(x, y):
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def _rs_divisor(degree):
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = _gf_mul(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = _gf_mul(root, 0x02)
    return result


def _rs_remainder(data, divisor):
    result = [0] * len(divisor)
    for b in data:
        factor = b ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= _gf_mul(coef, factor)
    return result


def _codewords(data, ver):
    """Data bytes -> final interleaved codewords (data + error correction)."""
    bits = []

    def put(value, length):
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)                                  # byte mode
    put(len(data), 8 if ver < 10 else 16)
    for b in data:
        put(b, 8)
    capacity = _data_codewords(ver) * 8
    put(0, min(4, capacity - len(bits)))            # terminator
    put(0, -len(bits) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]

    nblocks, ecclen = _NUM_BLOCKS[ver], _ECC_PER_BLOCK[ver]
    raw = _raw_modules(ver) // 8
    nshort = nblocks - raw % nblocks
    shortlen = raw // nblocks
    divisor = _rs_divisor(ecclen)
    blocks, k = [], 0
    for i in range(nblocks):
        size = shortlen - ecclen + (0 if i < nshort else 1)
        dat = words[k:k + size]
        k += size
        ecc = _rs_remainder(dat, divisor)
        if i < nshort:
            dat = dat + [0]
        blocks.append(dat + ecc)
    out = []
    for i in range(len(blocks[0])):
        for j, blk in enumerate(blocks):
            if i != shortlen - ecclen or j >= nshort:
                out.append(blk[i])
    return out


class _Matrix:
    def __init__(self, ver):
        self.ver = ver
        self.size = ver * 4 + 17
        self.mod = [[False] * self.size for _ in range(self.size)]
        self.fn = [[False] * self.size for _ in range(self.size)]

    def set_fn(self, x, y, dark):
        self.mod[y][x] = dark
        self.fn[y][x] = True

    def draw_function_patterns(self):
        n = self.size
        for i in range(n):
            self.set_fn(6, i, i % 2 == 0)
            self.set_fn(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (n - 4, 3), (3, n - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < n and 0 <= y < n:
                        self.set_fn(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        pos = _ALIGN[self.ver]
        last = len(pos) - 1
        for i, ax in enumerate(pos):
            for j, ay in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fn(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(0)     # reserve the areas; redrawn with the real mask later
        if self.ver >= 7:
            rem = self.ver
            for _ in range(12):
                rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
            bits = self.ver << 12 | rem
            for i in range(18):
                bit = (bits >> i) & 1 == 1
                a, b = n - 11 + i % 3, i // 3
                self.set_fn(a, b, bit)
                self.set_fn(b, a, bit)

    def draw_format(self, mask):
        data = _FORMAT_ECL_M << 3 | mask
        rem = data
        for _ in range(10):
            rem = (rem << 1) ^ ((rem >> 9) * 0x537)
        bits = (data << 10 | rem) ^ 0x5412
        bit = lambda i: (bits >> i) & 1 == 1  # noqa: E731
        n = self.size
        for i in range(6):
            self.set_fn(8, i, bit(i))
        self.set_fn(8, 7, bit(6))
        self.set_fn(8, 8, bit(7))
        self.set_fn(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fn(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fn(n - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fn(8, n - 15 + i, bit(i))
        self.set_fn(8, n - 8, True)

    def draw_codewords(self, words):
        n, i = self.size, 0
        total = len(words) * 8
        right = n - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(n):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = n - 1 - vert if upward else vert
                    if not self.fn[y][x] and i < total:
                        self.mod[y][x] = (words[i >> 3] >> (7 - (i & 7))) & 1 == 1
                        i += 1
            right -= 2

    def apply_mask(self, mask):
        cond = [
            lambda x, y: (x + y) % 2 == 0,
            lambda x, y: y % 2 == 0,
            lambda x, y: x % 3 == 0,
            lambda x, y: (x + y) % 3 == 0,
            lambda x, y: (x // 3 + y // 2) % 2 == 0,
            lambda x, y: x * y % 2 + x * y % 3 == 0,
            lambda x, y: (x * y % 2 + x * y % 3) % 2 == 0,
            lambda x, y: ((x + y) % 2 + x * y % 3) % 2 == 0,
        ][mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fn[y][x] and cond(x, y):
                    self.mod[y][x] = not self.mod[y][x]

    def penalty(self):
        """Simplified penalty (runs, 2×2 blocks, balance), good enough to pick a mask."""
        n, score = self.size, 0
        for lines in (self.mod, list(zip(*self.mod))):
            for line in lines:
                run, prev = 0, None
                for v in line:
                    run = run + 1 if v == prev else 1
                    prev = v
                    if run == 5:
                        score += 3
                    elif run > 5:
                        score += 1
        for y in range(n - 1):
            for x in range(n - 1):
                v = self.mod[y][x]
                if v == self.mod[y][x + 1] == self.mod[y + 1][x] == self.mod[y + 1][x + 1]:
                    score += 3
        dark = sum(map(sum, self.mod))
        score += abs(dark * 20 - n * n * 10) // (n * n) * 10
        return score


def encode(text):
    """QR code for `text` as rows of booleans (True = dark), without the quiet zone."""
    data = text.encode("utf-8")
    for ver in range(1, 11):
        header = 4 + (8 if ver < 10 else 16)
        if header + 8 * len(data) <= _data_codewords(ver) * 8:
            break
    else:
        raise ValueError("text too long for a version 10 QR code")
    words = _codewords(data, ver)
    best = None
    for mask in range(8):
        m = _Matrix(ver)
        m.draw_function_patterns()
        m.draw_codewords(words)
        m.apply_mask(mask)
        m.draw_format(mask)
        score = m.penalty()
        if best is None or score < best[0]:
            best = (score, m)
    return best[1].mod


def to_svg(matrix, module=6, border=4, dark="#000000", light="#ffffff"):
    n = len(matrix) + 2 * border
    rects = "".join(f'<rect x="{x + border}" y="{y + border}" width="1" height="1"/>'
                    for y, row in enumerate(matrix) for x, v in enumerate(row) if v)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{n * module}" height="{n * module}" '
            f'viewBox="0 0 {n} {n}" shape-rendering="crispEdges">'
            f'<rect width="{n}" height="{n}" fill="{light}"/><g fill="{dark}">{rects}</g></svg>')
