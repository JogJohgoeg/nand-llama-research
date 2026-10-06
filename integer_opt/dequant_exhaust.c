/* Exact finite-domain certificate for the actual old/new TapeOut bytes.
 * Each uint64_t carries 64 independent inputs. No synthesis or SAT solver.
 * SPDX-License-Identifier: CC0-1.0 */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "../physical/golden_slice.c"

typedef struct { uint32_t a,b; } Gate;
typedef struct { size_t count; Gate *gates; uint64_t *wires; } Graph;

static uint32_t be24(const unsigned char *p) {
    return ((uint32_t)p[0]<<16)|((uint32_t)p[1]<<8)|p[2];
}
static Graph graph(const char *path) {
    FILE *f=fopen(path,"rb");assert(f);
    assert(fseek(f,0,SEEK_END)==0);long length=ftell(f);
    assert(length>0 && length%7==0);rewind(f);
    unsigned char *raw=malloc((size_t)length);assert(raw);
    assert(fread(raw,1,(size_t)length,f)==(size_t)length);assert(fclose(f)==0);
    Graph n={.count=(size_t)length/7};assert(n.count>=20 && n.count<=4000);
    n.gates=malloc(n.count*sizeof(Gate));n.wires=calloc(n.count+30,sizeof(uint64_t));
    assert(n.gates && n.wires);n.wires[1]=UINT64_MAX;
    for(size_t i=0;i<n.count;i++) {
        assert(raw[7*i]==0);n.gates[i]=(Gate){be24(raw+7*i+1),be24(raw+7*i+4)};
        assert(n.gates[i].a<30+i && n.gates[i].b<30+i);
    }
    free(raw);return n;
}
static uint64_t *eval(Graph *n,uint32_t first) {
    static const uint64_t low[6]={UINT64_C(0xaaaaaaaaaaaaaaaa),UINT64_C(0xcccccccccccccccc),
        UINT64_C(0xf0f0f0f0f0f0f0f0),UINT64_C(0xff00ff00ff00ff00),
        UINT64_C(0xffff0000ffff0000),UINT64_C(0xffffffff00000000)};
    assert((first&63)==0);
    for(unsigned b=0;b<28;b++) {
        n->wires[b+2]=b<6?low[b]:((first>>b)&1)?UINT64_MAX:0;
    }
    for(size_t i=0;i<n->count;i++) {
        Gate g=n->gates[i];n->wires[30+i]=~(n->wires[g.a]&n->wires[g.b]);
    }
    return n->wires+30+n->count-20;
}
static uint32_t unpack(const uint64_t *planes,unsigned lane) {
    uint32_t x=0;
    for(unsigned b=0;b<20;b++) { x|=(uint32_t)((planes[b]>>lane)&1)<<b; }
    return x;
}
int main(int argc,char **argv) {
    assert(argc==6);
    uint64_t first=strtoull(argv[4],NULL,10),count=strtoull(argv[5],NULL,10);
    assert((first&63)==0 && count>0 && (count&63)==0 && first+count<=(UINT64_C(1)<<28));
    const char *cloud=getenv("GITHUB_ACTIONS");
    assert(count<=4096 || (cloud && strcmp(cloud,"true")==0));
    Graph old=graph(argv[1]),candidate=graph(argv[2]),bad=graph(argv[3]);
    clock_t begin=clock();
    for(uint64_t base=first;base<first+count;base+=64) {
        uint64_t *a=eval(&old,(uint32_t)base),*b=eval(&candidate,(uint32_t)base);
        for(unsigned bit=0;bit<20;bit++) {
            if(a[bit]!=b[bit]) {
                fprintf(stderr,"old/new mismatch at batch=%llu bit=%u\n",(unsigned long long)base,bit);return 2;
            }
        }
        for(unsigned lane=0;lane<64;lane++) {
            uint32_t input=(uint32_t)base+lane;int q=(int)(input&255);if(q>=128) { q-=256; }
            uint32_t maximum=input>>8;int32_t expected=slice_sat(int_rne((int64_t)q*maximum,127));
            if(unpack(b,lane)!=((uint32_t)expected&1048575)) {
                fprintf(stderr,"C mismatch at input=%u q=%d m=%u\n",input,q,maximum);return 3;
            }
        }
        if(((base-first)&((UINT64_C(1)<<24)-1))==0) {
            fprintf(stderr,"checked through input %llu\n",(unsigned long long)(base+64));fflush(stderr);
        }
    }
    uint64_t *good=eval(&candidate,(uint32_t)first),*mutated=eval(&bad,(uint32_t)first);unsigned wrong=0;
    for(unsigned lane=0;lane<64;lane++) { wrong+=(unpack(good,lane)!=unpack(mutated,lane)); }
    assert(wrong==64);
    fprintf(stderr,"CPU seconds: %.6f\n",(double)(clock()-begin)/CLOCKS_PER_SEC);
    printf("{\"status\":\"pass\",\"first_input\":%llu,\"input_count\":%llu,\"old_nand\":%zu,\"new_nand\":%zu,\"old_new_mismatches\":0,\"frozen_C_mismatches\":0,\"actual_output_gate_mutation_mismatches\":%u}\n",
        (unsigned long long)first,(unsigned long long)count,old.count,candidate.count,wrong);
    free(old.gates);free(old.wires);free(candidate.gates);free(candidate.wires);free(bad.gates);free(bad.wires);
    return 0;
}
