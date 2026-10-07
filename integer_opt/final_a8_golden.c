/* Observables for the final head front end; the integer golden is unchanged. */
#include "int_model.c"

int32_t final_a8(const int32_t *x,int32_t *h,int8_t *q) {
    norm(x,10,h);return quant(h,128,q);
}
