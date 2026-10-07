/* SPDX-License-Identifier: CC0-1.0 */
#include "vocab_scale_golden.c"

int vocab_input(const int32_t *input, int8_t *q, uint32_t *maximum) {
    if (!loaded) return -1;
    int32_t h[128];
    norm(input, 10, h);
    *maximum = (uint32_t)quant(h, 128, q);
    return 0;
}

int64_t vocab_row(const int8_t *q, uint32_t maximum, unsigned row, int32_t *partial) {
    int32_t dot = 0;
    for (unsigned col = 0; col < 128; col++) {
        if (row < 192) dot += (int32_t)q[col] * embedding[row * 128 + col];
        partial[col] = dot;
    }
    return row < 192 ? vocab_scale_expected(dot, maximum, escale[row]) : 0;
}

uint32_t vocab_mac(int32_t acc, int8_t q, int8_t e) {
    return ((uint32_t)(acc + (int32_t)q * e)) & 4194303u;
}

uint32_t vocab_escale(unsigned row) {
    return row < 192 ? escale[row] : 0;
}
