gds read /h2/romx/p64.gds
load rom_array_64x64
select top cell
drc euclidean on
drc style drc(full)
drc check
drc catchup
set r [drc listall why]
set n 0
foreach {why boxes} $r { puts "DRC: [llength $boxes] :: $why :: [lrange $boxes 0 1]"; incr n [llength $boxes] }
puts "DRCTOTAL $n"
quit -noprompt
