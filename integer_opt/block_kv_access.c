/* Whole KV words live in the current, still-dead32-lane H output slice.
 * Each H slot is20 bits. Codes occupy bits0..7; m is split across the unused
 * upper12 bits of slot0 and upper8 bits of slot1. No extra word buffer. */
static uint64_t cached_dequants,acc_byte_reads,acc_byte_writes,acc_rotations;
static unsigned acc_cursor;
static void ring_cache(unsigned head,unsigned pos,unsigned value,int32_t *cache) {
    ring_seek(head,pos+value*(BANK_ROWS/2));assert(ring_valid[head][ring_head[head]]);
    const RingWord *word=&ring_words[head][ring_head[head]];
    for(unsigned i=0;i<32;i++)cache[i]=(uint8_t)word->q[i];
    cache[0]|=(word->m&4095)<<8;cache[1]|=((word->m>>12)&255)<<8;
#ifdef BAD_BLOCK_CACHE
    cache[0]^=1; /* An actual captured KV code bit. */
#endif
    ring_reads++;accept_clock();
}
static int32_t cached_dequant(const int32_t *cache,unsigned lane) {
    int32_t q=cache[lane]&255;if(q>=128)q-=256;
    int32_t m=((cache[0]>>8)&4095)|(((cache[1]>>8)&255)<<12);
    cached_dequants++;return kv_dequant((int8_t)q,m);
}
/* The dead336-byte FF-code bank holds32 signed64 accumulators in256 bytes.
 * Current arithmetic only needs s42;64 bits align two values per128-bit word.
 * The C array is logical order; a held21-word ring cursor accounts for moves.
 * Arithmetic operand registers hold one captured accumulator, never32 new ones. */
static void acc_seek(unsigned lane) {
    unsigned address=lane/2,wait=(address+21-acc_cursor)%21;
    acc_rotations+=wait;all_tick(wait);acc_cursor=address;
}
static void acc_home(void) {acc_seek(0);}
static void acc_store(int32_t *scratch,unsigned lane,int64_t value) {
    acc_seek(lane);uint64_t bits=(uint64_t)value;
    for(unsigned j=0;j<8;j++)scratch[8*lane+j]=(int32_t)((bits>>(8*j))&255);
#ifdef BAD_ACCUM_STORE
    scratch[8*lane+3]^=1; /* Actual stored bit24, safely within int64. */
#endif
    acc_byte_writes+=8;all_tick(8);
}
static int64_t acc_load(const int32_t *scratch,unsigned lane) {
    acc_seek(lane);uint64_t bits=0;
    for(unsigned j=0;j<8;j++)bits|=(uint64_t)(scratch[8*lane+j]&255)<<(8*j);
    acc_byte_reads+=8;all_tick(8);
    return bits>>63?-(int64_t)(~bits)-1:(int64_t)bits;
}
uint64_t block_count(unsigned field) {
    return field==0?cached_dequants:field==1?acc_byte_reads:field==2?acc_byte_writes:acc_rotations;
}
