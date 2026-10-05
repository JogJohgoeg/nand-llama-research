"""Small deterministic NAND constructors; no solver, synthesis or search.

Constants are wires 0/1. Finish adds explicit last-output records required by
TapeOut. Both core and encoded counts are reported so buffers cannot hide a cost.
"""
import hashlib
from golden import Netlist


class Builder:
    def __init__(self, n_in, share=True):
        self.n_in, self.share = n_in, share
        self.gates, self.unique, self.inverse = [], {}, {0: 1, 1: 0}

    def nand(self, a, b):
        if a == 0 or b == 0:
            return 1
        if a == 1:
            a = b
        if b == 1:
            b = a
        if a == b and a in self.inverse:
            return self.inverse[a]
        key = tuple(sorted((a, b)))
        if self.share and key in self.unique:
            return self.unique[key]
        w = 2 + self.n_in + len(self.gates)
        self.gates.append((0, *key))
        self.unique[key] = w
        if a == b:
            self.inverse[a], self.inverse[w] = w, a
        return w

    def inv(self, a):
        return self.nand(a, a)

    def land(self, a, b):
        return self.inv(self.nand(a, b))

    def lor(self, a, b):
        return self.nand(self.inv(a), self.inv(b))

    def xor(self, a, b):
        t = self.nand(a, b)
        return self.nand(self.nand(a, t), self.nand(b, t))

    def mux(self, s, a, b):
        if a == b:
            return a
        return self.nand(self.nand(self.inv(s), a), self.nand(s, b))

    def reduce(self, terms, op, empty):
        terms = list(terms)
        while len(terms) > 1:
            terms = [op(terms[i], terms[i + 1]) if i + 1 < len(terms) else terms[i]
                     for i in range(0, len(terms), 2)]
        return terms[0] if terms else empty

    def add(self, a, b, carry=0):
        assert len(a) == len(b)
        out = []
        for x, y in zip(a, b):
            t = self.nand(x, y)
            p = self.nand(self.nand(x, t), self.nand(y, t))
            u = self.nand(p, carry)
            out.append(self.nand(self.nand(p, u), self.nand(carry, u)))
            carry = self.nand(t, u)
        return out, carry

    def finish(self, outputs):
        base = self.n_in + 2
        needed = set(outputs)
        for i in range(len(self.gates) - 1, -1, -1):
            if base + i in needed:
                needed.update(self.gates[i][1:])
        remap, live = dict(enumerate(range(base))), []
        for i, (_, a, b) in enumerate(self.gates):
            if base + i in needed:
                remap[base + i] = base + len(live)
                live.append((0, remap[a], remap[b]))
        inverted = []
        for out in outputs:
            w = remap[out]
            inverted.append(base + len(live))
            live.append((0, w, w))
        live.extend((0, w, w) for w in inverted)
        result = Netlist(self.n_in, len(outputs), live)
        result.validate()
        return result


def metrics(net):
    depth = [0] * (net.n_in + 2)
    for op, *args in net.records:
        depth.append(1 + max(depth[w] for w in args) if op == 0 else 0)
    raw = net.encode()
    count = len(net.records) - net.n_state
    endpoint_depths = depth[-net.n_out:] + [depth[g[1]] for g in net.records if g[0] == 1]
    return dict(nIn=net.n_in, nOut=net.n_out, nNand=count, nLatch=net.n_state,
                nand_depth=max(endpoint_depths),
                core_nand=count - 2 * net.n_out,
                output_copy_nand=2 * net.n_out,
                sha256=hashlib.sha256(raw).hexdigest())


def verilog(net):
    base = 2 + net.n_in
    lines = [f'module top(input [{net.n_in - 1}:0] din, output [{net.n_out - 1}:0] dout);',
             f'wire [{base + len(net.records) - 1}:0] w;',
             "assign w[0] = 1'b0; assign w[1] = 1'b1;",
             f'assign w[{base - 1}:2] = din;']
    lines += [f'assign w[{base + i}] = ~(w[{a}] & w[{b}]);'
              for i, (op, a, b) in enumerate(net.records) if op == 0]
    lines += [f'assign dout = w[{base + len(net.records) - 1}:{base + len(net.records) - net.n_out}];',
              'endmodule']
    return '\n'.join(lines) + '\n'


def blif(net):
    assert net.n_state == 0
    lines = ['.model top', '.inputs ' + ' '.join(f'din[{i}]' for i in range(net.n_in)),
             '.outputs ' + ' '.join(f'dout[{i}]' for i in range(net.n_out)),
             '.names w0', '.names w1', '1']
    def name(w):
        return f'din[{w-2}]' if 2 <= w < net.n_in + 2 else f'w{w}'
    for i, (_, a, b) in enumerate(net.records, net.n_in + 2):
        if a == b:
            lines += [f'.names {name(a)} w{i}', '0 1']
        else:
            lines += [f'.names {name(a)} {name(b)} w{i}', '0- 1', '-0 1']
    start = net.n_in + 2 + len(net.records) - net.n_out
    for bit in range(net.n_out):
        lines += [f'.names w{start+bit} dout[{bit}]', '1 1']
    return '\n'.join(lines + ['.end', ''])


