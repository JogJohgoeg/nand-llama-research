/* Test implementation of the already-proved ring bank's model access schedule.
 * Inserted after kv_dequant in a temporary copy of the unchanged golden C.
 * Host structs store logical s8[32]+u20 words; sizeof is not a silicon budget.
 */
#ifndef BANK_ROWS
#define BANK_ROWS 32
#endif
#include <string.h>
typedef struct {int8_t q[32];int32_t m;} RingWord;
static RingWord ring_words[4][BANK_ROWS];
static unsigned ring_cursor[4];
static uint64_t ring_rotations,ring_reads,ring_writes,ring_homing;

static void ring_step(unsigned head,const RingWord *replacement) {
    RingWord tail=replacement?*replacement:ring_words[head][0];
    for(unsigned j=0;j<BANK_ROWS-1;j++)ring_words[head][j]=ring_words[head][j+1];
    ring_words[head][BANK_ROWS-1]=tail;
    ring_cursor[head]=(ring_cursor[head]+1)%BANK_ROWS;
    ring_rotations++;
}
static void ring_seek(unsigned head,unsigned address) {
    while(ring_cursor[head]!=address)ring_step(head,0);
}
static void ring_begin(void) {
    ring_rotations=ring_reads=ring_writes=0;
    for(unsigned h=0;h<4;h++)ring_seek(h,0);
    ring_homing=ring_rotations;
}
static void ring_store(unsigned head,unsigned pos,unsigned value,const int32_t *a) {
    RingWord word;word.m=quant(a,32,word.q);
    ring_seek(head,pos+value*(BANK_ROWS/2));ring_step(head,&word);ring_writes++;
}
static int32_t ring_dequant(unsigned head,unsigned pos,unsigned value,unsigned lane) {
    unsigned address=pos+value*(BANK_ROWS/2);
#ifdef BAD_RING_READ
    address=(address+1)%BANK_ROWS;
#endif
    ring_seek(head,address);ring_reads++;
    return kv_dequant(ring_words[head][0].q[lane],ring_words[head][0].m);
}
uint64_t ring_count(unsigned field) {
    return field==0?ring_rotations:field==1?ring_reads:field==2?ring_writes:ring_homing;
}
