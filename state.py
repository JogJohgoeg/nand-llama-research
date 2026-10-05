"""Small reusable state/number components and independent integer references."""
import math
import random
from nand import Builder


def signed(value, width):
    return value - (1 << width) if value >> (width - 1) else value


def memory(method, words=16, width=16):
    a = (words - 1).bit_length()
    state = words * width
    b = Builder(state + 2 * a + width + 1)
    q = [list(range(2 + i * width, 2 + (i + 1) * width)) for i in range(words)]
    ra = list(range(2 + state, 2 + state + a))
    wa = list(range(2 + state + a, 2 + state + 2 * a))
    data = list(range(2 + state + 2 * a, 2 + state + 2 * a + width))
    we = 2 + state + 2 * a + width
    def decode(addr, i):
        return b.reduce([x if i >> j & 1 else b.inv(x) for j, x in enumerate(addr)], b.land, 1)
    out = []
    for i, row in enumerate(q):
        enable = b.land(we, decode(wa, i))
        out += [b.mux(enable, old, new) for old, new in zip(row, data)]
    if method == 'onehot':
        dec = [decode(ra, i) for i in range(words)]
        read = [b.reduce([b.land(dec[i], q[i][bit]) for i in range(words)], b.lor, 0)
                for bit in range(width)]
    else:
        read = q
        for select in ra:
            read = [[b.mux(select, x, y) for x, y in zip(read[i], read[i + 1])]
                    for i in range(0, len(read), 2)]
        read = read[0]
    return b.finish(out + read)


def counter(method):
    b = Builder(15)
    q = list(range(2, 15))
    enable, reset = 15, 16
    terminal = b.reduce([x if 6911 >> i & 1 else b.inv(x) for i, x in enumerate(q)], b.land, 1)
    increment = b.add(q, [1] + [0] * 12)[0]
    if method == 'nested':
        out = [b.mux(reset, b.mux(enable, x, b.mux(terminal, y, 0)), 0)
               for x, y in zip(q, increment)]
    else:
        clear = b.lor(reset, b.land(enable, terminal))
        out = [b.land(b.inv(clear), b.mux(enable, x, y)) for x, y in zip(q, increment)]
    done = b.land(b.inv(reset), b.land(enable, terminal))
    return b.finish(out + [done])


def cordic(method):
    b = Builder(52)
    x, y, z = [list(range(2 + 16 * j, 18 + 16 * j)) for j in range(3)]
    iteration = list(range(50, 54))
    def shift(q):
        for j, select in enumerate(iteration):
            shifted = q[(1 << j):] + [q[-1]] * (1 << j)
            q = [b.mux(select, a, c) for a, c in zip(q, shifted)]
        return q
    atan = [round(math.atan(2 ** -i) * 8192) for i in range(16)]
    angles = [[(value >> bit) & 1 for bit in range(16)] for value in atan]
    for select in iteration:
        angles = [[b.mux(select, a, c) for a, c in zip(angles[i], angles[i+1])]
                  for i in range(0, len(angles), 2)]
    def addsub(a, c, subtract):
        if method == 'dual':
            plus = b.add(a, c)[0]
            minus = b.add(a, [b.inv(w) for w in c], 1)[0]
            return [b.mux(subtract, p, m) for p, m in zip(plus, minus)]
        return b.add(a, [b.xor(w, subtract) for w in c], subtract)[0]
    newx = addsub(x, shift(y), b.inv(z[-1]))
    newy = addsub(y, shift(x), z[-1])
    newz = addsub(z, angles[0], b.inv(z[-1]))
    # D state first, observable new Z last. This is one rotation microstep.
    return b.finish(newx + newy + newz + newz)


def int_to_bf16(method):
    b = Builder(32)
    q = list(range(2, 34))
    negative = b.add([b.inv(w) for w in q], [0] * 32, 1)[0]
    magnitude = [b.mux(q[-1], x, y) for x, y in zip(q, negative)]
    higher, onehot = 0, [0] * 32
    for i in range(31, -1, -1):
        onehot[i] = b.land(magnitude[i], b.inv(higher))
        higher = b.lor(higher, magnitude[i])
    out = []
    if method == 'row_round':
        # Separate per-leading-position rounding, then select the finished word.
        rows = []
        for k in range(32):
            sig = [magnitude[k - 7 + j] if 0 <= k - 7 + j < 32 else 0 for j in range(8)]
            sticky = b.reduce(magnitude[:max(0, k-8)], b.lor, 0)
            rounded = b.land(magnitude[k-8], b.lor(sticky, sig[0])) if k >= 8 else 0
            mant, carry = b.add(sig, [0] * 8, rounded)
            exponent = b.add([(k + 127) >> bit & 1 for bit in range(8)], [0] * 8, carry)[0]
            rows.append(mant[:7] + exponent)
        for bit in range(15):
            out.append(b.reduce([b.land(onehot[k], rows[k][bit]) for k in range(32)], b.lor, 0))
    else:
        # Select a common unrounded significand and one guard/sticky decision,
        # then use a single rounding incrementer and exponent incrementer.
        sig = [b.reduce([b.land(onehot[k], magnitude[k-7+j]) for k in range(32) if k-7+j >= 0], b.lor, 0)
               for j in range(8)]
        rounds = []
        for k in range(8, 32):
            sticky = b.reduce(magnitude[:k-8], b.lor, 0)
            rounds.append(b.land(onehot[k], b.land(magnitude[k-8], b.lor(sticky, magnitude[k-7]))))
        rounding = b.reduce(rounds, b.lor, 0)
        mant, carry = b.add(sig, [0] * 8, rounding)
        exp = [b.reduce([onehot[k] for k in range(32) if (k+127) >> bit & 1], b.lor, 0)
               for bit in range(8)]
        exponent = b.add(exp, [0] * 8, carry)[0]
        out = mant[:7] + exponent
    return b.finish(out + [q[-1]])