def from_yosys(data, ni, no):
    """Reject unexpected/undefined cells; turn every NOT into tied-input NAND."""
    module = data['modules']['top']
    assert set(module['ports']) == {'din', 'dout'}
    pi, po = module['ports']['din'], module['ports']['dout']
    assert (pi['direction'], len(pi['bits'])) == ('input', ni)
    assert (po['direction'], len(po['bits'])) == ('output', no)
    assert all(isinstance(w, int) for w in pi['bits']) and len(set(pi['bits'])) == ni
    known = dict(zip(pi['bits'], range(2, ni + 2)))
    known.update({'0': 0, '1': 1})
    drivers = {}
    for cell in module['cells'].values():
        assert cell['type'] in ('$_NAND_', '$_NOT_'), cell['type']
        con = cell['connections']
        args = [con['A'][0], con['B'][0]] if cell['type'] == '$_NAND_' else con['A'] * 2
        target = con['Y'][0]
        assert target not in drivers and target not in known
        assert all(isinstance(w, int) or w in ('0', '1') for w in args)
        drivers[target] = args
    builder = Builder(ni)
    visiting = set()
    for out in po['bits']:
        stack = [(out, False)]
        while stack:
            wire, ready = stack.pop()
            if wire in known:
                continue
            assert wire in drivers, f'undriven or undefined wire {wire}'
            a, b = drivers[wire]
            if ready:
                known[wire] = builder.nand(known[a], known[b])
                visiting.remove(wire)
            else:
                assert wire not in visiting, 'combinational cycle'
                visiting.add(wire)
                stack += [(wire, True), (b, False), (a, False)]
    return builder.finish([known[w] for w in po['bits']])


def simulate(net, values):
    """Local caller enforces <=4k. CI also decodes the exact encoded artifact."""
    decoded = Netlist.decode(net.encode(), net.n_in, net.n_out)
    xs = [bytes(v >> b & 1 for b in range(net.n_in)) for v in values]
    outs = decoded.step_simd([b''] * len(xs), xs)
    return [sum(bit << b for b, bit in enumerate(out)) for _, out in outs]


def flip_output(net):
    gates = net.records.copy()
    inverse = gates[-net.n_out][1]
    op, a, b = gates[inverse - net.n_in - 2]
    assert op == 0 and a == b
    gates[-net.n_out] = (0, a, a)
    return Netlist(net.n_in, net.n_out, gates)


def with_state(net, bits):
    """Reify low input/output state bits as actual TapeOut LATCH records.

    D outputs are internal wires, so their diagnostic output-copy gates disappear.
    The remaining outputs retain canonical final records. No retiming or reset
    assumption: every current-state/input combination has the same transition.
    """
    assert 0 < bits < net.n_out and bits < net.n_in
    old_base = 2 + net.n_in
    ds = []
    for record in net.records[-net.n_out:-net.n_out + bits]:
        op, a, b = record
        assert op == 0 and a == b
        op, x, y = net.records[a - old_base]
        assert op == 0 and x == y
        ds.append(x)
    outputs = list(range(old_base + len(net.records) - net.n_out + bits, old_base + len(net.records)))
    needed = set(ds + outputs)
    for i in range(len(net.records) - 1, -1, -1):
        if old_base + i in needed:
            needed.update(net.records[i][1:])
    ni = net.n_in - bits
    base = ni + 2
    remap = {0: 0, 1: 1}
    remap.update({2 + i: base + i for i in range(bits)})
    remap.update({2 + bits + i: 2 + i for i in range(ni)})
    gates = [(1, 0)] * bits
    for i, (_, a, b) in enumerate(net.records):
        if old_base + i in needed:
            remap[old_base + i] = base + len(gates)
            gates.append((0, remap[a], remap[b]))
    for i, d in enumerate(ds):
        gates[i] = (1, remap[d])
    result = Netlist(ni, net.n_out - bits, gates)
    result.validate()
    return result


def verify_state(net, values, expected, bits):
    state = with_state(net, bits)
    decoded = Netlist.decode(state.encode(), state.n_in, state.n_out)
    ss = [bytes(x >> b & 1 for b in range(bits)) for x in values]
    xs = [bytes(x >> (bits + b) & 1 for b in range(state.n_in)) for x in values]
    outs = decoded.step_simd(ss, xs)
    actual = [sum(x << b for b, x in enumerate(s + y)) for s, y in outs]
    assert actual == expected
    bad = flip_output(decoded)
    ns, y = bad.step(ss[0], xs[0])
    assert sum(x << b for b, x in enumerate(ns + y)) == (expected[0] ^ (1 << bits))
    return dict(metrics(state), vectors=len(values), mismatches=0,
                negative_control='actual first observable output flipped and rejected',
                negative_sha256=hashlib.sha256(bad.encode()).hexdigest(), nl_hex='0x' + state.encode().hex())
