/* SPDX-License-Identifier: CC0-1.0 */
#include "../integer/int_model.c"

int64_t vocab_scale_expected(int32_t a, uint32_t m, uint32_t g) {
    return int_rne(int_rne((int64_t)a * m, 127) * g, 16777216);
}

int vocab_rows(const int32_t *input, int64_t *output) {
    if (!loaded) return -1;
    int32_t h[128];
    int8_t q[128];
    norm(input, 10, h);
    int32_t m = quant(h, 128, q);
    for (int row = 0; row < 192; row++) {
        int32_t dot = 0;
        for (int i = 0; i < 128; i++) {
            dot += (int32_t)q[i] * embedding[row * 128 + i];
        }
        output[4 * row] = dot;
        output[4 * row + 1] = m;
        output[4 * row + 2] = escale[row];
        output[4 * row + 3] = vocab_scale_expected(dot, (uint32_t)m, escale[row]);
    }
    return 0;
}
