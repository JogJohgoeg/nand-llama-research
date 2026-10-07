/* Observables for one attention-head K/V word; the integer golden is unchanged. */
#include "int_model.c"

int32_t kv_word(const int32_t *a32,int pos,int kmode,int8_t *q) {
    int32_t in[128]={0};
    for(int i=0;i<32;i++)in[i]=a32[i];
    if(kmode)rope(in,pos);
    return quant(in,32,q);
}
