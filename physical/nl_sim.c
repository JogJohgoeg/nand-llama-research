/* Generic bytewise TapeOut NAND/LATCH interpreter, used on Actions only. */
#include <stdint.h>
#include <stddef.h>
#include <stdlib.h>
#include <string.h>
static uint32_t *a,*b,*latch,ni,no,ng,nl;
static uint8_t *op,*value,*state;
static uint32_t u24(const uint8_t *p){return ((uint32_t)p[0]<<16)|((uint32_t)p[1]<<8)|p[2];}
int nl_init(const uint8_t *raw,size_t size,uint32_t n_in,uint32_t n_out) {
    free(a);free(b);free(latch);free(op);free(value);free(state);
    ni=n_in;no=n_out;ng=0;nl=0;
    for(size_t p=0;p<size;) {if(raw[p]>1)return -1;size_t len=raw[p]?4:7;if(p+len>size)return -2;ng++;p+=len;}
    a=calloc(ng,sizeof(*a));b=calloc(ng,sizeof(*b));op=calloc(ng,1);latch=calloc(ng,sizeof(*latch));
    value=calloc(2+ni+ng,1);state=calloc(ng,1);
    if(!a||!b||!op||!latch||!value||!state)return -3;
    size_t p=0;
    for(uint32_t g=0;g<ng;g++) {
        op[g]=raw[p];a[g]=u24(raw+p+1);
        if(op[g]) {if(a[g]>=2+ni+ng)return -4;latch[nl++]=g;p+=4;}
        else {b[g]=u24(raw+p+4);if(a[g]>=2+ni+g||b[g]>=2+ni+g)return -5;p+=7;}
    }
    return 0;
}
void nl_step(const uint8_t *in,uint8_t *out) {
    value[0]=0;value[1]=1;
    for(uint32_t i=0;i<ni;i++)value[2+i]=(in[i/8]>>(i%8))&1;
    uint32_t s=0;
    for(uint32_t g=0;g<ng;g++)value[2+ni+g]=op[g]?state[s++]:!(value[a[g]]&value[b[g]]);
    memset(out,0,(no+7)/8);
    for(uint32_t i=0;i<no;i++)out[i/8]|=value[2+ni+ng-no+i]<<(i%8);
    for(uint32_t i=0;i<nl;i++)state[i]=value[a[latch[i]]];
}