def integer_bf16(word):
    value = signed(word, 32)
    if value == 0:
        return 0
    sign, value = int(value < 0), abs(value)
    k = value.bit_length() - 1
    if k <= 7:
        sig = value << (7-k)
    else:
        step = 1 << (k-7)
        sig, remainder = divmod(value, step)
        sig += 2 * remainder > step or (2 * remainder == step and sig & 1)
    if sig == 256:
        k, sig = k+1, 128
    return (sign << 15) | ((k+127) << 7) | (sig & 127)


def cases():
    rng = random.Random(3120)
    words, width, a = 16, 16, 4
    state = words * width
    values, expected = [], []
    for i in range(1024):
        q = rng.getrandbits(state)
        ra, wa, data, we = i % words, (i//words) % words, rng.getrandbits(width), (i//256) & 1
        new = (q & ~(((1 << width)-1) << (wa*width))) | (data << (wa*width)) if we else q
        read = (q >> (ra*width)) & ((1 << width)-1)  # read old state on a simultaneous write
        values.append(q | (ra << state) | (wa << (state+a)) | (data << (state+2*a)) | (we << (state+2*a+width)))
        expected.append(new | (read << state))
    for method in ('mux', 'onehot'):
        yield 'memory_' + method, memory(method), values, expected, dict(state_latches=state, method=method,
                    contract='16x16, one read old state and one enabled write, no bulk reset', words=words, width=width)
    values = list(range(1 << 15))
    expected = []
    for word in values:
        q, enable, reset = word & 8191, word >> 13 & 1, word >> 14
        done = int(enable and not reset and q == 6911)
        new = 0 if reset or done else (q+enable) & 8191
        expected.append(new | done << 13)
    for method in ('nested', 'common_clear'):
        yield 'counter_' + method, counter(method), values, expected, dict(state_latches=13, method=method,
                    contract='13-bit enabled counter; terminal 6911; defined behavior on all 8192 states')
    values, expected = [], []
    for i in range(2048):
        x, y, z = [signed(rng.getrandbits(16), 16) for _ in range(3)]
        if i < 16:
            x, y, z = -32768, 32767, (-32768 if i & 1 else 0)
        iteration = i % 16
        direction = 1 if z >= 0 else -1
        nx, ny = (x-direction*(y >> iteration)) & 65535, (y+direction*(x >> iteration)) & 65535
        nz = (z-direction*round(math.atan(2 ** -iteration) * 8192)) & 65535
        values.append((x & 65535) | ((y & 65535) << 16) | ((z & 65535) << 32) | iteration << 48)
        expected.append(nx | ny << 16 | nz << 32 | nz << 48)
    for method in ('dual', 'shared'):
        yield 'cordic_' + method, cordic(method), values, expected, dict(state_latches=48, method=method,
                    contract='one Q3.13 angle CORDIC microstep, arithmetic shifts, 16-bit modular overflow; not full BF16 RoPE')
    values = list(range(512)) + [(1 << 32)-i for i in range(1, 513)]
    values += [rng.getrandbits(32) for _ in range(8192)]
    for k in range(8, 31):
        for sig in (128, 129, 254, 255):
            tie = (sig << (k-7)) + (1 << (k-8))
            for delta in (-1, 0, 1):
                values += [(tie+delta) & 0xffffffff, (-tie-delta) & 0xffffffff]
    values += [0x7fffffff, 0x80000000, 0xffffffff, 0]
    expected = [integer_bf16(v) for v in values]
    for method in ('row_round', 'shared_round'):
        yield 'int32_bf16_' + method, int_to_bf16(method), values, expected, dict(method=method,
                    contract='all signed int32 to BF16, round-to-nearest ties-to-even; integer reference without float32 double rounding')
