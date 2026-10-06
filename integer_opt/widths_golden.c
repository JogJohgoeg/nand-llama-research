/* Independent dense packing through the frozen C99 model decoder. */
#include "../integer/int_model.c"
uint64_t dense_weight_word(unsigned address,unsigned lanes) {
    if(lanes!=1 && lanes!=8 && lanes!=16)return 0;
    unsigned start=address*lanes;uint64_t word=0;
    for(unsigned j=0;j<lanes && start+j<972800;j++) {
        int q=weights[start+j];word|=(uint64_t)(q<0?2:q)<<(2*j);
    }
    return word;
}
