#!/usr/bin/env python3
"""Whole-layer check: Verilator runs the actual layer-0 graph (rtl() of the exact NAND/LATCH bytes)
against int_run's own layer-0 trace for a real prompt. Heavy: m149/m64 or Actions only.

usage: layer0_tb.py prepare <dir> <L> <seed> [fault]   (writes layer0.v, tb.cpp, case.txt, expected.json)
       then in <dir>: verilator --cc --exe --build -O2 --prefix Vdut --top-module layer0 layer0.v tb.cpp -o vsim; ./obj_dir/vsim case.txt [max_layer_clocks]
tb.cpp is generic: the case file holds L, X0 (layer input) and X1 (int_run layer-0 output), so one build serves every case.
"""
from pathlib import Path
import ctypes as ct,json,random,sys,hashlib
HERE=Path(__file__).resolve().parent;sys.path[:0]=[str(HERE),str(HERE.parent/'physical'),str(HERE.parent)]
import layer0,final_a8 as fa
from export import rtl
from nand import metrics,flip_output


def expected(L,seed,out_layer=1):
    import tempfile
    rng=random.Random(seed);toks=[rng.randrange(192) for _ in range(L)]
    with tempfile.TemporaryDirectory() as t:
        g=fa.golden_lib(t);lg=(ct.c_int32*(L*192))();tr=(ct.c_int32*(6*L*128))()
        assert g.int_run((ct.c_int32*L)(*toks),L,lg,tr)==0
        x0=[list(tr[p*128:(p+1)*128]) for p in range(L)];x1=[list(tr[(out_layer*L+p)*128:(out_layer*L+p+1)*128]) for p in range(L)]
    return toks,x0,x1


TB=r'''
#include "Vdut.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <cstdlib>
static Vdut*t;static uint64_t clk=0;
static int L;static int32_t X0[16][128],X1[16][128];
static uint64_t din=0;
static uint32_t step(uint64_t d){t->din=d;t->clk=0;t->eval();uint32_t o=t->dout;t->clk=1;t->eval();t->clk=0;t->eval();clk++;return o;}
static uint32_t peek(uint64_t d){t->din=d;t->clk=0;t->eval();return t->dout;}
static uint64_t host(int start,int row,int mode,int32_t data,int valid,int read){
  return ((uint64_t)start<<7)|((uint64_t)row<<8)|((uint64_t)mode<<14)|((uint64_t)(data&0xfffff)<<16)|((uint64_t)valid<<36)|((uint64_t)read<<37);}
static void write_row(int row,const int32_t*v){
  while(peek(0)>>23&1)step(0);
  step(host(1,row,0,0,0,0));int k=0;long guard=0;
  while(k<32){uint32_t o=peek(host(0,row,0,v[k],1,0));if(o>>20&1){step(host(0,row,0,v[k],1,0));k++;}else step(0);if(++guard>100000){printf("write stall\n");exit(2);}}
  int e=0;while(e<32){uint32_t o=peek(host(0,0,0,0,0,1));if(o>>21&1){if((int32_t)((o&0xfffff)<<12)>>12!=v[e]){printf("echo mismatch row %%d lane %%d\n",row,e);exit(3);}step(host(0,0,0,0,0,1));e++;}else step(0);if(++guard>200000){printf("echo stall\n");exit(2);}}
}
static void read_row(int row,int32_t*v){
  while(peek(0)>>23&1)step(0);
  step(host(1,row,1,0,0,0));int e=0;long guard=0;
  while(e<32){uint32_t o=peek(host(0,0,0,0,0,1));if(o>>21&1){v[e]=(int32_t)((o&0xfffff)<<12)>>12;step(host(0,0,0,0,0,1));e++;}else step(0);if(++guard>200000){printf("read stall\n");exit(2);}}
}
int main(int argc,char**argv){
  if(argc<2){printf("usage: vsim case.txt\n");return 2;}
  FILE*f=fopen(argv[1],"r");if(!f||fscanf(f,"%%d",&L)!=1||L<1||L>16){printf("bad case file\n");return 2;}
  for(int p=0;p<L;p++)for(int i=0;i<128;i++)if(fscanf(f,"%%d",&X0[p][i])!=1)return 2;
  for(int p=0;p<L;p++)for(int i=0;i<128;i++)if(fscanf(f,"%%d",&X1[p][i])!=1)return 2;
  fclose(f);t=new Vdut;
  step(1);step(0);
  for(int p=0;p<L;p++)for(int c=0;c<4;c++)write_row(4*p+c,&X0[p][32*c]);
  uint64_t loaded=clk;
  step(2|((uint64_t)L<<2));long max=argc>2?atol(argv[2]):40000000L*L;uint64_t t0=clk;
  while(!(peek(0)>>26&1)){step(0);if(clk-t0>(uint64_t)max){printf("{\"status\":\"timeout\",\"clocks\":%%llu}\n",(unsigned long long)(clk-t0));return 4;}}
  uint64_t run=clk-t0;int bad=0;int32_t v[32];
  for(int p=0;p<L;p++)for(int c=0;c<4;c++){read_row(4*p+c,v);for(int i=0;i<32;i++)if(v[i]!=X1[p][32*c+i]){if(bad<5)printf("mismatch p%%d i%%d got %%d want %%d\n",p,32*c+i,v[i],X1[p][32*c+i]);bad++;}}
  printf("{\"status\":\"%%s\",\"L\":%%d,\"load_clocks\":%%llu,\"layer_clocks\":%%llu,\"mismatches\":%%d}\n",bad?"fail":"pass",L,(unsigned long long)loaded,(unsigned long long)run,bad);
  delete t;return bad?1:0;
}
'''


def write_case(path,L,seed,out_layer=1):
    toks,x0,x1=expected(L,seed,out_layer)
    text=f'{L}\n'+''.join(' '.join(map(str,row))+'\n' for row in x0+x1);Path(path).write_text(text)
    return dict(L=L,seed=seed,tokens=toks,case_sha256=hashlib.sha256(text.encode()).hexdigest())


def write_tb(d,net):
    d=Path(d);d.mkdir(parents=True,exist_ok=True)
    (d/'layer0.v').write_text(rtl(net,'layer0'));(d/'tb.cpp').write_text(TB.replace('%%','%'))


def prepare(d,L,seed,fault=None):
    d=Path(d);ch=layer0.children();net,comb=layer0.connect(layer0.shell(ch,fault),ch)
    if fault=='output_flip':net=flip_output(net)
    write_tb(d,net);c=write_case(d/'case.txt',L,seed)
    (d/'expected.json').write_text(json.dumps(dict(c,metrics=metrics(net),fault=fault),indent=1))
    print(d,metrics(net)['nNand'],c['tokens'])


if __name__=='__main__':
    assert sys.argv[1]=='prepare';prepare(sys.argv[2],int(sys.argv[3]),int(sys.argv[4]),sys.argv[5] if len(sys.argv)>5 else None)
