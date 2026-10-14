#!/bin/sh
# run_eq.sh <body.v> <body_ram.v> <NEG> <cycles> [rp]: Verilator lockstep (m149)
set -e; export PATH=$HOME/tapeout_remote/oss-cad-suite/bin:$PATH
D=$(dirname $0); B=build_neg$3; rm -rf $D/$B
verilator --cc --exe --build -O1 -Wno-fatal --x-assign fast --x-initial fast -DNEG=$3 --top-module eqtop --prefix Veq \
  -Mdir $D/$B $D/top.v $D/prefix_ram_model.v $1 $2 $D/tb.cpp -j 8 > $D/$B.log 2>&1
$D/$B/Veq $4 ${5:-0}
