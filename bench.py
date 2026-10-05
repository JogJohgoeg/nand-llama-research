#!/usr/bin/env python3
"""Reproducible random-weight experiments, Python construction only.

Full tables are generated/countable locally, but gate evaluation >4,000 NAND
is refused outside Actions. Training and model-quality claims are out of scope.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import struct

from golden import Netlist
from nand import Builder, flip_output, metrics, simulate, verilog, verify_state

ROOT = Path(__file__).resolve().parent
GROUPS = ('weights_z0', 'weights_z25', 'weights_z50', 'embedding', 'nonlinear', 'dot', 'state')


def ternary_table(words, lanes, zeros, seed=3103):
    rng = random.Random(seed)
    n = words * lanes
    values = [0] * (n * zeros // 100)
    values += [rng.choice((-1, 1)) for _ in range(n - len(values))]
    rng.shuffle(values)
    return [sum({0: 0, 1: 1, -1: 2}[v] << (2 * i)
                for i, v in enumerate(values[j:j + lanes])) for j in range(0, n, lanes)]


def lookup(table, width, method):
    n = max(1, (len(table) - 1).bit_length())
    table = table + [0] * ((1 << n) - len(table))
    b = Builder(n, share=method != 'flat')
    addr = list(range(2, n + 2))
    if method == 'flat':
        literals = [(b.inv(x), x) for x in addr]
        dec = [b.reduce([pair[j >> i & 1] for i, pair in enumerate(literals)], b.land, 1)
               for j in range(len(table)) if table[j]]
        rows = [j for j, value in enumerate(table) if value]
        out = [b.reduce([d for d, j in zip(dec, rows) if table[j] >> bit & 1], b.lor, 0)
               for bit in range(width)]
    else:
        memo = {}

        def node(truth, level):
            mask = (1 << (1 << level)) - 1
            if truth in (0, mask):
                return int(bool(truth))
            if method == 'phase' and truth > (truth ^ mask):
                return b.inv(node(truth ^ mask, level))
            key = (truth, level)
            if key not in memo:
                half = 1 << (level - 1)
                low = node(truth & ((1 << half) - 1), level - 1)
                high = node(truth >> half, level - 1)
                memo[key] = b.mux(addr[level - 1], low, high)
            return memo[key]

        out = [node(sum((v >> bit & 1) << i for i, v in enumerate(table)), n)
               for bit in range(width)]
    return b.finish(out)


def table_rtl(table, width):
    ni = max(1, (len(table) - 1).bit_length())
    lines = [f'module top(input [{ni-1}:0] din, output reg [{width-1}:0] dout);',
             'always @* begin', f"dout = {width}'d0;", 'case (din)']
    lines += [f"{ni}'d{i}: dout = {width}'h{word:x};" for i, word in enumerate(table)]
    return '\n'.join(lines + ['default: begin end', 'endcase', 'end', 'endmodule', ''])


def bf16(value):
    u = struct.unpack('>I', struct.pack('>f', value))[0]
    return (u + 0x7fff + ((u >> 16) & 1)) >> 16


def nonlinear_tables():
    return dict(rsqrt=[bf16(1 / math.sqrt(1 + i / 256)) for i in range(256)],
                reciprocal=[bf16(1 / (1 + i / 256)) for i in range(256)],
                exp=[bf16(math.exp(-16 + 16 * i / 255)) for i in range(256)],
                sigmoid=[bf16(1 / (1 + math.exp(8 - 16 * i / 255))) for i in range(256)])


def dot_weights(zeros, seed):
    row = ternary_table(1, 32, zeros, seed)[0]
    return [{0: 0, 1: 1, 2: -1}[(row >> (2 * i)) & 3] for i in range(32)]


def fixed_dot(weights, method):
    b = Builder(256)
    if method == 'fold':
        data = json.loads((ROOT/'data/dot32_t.json').read_text())
        raw = bytes.fromhex(data['nl_hex'][2:])
        assert hashlib.sha256(raw).hexdigest() == 'b1507f55d3bd80bbc55f9e06dc656d81e049138827c97ec7fe640d7bfa2ce0b7'
        source = Netlist.decode(raw, 320, 32)
        wires = list(range(258))
        for w in weights:
            code = {0: 0, 1: 1, -1: 2}[w]
            wires += [code & 1, code >> 1]
        for op, a, c in source.records:
            assert op == 0
            wires.append(b.nand(wires[a], wires[c]))
        return b.finish(wires[-32:])

    def add(a, c):
        width = max(len(a), len(c)) + 1
        return b.add(a + [a[-1]] * (width - len(a)), c + [c[-1]] * (width - len(c)))[0]

    def tree(terms):
        return b.reduce(terms, add, [0])

    pos = tree([list(range(2 + 8 * i, 10 + 8 * i)) for i, w in enumerate(weights) if w == 1])
    neg = tree([list(range(2 + 8 * i, 10 + 8 * i)) for i, w in enumerate(weights) if w == -1])
    if any(w == -1 for w in weights):
        neg += [neg[-1]]
        if method == 'subtract':
            width = max(len(pos), len(neg)) + 1
            pos += [pos[-1]] * (width - len(pos))
            neg += [neg[-1]] * (width - len(neg))
            out = b.add(pos, [b.inv(w) for w in neg], 1)[0]
        else:
            inverted = [b.inv(w) for w in neg]
            negative = b.add(inverted, [0] * len(neg), 1)[0]
            out = add(pos, negative) if any(w == 1 for w in weights) else negative
    else:
        out = pos
    return b.finish(out + [out[-1]] * (32 - len(out)))


def dot_rtl(weights):
    lines = ['module top(input [255:0] din, output signed [31:0] dout);']
    terms = []
    for i, w in enumerate(weights):
        if w:
            lines.append(f'wire signed [31:0] q{i} = {{{{24{{din[{8*i+7}]}}}}, din[{8*i+7}:{8*i}]}};')
            terms.append(('+' if w == 1 else '-') + f' q{i}')
    return '\n'.join(lines + ["assign dout = 32'sd0 " + ' '.join(terms) + ';', 'endmodule', ''])


def table_cases(group, small=False):
    cases = []
    if group.startswith('weights_z'):
        z = int(group.split('_z')[1])
        cases.append(('selector', ternary_table(29 if small else 6912, 4 if small else 32, z),
                      8 if small else 64, {'zeros_percent': z, 'seed': 3103}))
    elif group == 'embedding':
        for z in (0, 25, 50):
            cases.append((f'embed_z{z}', ternary_table(29 if small else 512, 4 if small else 64, z, 3104),
                          8 if small else 128, {'zeros_percent': z, 'seed': 3104}))
        rng = random.Random(3105)
        cases += [('norm', [0x3f00 + rng.randrange(256) for _ in range(576)], 16,
                   {'domain': 'synthetic BF16 norm coefficients [0.5,2), not trained'}),
                  ('scale', [0x3d00 + rng.randrange(768) for _ in range(30)], 16,
                   {'domain': 'synthetic BF16 scales [0.03125,2), not trained'})]
    elif group == 'nonlinear':
        domains = {'rsqrt': 'x=1+i/256', 'reciprocal': 'x=1+i/256',
                   'exp': 'x=-16+16*i/255', 'sigmoid': 'x=-8+16*i/255'}
        cases += [(name, table, 16, {'domain': domains[name]+'; finite lookup only; range reduction excluded'})
                  for name, table in nonlinear_tables().items()]
    return cases


def verify(net, values, expected):
    if len(net.records) > 4000 and os.environ.get('GITHUB_ACTIONS') != 'true':
        return {'status': 'pending Actions: local simulation limit 4000 NAND'}
    actual = simulate(net, values)
    assert actual == expected, 'decoded netlist differs from independent integer/table reference'
    negative = flip_output(net)
    bad = simulate(negative, values[:1])
    assert bad == [expected[0] ^ 1], 'actual-output mutation not detected'
    return dict(status='pass', vectors=len(values), checked_bits=len(values) * net.n_out,
                mismatches=0, negative_sha256=hashlib.sha256(negative.encode()).hexdigest(),
                negative_control='actual first output gate flipped and rejected')


def dot_vectors():
    rng = random.Random(3106)
    vectors = [[rng.randrange(-128, 128) for _ in range(32)] for _ in range(1024)]
    vectors += [[q] * 32 for q in (-128, -127, -1, 0, 1, 126, 127)]
    for i in range(32):
        for value in (-128, -1, 1, 127):
            v = [0] * 32
            v[i] = value
            vectors.append(v)
    packed = [sum((v & 255) << (8 * i) for i, v in enumerate(row)) for row in vectors]
    return vectors, packed


def generate(group, small=False):
    directory = ROOT/'build'/('small' if small else 'full')/group
    directory.mkdir(parents=True, exist_ok=True)
    rows = []

    def save(name, net, reference, values, expected, extra):
        row = dict(name=name, group=group, **metrics(net), **extra,
                   verification=verify(net, values, expected))
        prefix = directory/name
        prefix.with_suffix('.v').write_text(verilog(net))
        prefix.with_suffix('.ref.v').write_text(reference)
        prefix.with_suffix('.json').write_text(json.dumps(dict(row, nl_hex='0x' + net.encode().hex())) + '\n')
        # Includes independent expected values, also used to check the mapped bytes.
        prefix.with_suffix('.vectors.json').write_text(json.dumps({'input': values, 'expected': expected}) + '\n')
        if extra.get('state_latches') and row['verification']['status'] == 'pass':
            actual = verify_state(net, values, expected, extra['state_latches'])
            prefix.with_suffix('.state.json').write_text(json.dumps(actual)+'\n')
            row['stateful'] = {k: v for k, v in actual.items() if k != 'nl_hex'}
        row['verilog_sha256'] = hashlib.sha256(prefix.with_suffix('.v').read_bytes()).hexdigest()
        row['reference_sha256'] = hashlib.sha256(reference.encode()).hexdigest()
        rows.append(row)
        print(f'{group}/{name}: {row["nNand"]} NAND depth {row["nand_depth"]}; {row["verification"]["status"]}', flush=True)

    for label, table, width, extra in table_cases(group, small):
        for method in ('flat', 'shannon', 'phase'):
            net = lookup(table, width, method)
            values = list(range(1 << net.n_in))
            expected = table + [0] * (len(values) - len(table))
            save(label + '_' + method, net, table_rtl(table, width), values, expected,
                 dict(extra, method=method, words=len(table), table_sha256=hashlib.sha256(json.dumps(table).encode()).hexdigest()))
    if group == 'dot':
        vectors, packed = dot_vectors()
        for z in (0, 25, 50):
            for sample in range(4):
                seed = 3110 + sample
                weights = dot_weights(z, seed)
                expected = [sum(w * q for w, q in zip(weights, vec)) & 0xffffffff for vec in vectors]
                for method in ('fold', 'tree', 'subtract'):
                    save(f'z{z}_s{seed}_{method}', fixed_dot(weights, method), dot_rtl(weights), packed, expected,
                         dict(method=method, zeros_percent=z, seed=seed, weights=weights))
    if group == 'state':
        from state import cases
        for label, net, values, expected, extra in cases():
            save(label, net, '// Independent behavioral reference: state.py integer functions and vectors.\n',
                 values, expected, extra)
    assert rows, 'empty experiment group must not pass'
    result = dict(group=group, small=small, status='constructed; each row records its verification boundary', cases=rows)
    (directory/'counts.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=GROUPS, required=True)
    parser.add_argument('--small', action='store_true')
    args = parser.parse_args()
    generate(args.group, args.small)


if __name__ == '__main__':
    main()
