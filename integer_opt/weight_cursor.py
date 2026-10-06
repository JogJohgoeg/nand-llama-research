#!/usr/bin/env python3
"""Small exact controller for the existing 32-lane true-weight table interface.

Controls: reset/start/advance, layer[2:0], matrix[2:0]. Start loads a matrix;
advance visits its row-major groups, holding the final address after completion.
Reset or an invalid start clears state. The complete model controller is separate.
"""
import argparse
import ctypes as ct
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import signal
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from ci import cec
from gate_check import verify
from nand import Builder,blif,flip_output,from_yosys,metrics,verilog,verify_state,with_state

BASE=(0,512,1024,1536,2048,3392,4736)
NS=32


def make():
    b=Builder(NS+9);old=list(range(2,34));ins=list(range(34,43))
    addr,row,group,matrix,active=old[:15],old[15:24],old[24:28],old[28:31],old[31]
    reset,start,advance=ins[:3];layer,mat=ins[3:6],ins[6:9]
    def eq(bits,value):return b.reduce([x if value>>i&1 else b.inv(x) for i,x in enumerate(bits)],b.land,1)
    last_group=b.mux(eq(matrix,6),eq(group,3),eq(group,10))
    wide_rows=b.land(matrix[2],b.inv(matrix[1]))
    last_row=b.mux(wide_rows,eq(row,127),eq(row,335))
    last=b.land(last_group,last_row);inc=b.land(b.land(advance,active),b.inv(last))
    valid_layer=b.lor(b.inv(layer[2]),b.land(b.inv(layer[0]),b.inv(layer[1])))
    valid_matrix=b.inv(b.reduce(mat,b.land,1));begin=b.land(b.land(start,b.inv(reset)),b.land(valid_layer,valid_matrix))
    load=b.lor(reset,start)
    # 6,144*layer + matrix base, with constants folded into NAND logic.
    times3=b.add(layer+[0],[0]+layer)[0]
    layer_base=[0]*11+times3
    offsets=[[value>>i&1 for i in range(15)] for value in (*BASE,0)]
    for select in mat:offsets=[[b.mux(select,x,y) for x,y in zip(offsets[i],offsets[i+1])] for i in range(0,len(offsets),2)]
    base=b.add(layer_base,offsets[0])[0]
    addr_next=b.add(addr,[0]*15,inc)[0]
    row_next=b.add(row,[0]*9,b.land(inc,last_group))[0]
    group_next=b.add(group,[0]*4,inc)[0];end_group=b.land(inc,last_group)
    group_next=[b.land(b.inv(end_group),x) for x in group_next]
    nxt=[b.mux(load,x,b.land(begin,y)) for x,y in zip(addr_next,base)]
    nxt += [b.land(b.inv(load),x) for x in row_next+group_next]
    nxt += [b.mux(load,x,b.land(begin,y)) for x,y in zip(matrix,mat)]
    nxt += [b.mux(load,b.land(active,b.inv(b.land(advance,last))),begin)]
    outputs=addr+row+group+[active,b.land(active,last_group),b.land(active,last)]
    combin=b.finish(nxt+outputs);return combin,with_state(combin,NS)


def transition(state,inputs):
    addr=state&32767;row=state>>15&511;group=state>>24&15;mat=state>>28&7;active=state>>31
    last_group=group==(10 if mat==6 else 3);last=last_group and row==(335 if mat in (4,5) else 127)
    observed=addr+(row<<15)+(group<<24)+(active<<28)+(int(active and last_group)<<29)+(int(active and last)<<30)
    reset=inputs&1;start=inputs>>1&1;advance=inputs>>2&1;layer=inputs>>3&7;matrix=inputs>>6&7
    if reset or start and (layer>=5 or matrix>=7):new=0
    elif start:new=layer*6144+BASE[matrix]+(matrix<<28)+(1<<31)
    elif advance and active:
        if last:new=state&0x7fffffff
        else:
            addr=(addr+1)&32767
            if last_group:row=(row+1)&511;group=0
            else:group=(group+1)&15
            new=addr+(row<<15)+(group<<24)+(mat<<28)+(1<<31)
    else:new=state
    return new,observed


