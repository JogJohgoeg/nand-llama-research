# Prefix-state leaf physical measurement

This is one held64x20-bit width slice of the frozen C16 prefix ring. Its
3,942 NAND/1,280 LATCH graph retains the existing advance/write protocol:
`din[19:0]` data, `din[20]` advance, `din[21]` write, `dout[19:0]` head.
Write replaces the departing head only on an advancing edge. Idle holds
all bits. There is no RAM reset; fill all64 words before reading. A shared
six-bit cursor belongs outside the leaf; no per-leaf cursor is introduced.
Thirty-two slices cover the existing64x640 bank. This is a storage leaf,
not the complete prefix controller or the language model.

The immutable `random16` fixture is copied from the integer demo; frozen
C99 must reproduce both its full logits and six prefix-state trace hashes.
All32 lane slices of all six snapshots supply192 complete64-word epochs;
four signed20 boundary epochs follow. An independent addressed C array
supplies every expected memory value across88,196 clocks, including held
writes and arbitrary read order. No NAND output decides expected timing.
Only a4x8 bank is gate-simulated locally. Actions requires full arbitrary
state/input RTL CEC, actual NAND/Verilator/C and real mutations first.

The physical job uses pinned LibreLane3.0.14/SKY130A, four threads and the
same200ns constraints as Q. FP utilization30% and placement target45% are
a single state-heavy calibration point, not an asserted final density or
whole-chip fit. All timing repairs, DRC/LVS/XOR, mapped C vectors and OAS
readback remain enabled. No local EDA or paid runner is used. Completed
GDS/OAS feeds the unified self-hosted viewer; the original Q design stays.

The job is independent of the existing Q and large-pilot queues. Its
artifacts retain final LEF and any emitted timing views for later macro
integration. No claim is made that these views or a complete hierarchy
are usable until the actual artifact is inspected.
