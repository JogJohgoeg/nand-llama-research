/* Observables for the norm0 + Q/K/V producer of any layer; the integer golden is unchanged. */
#include "int_model.c"

void r95_rows(const int32_t *xin,int layer,int matrix,int32_t *out) {
    int32_t h[128];norm(xin,2*layer,h);linear(h,layer,matrix,out);
}