def reference():
    cases='\n'.join(f"3'd{i}:base=15'd{v};" for i,v in enumerate(BASE))
    return f'''module top(input [40:0] din,output [62:0] dout);
wire [31:0] old=din[31:0];
wire reset=din[32],start=din[33],advance=din[34];
wire [2:0] layer=din[37:35],matrix=din[40:38],old_matrix=old[30:28];
wire [14:0] address=old[14:0];wire [8:0] row=old[23:15];wire [3:0] group_id=old[27:24];
wire active=old[31];
wire last_group=(old_matrix==6) ? group_id==10 : group_id==3;
wire last_row=(old_matrix==4 || old_matrix==5) ? row==335 : row==127;
reg [14:0] base;reg [31:0] next_state;
always @* begin
 base=0;case(matrix)
 {cases}
 default:base=0;endcase
 next_state=old;
 if(reset || (start && (layer>=5 || matrix>=7))) next_state=0;
 else if(start) begin
  next_state=0;next_state[14:0]=layer*15'd6144+base;
  next_state[30:28]=matrix;next_state[31]=1;
 end else if(advance && active) begin
  if(last_group && last_row) next_state[31]=0;
  else begin
   next_state[14:0]=address+15'd1;
   if(last_group) begin next_state[27:24]=0;next_state[23:15]=row+9'd1;end
   else next_state[27:24]=group_id+4'd1;
  end
 end
end
assign dout={{active && last_group && last_row,active && last_group,active,group_id,row,address,next_state}};
endmodule
'''


