/* Observables for norm1 + FFN + residual of any layer (int_model.c:142-157); the integer golden is unchanged. */
#include "int_model.c"

void ffn_res(const int32_t *xin,int layer,int32_t *y) {
    int32_t h[128],ff[336];int8_t qh[128];
    norm(xin,2*layer+1,h);int32_t m=quant(h,128,qh);
    for(int i=0;i<336;i++) {
        const int8_t *wg=weights+layer*194560+offsets[4]+i*128;
        const int8_t *wu=weights+layer*194560+offsets[5]+i*128;
        int32_t dg=0,du=0;
        for(int j=0;j<128;j++){dg+=(int32_t)qh[j]*wg[j];du+=(int32_t)qh[j]*wu[j];}
        int32_t g=sat(int_rne((int64_t)dg*m*alpha[layer*7+4],33292288));
        int32_t u=sat(int_rne((int64_t)du*m*alpha[layer*7+5],33292288));
        int64_t j=int_rne(g<0?-(int64_t)g:g,64);if(j>1024)j=1024;
        uint32_t s=g<0?65536-sigtab[j]:sigtab[j];
        ff[i]=sat(int_rne((int64_t)sat(int_rne((int64_t)g*s,65536))*u,4096));
    }
    linear(ff,layer,6,h);
    for(int i=0;i<128;i++)y[i]=sat((int64_t)xin[i]+h[i]);
}
