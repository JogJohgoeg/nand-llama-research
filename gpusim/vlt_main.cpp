// Verilator baseline: one design instance, random inputs, N clocks; prints cycles/s.
#include "Vdut.h"
#include "verilated.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
int main(int argc,char**argv){
  Verilated::commandArgs(argc,argv);
  long n=argc>1?atol(argv[1]):100000;
  Vdut*top=new Vdut;uint64_t s=0x9e3779b97f4a7c15ull,chk=0;
  auto next=[&]{s^=s<<13;s^=s>>7;s^=s<<17;return s;};
  auto t0=std::chrono::steady_clock::now();
  for(long c=0;c<n;c++){
    #if VL_WORDS_IN
    for(int i=0;i<VL_WORDS_IN;i++)top->din[i]=(uint32_t)next();
    #else
    top->din=next();
    #endif
    top->clk=0;top->eval();
    chk^=(uint64_t)top->dout*(c+1);
    top->clk=1;top->eval();
  }
  double sec=std::chrono::duration<double>(std::chrono::steady_clock::now()-t0).count();
  printf("{\"clocks\":%ld,\"seconds\":%.6f,\"checksum\":%llu}\n",n,sec,(unsigned long long)chk);
  delete top;return 0;
}