def sequence(net,out):
    assert metrics(net)['nNand']<=4000
    with tempfile.TemporaryDirectory() as tmp:
        tmp=Path(tmp);sim=tmp/'sim.so';gold=tmp/'gold.so'
        for source,target in ((ROOT/'physical/nl_sim.c',sim),(ROOT/'integer_opt/weights_golden.c',gold)):
            subprocess.run(['cc','-O2','-std=c99','-shared','-fPIC',str(source),'-o',str(target)],check=True,timeout=30)
        g=ct.CDLL(str(gold));g.int_init.argtypes=[ct.c_void_p,ct.c_int]
        blob=(ROOT/'physical/model.bin').read_bytes()
        assert hashlib.sha256(blob).hexdigest()=='5a8415731e525ced7873198369c9138407631f4d0485b5e4d0314c55850478a1'
        assert g.int_init(blob,len(blob))==0
        g.true_weight_word.argtypes=[ct.c_uint,ct.c_uint];g.true_weight_word.restype=ct.c_uint64
        s=ct.CDLL(str(sim));s.nl_init.argtypes=[ct.c_void_p,ct.c_size_t,ct.c_uint32,ct.c_uint32]
        s.nl_step.argtypes=[ct.c_void_p,ct.c_void_p];raw=net.encode();assert s.nl_init(raw,len(raw),net.n_in,net.n_out)==0
        result=ct.create_string_buffer(4);state=0;clocks=words=stalls=0;digest=hashlib.sha256()
        def step(inputs):
            nonlocal state,clocks
            state,want=transition(state,inputs);s.nl_step(inputs.to_bytes(2,'little'),result)
            got=int.from_bytes(result.raw,'little');assert got==want,(clocks,got,want)
            digest.update(got.to_bytes(4,'little'));clocks+=1;return got
        step(1)
        for layer in range(5):
            offset=layer*194560
            for matrix in range(7):
                rows=336 if matrix in (4,5) else 128;cols=336 if matrix==6 else 128;groups=(cols+31)//32
                step(2+(layer<<3)+(matrix<<6))
                for row in range(rows):
                    for group in range(groups):
                        address=layer*6144+BASE[matrix]+row*groups+group
                        if words%31==0:step(0);stalls+=1
                        observed=step(4)
                        assert observed&32767==address and observed>>15&511==row and observed>>24&15==group and observed>>28&1
                        word=0
                        for lane in range(min(32,cols-32*group)):
                            i=offset+row*cols+32*group+lane
                            word|=((blob[8+i//4]>>(2*(i%4)))&3)<<(2*lane)
                        assert g.true_weight_word(observed&32767,1)==word
                        words+=1
                assert step(4)>>28&1==0
                offset+=rows*cols
        # Restart, invalid start and reset priorities, including an interrupted matrix.
        for controls in (2,4,2+(4<<3)+(6<<6),4,2+(7<<3),4,2+(7<<6),4,7,0):step(controls)
        assert words==30720
        bad=flip_output(net);raw=bad.encode();assert s.nl_init(raw,len(raw),net.n_in,net.n_out)==0
        s.nl_step((1).to_bytes(2,'little'),result);assert int.from_bytes(result.raw,'little')==1
        return dict(status='pass',matrix_calls=35,weight_words=words,clocks=clocks,stalls=stalls,
                    trace_sha256=digest.hexdigest(),negative_control='actual address output gate mutation rejected',
                    scope='small cursor gates plus C weight-parser addressing; no large selector gate simulation')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--cloud',action='store_true');args=ap.parse_args()
    if args.cloud:assert os.getenv('GITHUB_ACTIONS')=='true'
    else:signal.alarm(55)
    out=ROOT/'build/integer_opt/weight_cursor';out.mkdir(parents=True,exist_ok=True);combin,net=make();p=out/'cursor'
    rng=random.Random(260649);xs=[rng.getrandbits(41) for _ in range(512)];ys=[]
    for x in xs:
        state,observed=transition(x&0xffffffff,x>>32);ys.append(state+(observed<<32))
    checks=verify(combin,xs,ys);assert checks['status']=='pass';state=verify_state(combin,xs,ys,NS);state.pop('nl_hex')
    p.with_suffix('.nl').write_bytes(net.encode());p.with_suffix('.v').write_text(verilog(combin))
    p.with_suffix('.ref.v').write_text(reference());p.with_suffix('.blif').write_text(blif(combin))
    report=dict(status='small gate and C addressing checks pass',metrics=metrics(net),vectors=checks,
                latch_check=state,sequence=sequence(net,out),scope='one actual controller component, not an autonomous inference controller')
    if args.cloud:
        abc=shutil.which('yosys-abc') or shutil.which('berkeley-abc');assert abc
        script=p.with_suffix('.ys');script.write_text(f'read_verilog {p}.ref.v\nhierarchy -check -top top\nproc\nflatten\ntechmap\nopt -fast\nabc -g NAND -fast\nopt_clean\ncheck -assert\nwrite_json {p}.ref.json\n')
        subprocess.run(['yosys','-Q','-T','-l',str(p)+'.yosys.log','-s',str(script)],check=True,stdout=subprocess.DEVNULL,timeout=240)
        ref=from_yosys(json.loads(p.with_suffix('.ref.json').read_text()),combin.n_in,combin.n_out)
        p.with_suffix('.reference.blif').write_text(blif(ref))
        report['cec']=cec(abc,p.with_suffix('.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.cec.log'));assert report['cec']['verdict']=='equivalent'
        p.with_suffix('.negative.blif').write_text(blif(flip_output(combin)))
        report['cec_negative']=cec(abc,p.with_suffix('.negative.blif'),p.with_suffix('.reference.blif'),p.with_suffix('.negative.log'));assert report['cec_negative']['verdict']=='different'
        report['status']='cloud independent RTL CEC and small gate/C-addressing checks pass'
    report.update(run_id=os.getenv('GITHUB_RUN_ID'),revision=os.getenv('GITHUB_SHA'),
                  sources={str(f.relative_to(ROOT)):hashlib.sha256(f.read_bytes()).hexdigest() for f in [Path(__file__),ROOT/'integer_opt/weights_golden.c',ROOT/'integer/int_model.c',ROOT/'physical/nl_sim.c',ROOT/'nand.py',ROOT/'golden.py',ROOT/'integer_opt/gate_check.py',ROOT/'ci.py']})
    (out/'receipt.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))


if __name__=='__main__':main()
