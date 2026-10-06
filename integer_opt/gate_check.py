"""Same strict golden interpreter, with state count cached per decoded snapshot.

The archived golden.py scans every record in its n_state property. Calling
that property twice per lane dominates exhaustive truth-table checks. The
decoded graph here is private and never mutated, so caching changes no logic.
"""
from functools import cached_property
import hashlib
import os

from golden import Netlist
from nand import flip_output


class Snapshot(Netlist):
    @cached_property
    def n_state(self):return super().n_state


def simulate(net,values):
    decoded=Snapshot.decode(net.encode(),net.n_in,net.n_out)
    inputs=[bytes(x>>bit&1 for bit in range(net.n_in)) for x in values]
    outputs=decoded.step_simd([b'']*len(inputs),inputs)
    return [sum(bit<<i for i,bit in enumerate(out)) for _,out in outputs]


def verify(net,values,expected):
    if len(net.records)>4000 and os.getenv('GITHUB_ACTIONS')!='true':
        return dict(status='pending Actions: local simulation limit 4000 NAND')
    assert simulate(net,values)==expected,'canonical bytes differ from reference'
    negative=flip_output(net)
    assert simulate(negative,values[:1])==[expected[0]^1],'actual gate mutation was not rejected'
    return dict(status='pass',vectors=len(values),checked_bits=len(values)*net.n_out,mismatches=0,
                negative_sha256=hashlib.sha256(negative.encode()).hexdigest(),
                negative_control='actual first output gate flipped and rejected')
