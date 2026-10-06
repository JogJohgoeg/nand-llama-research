/* Read true trits through the frozen C99 model parser, independently of BDDs. */
#include "../integer/int_model.c"

uint64_t true_weight_word(unsigned address, unsigned all_layers) {
    unsigned layer=all_layers?address/6144:0;
    if(layer>=5 || (!all_layers && address>=6144))return 0;
    address%=6144;
    for(int matrix=0;matrix<7;matrix++) {
        unsigned cols=matrix==6?336:128, rows=(matrix==4||matrix==5)?336:128;
        unsigned blocks=(cols+31)/32, count=rows*blocks;
        if(address<count) {
            unsigned row=address/blocks, start=(address%blocks)*32;
            uint64_t word=0;
            for(unsigned j=0;j<32 && start+j<cols;j++) {
                int q=weights[layer*194560+offsets[matrix]+row*cols+start+j];
                word|=(uint64_t)(q<0?2:q)<<(2*j);
            }
            return word;
        }
        address-=count;
    }
    return 0;
}
