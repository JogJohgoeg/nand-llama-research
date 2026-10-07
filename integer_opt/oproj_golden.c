/* Observables for the attention output projection + residual; the integer golden is unchanged. */
#include "int_model.c"

void oproj(const int32_t *h,const int32_t *x,int32_t *y,int layer) {
    int32_t a[128];linear(h,layer,3,a);
    for(int i=0;i<128;i++)y[i]=sat((int64_t)x[i]+a[i]);
}
int weight_at(int layer,int matrix,int row,int col) {
    int n=matrix==6?336:128;return weights[layer*194560+offsets[matrix]+row*n+col];
}
int32_t alpha_at(int layer,int matrix){return alpha[layer*7+matrix];}
