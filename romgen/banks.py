#!/usr/bin/env python3
"""banks.py <outdir>: write the 60 ROM bank programming files from the shared weight table.
Bank b holds words b*512 .. b*512+511 of layer0.shared_words() (linear address layer*6144 + weight_words
index); wordline w of bank b = word b*512+w; bitline r = word bit r (lane i at bits 2i plus, 2i+1 minus).
Line w of bank_bb.txt is 64 characters, character r = bit r. Words 30,720.. are all zero and not stored."""
import sys
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path[:0]=[str(R/'integer_opt'),str(R/'physical'),str(R)]
import layer0
K,NB=512,60
W=layer0.shared_words();assert not any(W[K*NB:])
d=Path(sys.argv[1]);d.mkdir(parents=True,exist_ok=True)
for b in range(NB):
    (d/f'bank_{b:02d}.txt').write_text(''.join(''.join(str(W[b*K+w]>>r&1) for r in range(64))+'\n' for w in range(K)))
print('banks',NB,'words',K*NB,'ones',sum(bin(x).count('1') for x in W))
