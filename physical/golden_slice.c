/* SPDX-License-Identifier: CC0-1.0
 * The frozen model's own parser/arithmetic form the independent golden.
 */
#include "../integer/int_model.c"

uint64_t slice_word(unsigned address) {
    int base=0;
    for(int m=0;m<7;m++) {
        int cols=m==6?336:128,rows=(m==4||m==5)?336:128;
        int blocks=(cols+31)/32, count=rows*blocks;
        if(address<(unsigned)(base+count)) {
            int row=((int)address-base)/blocks,block=((int)address-base)%blocks;
            uint64_t word=0;
            for(int j=0;j<32&&block*32+j<cols;j++) {
                int q=weights[offsets[m]+row*cols+block*32+j];
                word|=(uint64_t)(q<0?2:q)<<(2*j);
            }
            return word;
        }
        base+=count;
    }
    return 0;
}
int32_t slice_dot(unsigned address,const int8_t *in) {
    uint64_t word=slice_word(address);int32_t total=0;
    for(int i=0;i<32;i++) {
        int q=(int)((word>>(2*i))&3);total+=(int32_t)in[i]*(q==2?-1:q);
    }
    return total;
}
int32_t slice_sat(int64_t a){return sat(a);}
uint32_t slice_exp(int32_t maximum,int32_t score){return exp_weight((int64_t)maximum-score);}
