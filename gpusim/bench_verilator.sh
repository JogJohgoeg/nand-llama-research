#!/bin/sh
# m149 only: Verilator baseline per netlist and thread count. Usage: bench_verilator.sh <name> <din_bits> <clocks> <threads...>
set -e
export PATH=$HOME/tapeout_remote/oss-cad-suite/bin:$PATH
name=$1;bits=$2;clocks=$3;shift 3
cd "$(dirname "$0")";mkdir -p build/vlt
words=0;[ "$bits" -gt 64 ] && words=$(( (bits+31)/32 ))
for th in "$@"; do
  d=build/vlt/${name}_t$th;rm -rf $d
  topt=""; [ "$th" -gt 1 ] && topt="--threads $th"
  verilator --cc --exe --build -O3 --prefix Vdut --top-module dut -Wno-fatal $topt --x-assign fast --x-initial fast -CFLAGS "-O2 -DVL_WORDS_IN=$words" --Mdir $d -o vsim nets/$name.v vlt_main.cpp -j 16 >/dev/null 2>$d.log || { tail -5 $d.log; exit 1; }
  printf '%s threads=%s ' $name $th; $d/vsim $clocks
done
