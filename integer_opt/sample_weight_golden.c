/* New operator observables only; the integer golden remains unchanged. */
#include "int_model.c"

void weight_pairs(const uint64_t *input,uint32_t *index,uint32_t *weight,unsigned n) {
    for(unsigned i=0;i<n;i++) {
        int32_t top=signed32((uint32_t)input[i]);
        int32_t logit=signed32((uint32_t)(input[i]>>32));
        if(top<logit) {index[i]=UINT32_MAX;weight[i]=0;continue;}
        int64_t delta=(int64_t)top-logit;
        int64_t scaled=int_rne(5*delta,4);
        index[i]=(uint32_t)int_rne(scaled,64);
        weight[i]=exp_weight(scaled);
    }
}
