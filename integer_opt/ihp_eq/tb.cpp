// random lockstep: original body.v vs extracted post-route IHP body; reset (din[0]) held for the first 4 cycles
#include "Veq.h"
#include "verilated.h"
#include <cstdio>
#include <cstdint>
#include <random>
int main(int argc,char**argv){
  long N=argc>1?atol(argv[1]):20000; int rp=argc>2?atoi(argv[2]):0; std::mt19937_64 g(12345);
  Veq*t=new Veq; long bad=0,first=-1;
  for(long c=0;c<N;c++){
    for(int w=0;w<4;w++){uint32_t v=(uint32_t)g(); if(w==3)v&=0xfff; t->din[w]=v;}
    if(c<4)t->din[0]|=1; else if(rp) {if(g()%rp)t->din[0]&=~1u;} else t->din[0]&=~1u;
    t->clk=0;t->eval(); if(t->a!=t->b){bad++; if(first<0)first=c;}
    t->clk=1;t->eval();t->clk=0;t->eval();
  }
  printf("{\"cycles\":%ld,\"mismatch_cycles\":%ld,\"first\":%ld}\n",N,bad,first); return bad?1:0;}
