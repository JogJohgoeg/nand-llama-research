/* Read the tied E8 table through the unchanged C99 model parser. */
#include "../integer/int_model.c"

uint32_t embedding_code(unsigned address) {
    unsigned row=address&255, column=address>>8;
    if(row>=192 || column>=128)return 0;
    return (uint8_t)embedding[row*128+column];
}
