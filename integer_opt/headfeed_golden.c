/* Observables for one head's Q/K/V feed; the integer golden is unchanged. */
#include "int_model.c"

int32_t head_feed(const int32_t *a32,int pos,int mode,int8_t *q,int32_t *qv) {
    int32_t in[128]={0};
    for(int i=0;i<32;i++)in[i]=a32[i];
    if(mode&1)rope(in,pos);
    if(mode&2){for(int i=0;i<32;i++)qv[i]=in[i];return 0;}
    return quant(in,32,q);
}
