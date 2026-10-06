/* CPU cyclic-index representation of always-rotating banks. Clock gaps are
 * adversarial fixtures, not measured arithmetic latency or a hardware PRNG. */
#ifndef BANK_ROWS
#define BANK_ROWS 32
#endif
#ifndef CLOCK_GAPS
#define CLOCK_GAPS 0
#endif
#include <assert.h>
typedef struct {int8_t q[32];int32_t m;} RingWord;
static RingWord ring_words[4][BANK_ROWS];
static unsigned ring_cursor[4],ring_head[4];
static uint8_t ring_valid[4][BANK_ROWS];
static uint64_t ring_reads,ring_writes;
static uint64_t clock_total,clock_gaps,clock_align,clock_access,clock_residual;
static uint32_t gap_state;
static void prefix_clock(uint64_t clocks);
static void prefix_reset(void);
static void all_tick(uint64_t clocks) {
    clock_total+=clocks;
    for(unsigned h=0;h<4;h++) {
        ring_head[h]=(ring_head[h]+clocks)%BANK_ROWS;
        ring_cursor[h]=(ring_cursor[h]+clocks)%BANK_ROWS;
    }
    prefix_clock(clocks);
}
static void request_gap(void) {
    gap_state=gap_state*1664525U+1013904223U;
    uint64_t gap=CLOCK_GAPS?((gap_state>>8)&127U):0;
    clock_gaps+=gap;all_tick(gap);
}
static void accept_clock(void) {clock_access++;all_tick(1);}
static void ring_seek(unsigned head,unsigned address) {
    request_gap();
    uint64_t wait=(address+BANK_ROWS-ring_cursor[head])%BANK_ROWS;
    clock_align+=wait;all_tick(wait);assert(ring_cursor[head]==address);
}
static void ring_begin(void) {
    clock_total=clock_gaps=clock_align=clock_access=clock_residual=0;
    ring_reads=ring_writes=0;gap_state=260713U;
    all_tick(1); /* The reset edge also rotates the physical data. */
    for(unsigned h=0;h<4;h++) {
        ring_cursor[h]=0;
        for(unsigned j=0;j<BANK_ROWS;j++)ring_valid[h][j]=0;
    }
    prefix_reset();
}
static void ring_store(unsigned head,unsigned pos,unsigned value,const int32_t *a) {
    RingWord word;word.m=quant(a,32,word.q);
    ring_seek(head,pos+value*(BANK_ROWS/2));
    ring_words[head][ring_head[head]]=word;ring_valid[head][ring_head[head]]=1;
    ring_writes++;accept_clock();
}
static int32_t ring_dequant(unsigned head,unsigned pos,unsigned value,unsigned lane) {
    ring_seek(head,pos+value*(BANK_ROWS/2));assert(ring_valid[head][ring_head[head]]);
    /* The existing arithmetic operand registers capture this scalar pair. */
    int8_t q=ring_words[head][ring_head[head]].q[lane];int32_t m=ring_words[head][ring_head[head]].m;
#ifdef BAD_CIRCULATE_KV
    if(lane==0)q=(int8_t)((uint8_t)q^1U);
#endif
    ring_reads++;accept_clock();return kv_dequant(q,m);
}
uint64_t ring_count(unsigned field) {
    return field==0?4*clock_total:field==1?ring_reads:field==2?ring_writes:0;
}
uint64_t circulate_count(unsigned field) {
    return field==0?clock_total:field==1?clock_gaps:field==2?clock_align:field==3?clock_access:clock_residual;
}
