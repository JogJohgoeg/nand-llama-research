gds read /h2/romx/p64.gds
load rom_array_64x64
select top cell
extract do local
extract all
ext2spice lvs
ext2spice -o /h2/romx/p64_ext.spice
quit -noprompt
