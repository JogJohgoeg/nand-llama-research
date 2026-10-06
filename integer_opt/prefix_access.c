/* Logical s20[32] words in the proven 64x640 (C16) ring bank.
 * Only the existing H and A vector slots stage data; no extra row buffer.
 * Host int32 storage is not the silicon width. Trace peeks are observations,
 * excluded from inference accesses and never used to compute model values.
 */
#ifndef PREFIX_ROWS
#define PREFIX_ROWS 64
#endif
typedef struct {int32_t lane[32];} PrefixWord;
static PrefixWord prefix_words[PREFIX_ROWS];
static int32_t prefix_work_h[128];
static unsigned prefix_cursor;
static uint64_t prefix_rotations,prefix_reads,prefix_writes,prefix_homing;

static void prefix_step(const int32_t *replacement) {
    PrefixWord tail=prefix_words[0];
    if(replacement)for(int i=0;i<32;i++)tail.lane[i]=replacement[i];
    for(unsigned r=0;r<PREFIX_ROWS-1;r++)prefix_words[r]=prefix_words[r+1];
    prefix_words[PREFIX_ROWS-1]=tail;prefix_cursor=(prefix_cursor+1)%PREFIX_ROWS;
    prefix_rotations++;
}
static void prefix_seek(unsigned address) {
    while(prefix_cursor!=address)prefix_step(0);
}
static void prefix_begin(void) {
    prefix_rotations=prefix_reads=prefix_writes=0;prefix_seek(0);prefix_homing=prefix_rotations;
}
static void prefix_read_word(unsigned address) {
#ifdef BAD_PREFIX_READ
    address=(address+1)%PREFIX_ROWS;
#endif
    prefix_seek(address);prefix_reads++;
}
static void prefix_store_word(unsigned address,const int32_t *in) {
    prefix_seek(address);prefix_step(in);prefix_writes++;
}
static void prefix_store_row(unsigned pos,const int32_t *in) {
    for(unsigned c=0;c<4;c++)prefix_store_word(4*pos+c,in+32*c);
}
static void prefix_load_row(unsigned pos,int32_t *out) {
    for(unsigned c=0;c<4;c++) {
        prefix_read_word(4*pos+c);
        for(unsigned i=0;i<32;i++)out[c*32+i]=prefix_words[0].lane[i];
    }
}
static void prefix_norm(unsigned pos,int which,int32_t *h) {
    prefix_load_row(pos,h);norm(h,which,h);
}
static void prefix_residual(unsigned pos,int32_t *work) {
    for(unsigned c=0;c<4;c++) {
        prefix_read_word(4*pos+c);
        for(unsigned i=0;i<32;i++)work[c*32+i]=sat((int64_t)prefix_words[0].lane[i]+work[c*32+i]);
        prefix_store_word(4*pos+c,work+32*c);
    }
}
static int32_t prefix_peek(unsigned pos,unsigned lane) {
    unsigned physical=(4*pos+lane/32+PREFIX_ROWS-prefix_cursor)%PREFIX_ROWS;
    return prefix_words[physical].lane[lane%32];
}
uint64_t prefix_count(unsigned field) {
    return field==0?prefix_rotations:field==1?prefix_reads:field==2?prefix_writes:prefix_homing;
}
