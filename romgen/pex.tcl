gds read /h2/romx/p64.gds
load rom_array_64x64
select top cell
extract do local
extract all
ext2spice cthresh 0
ext2spice subcircuit top on
ext2spice scale off
ext2spice -o /h2/romx/p64_pex.spice
quit -noprompt
