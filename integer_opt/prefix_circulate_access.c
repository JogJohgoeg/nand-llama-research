/* Existing H/A slots hold captured prefix words; no added vector buffer.
 * Host head indices are a cyclic representation, not extra silicon state. */
#ifndef PREFIX_ROWS
#define PREFIX_ROWS 64
#endif
typedef struct {int32_t lane[32];} PrefixWord;
static PrefixWord prefix_words[PREFIX_ROWS];
static int32_t prefix_work_h[128];
static unsigned prefix_cursor,prefix_head;
static uint8_t prefix_valid[PREFIX_ROWS];
static uint64_t prefix_reads,prefix_writes;
static void prefix_clock(uint64_t clocks) {
    prefix_head=(prefix_head+clocks)%PREFIX_ROWS;
    prefix_cursor=(prefix_cursor+clocks)%PREFIX_ROWS;
}
static void prefix_reset(void) {
    prefix_cursor=0;prefix_reads=prefix_writes=0;
    for(unsigned i=0;i<PREFIX_ROWS;i++)prefix_valid[i]=0;
}
static void prefix_begin(void) {assert(prefix_cursor==0 && clock_total==1);}
static void prefix_seek(unsigned address) {
    request_gap();uint64_t wait=(address+PREFIX_ROWS-prefix_cursor)%PREFIX_ROWS;
    clock_align+=wait;all_tick(wait);assert(prefix_cursor==address);
}
static void prefix_read_word(unsigned address,int32_t *target) {
    prefix_seek(address);assert(prefix_valid[prefix_head]);
    for(unsigned i=0;i<32;i++) {
        target[i]=prefix_words[prefix_head].lane[i];
#ifdef BAD_CIRCULATE_PREFIX
        if(i==0)target[i]^=1;
#endif
    }
    prefix_reads++;accept_clock();
}
static void prefix_store_word(unsigned address,const int32_t *in) {
    prefix_seek(address);
    for(unsigned i=0;i<32;i++)prefix_words[prefix_head].lane[i]=in[i];
    prefix_valid[prefix_head]=1;prefix_writes++;accept_clock();
}
static void prefix_store_row(unsigned pos,const int32_t *in) {
    for(unsigned c=0;c<4;c++)prefix_store_word(4*pos+c,in+32*c);
}
static void prefix_load_row(unsigned pos,int32_t *out) {
    for(unsigned c=0;c<4;c++)prefix_read_word(4*pos+c,out+32*c);
}
static void prefix_norm(unsigned pos,int which,int32_t *h) {
    prefix_load_row(pos,h);norm(h,which,h);
}
static void prefix_residual(unsigned pos,int32_t *work,int32_t *dead_slot) {
    assert(work!=dead_slot);
    for(unsigned c=0;c<4;c++) {
        prefix_read_word(4*pos+c,dead_slot);
        for(unsigned i=0;i<32;i++) {
            work[c*32+i]=sat((int64_t)dead_slot[i]+work[c*32+i]);
            clock_residual++;all_tick(1); /* Demonstrate serial, not32 parallel adders. */
        }
        prefix_store_word(4*pos+c,work+32*c);
    }
}
static int32_t prefix_peek(unsigned pos,unsigned lane) {
    unsigned address=4*pos+lane/32;
    unsigned physical=(prefix_head+address+PREFIX_ROWS-prefix_cursor)%PREFIX_ROWS;
    assert(prefix_valid[physical]);return prefix_words[physical].lane[lane%32];
}
uint64_t prefix_count(unsigned field) {
    return field==0?clock_total:field==1?prefix_reads:field==2?prefix_writes:0;
}
