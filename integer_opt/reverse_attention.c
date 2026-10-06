/* Experimental exact schedule, not a change to TC16I001 arithmetic.
 * Descending positions leave all source rows 0..p untouched in this layer.
 * H holds attention results; A is the norm/A8 scratch. During attention the
 * otherwise dead 336-byte FF bank packs Q20[32], tmp20[32], acc42[32]:2624 bits.
 * Host containers below are wider; there is no history-shaped KV allocation.
 */
typedef union {
    int32_t ff[336];
    struct {int32_t q[32],tmp[32];int64_t acc[32];} attention;
} reverse_work;
static uint64_t reverse_stats[8];
static uint8_t rewritten[32];
uint64_t reverse_count(unsigned i){return i<8?reverse_stats[i]:0;}
static void reverse_norm_codes(int position,int layer,int32_t *scratch,int32_t *maximum) {
    reverse_stats[0]++;
    if(rewritten[position])reverse_stats[7]++;
    norm(x[position],2*layer,scratch);
    *maximum=quant_in_place(scratch,128);
}
static void reverse_linear_head(const int32_t *q,int32_t m,int layer,int matrix,int head,int32_t *out) {
    const int8_t *w=weights+layer*194560+offsets[matrix]+head*32*128;
    reverse_stats[1+matrix]+=32;
    for(int j=0;j<32;j++) {
        int32_t dot=0;for(int i=0;i<128;i++)dot+=q[i]*w[j*128+i];
        out[j]=sat(int_rne((int64_t)dot*m*alpha[layer*7+matrix],33292288));
    }
}
static void reverse_rope_head(int32_t *in,int position) {
    for(int i=0;i<16;i++) {
        int32_t a=in[i],b=in[i+16],c=costab[position*16+i],s=sintab[position*16+i];
        in[i]=sat(int_rne((int64_t)a*c-(int64_t)b*s,32768));
        in[i+16]=sat(int_rne((int64_t)b*c+(int64_t)a*s,32768));
    }
}
static void reverse_attention(int pos,int layer,int32_t *out,int32_t *scratch,reverse_work *work) {
    int32_t scores[32];
    int32_t *q=work->attention.q,*tmp=work->attention.tmp;
    int64_t *acc=work->attention.acc;
    for(int head=0;head<4;head++) {
        int32_t m;reverse_norm_codes(pos,layer,scratch,&m);
        reverse_linear_head(scratch,m,layer,0,head,q);reverse_rope_head(q,pos);
        int32_t maximum=INT32_MIN;
        for(int s=0;s<=pos;s++) {
            reverse_norm_codes(s,layer,scratch,&m);
            reverse_linear_head(scratch,m,layer,1,head,tmp);reverse_rope_head(tmp,s);
            m=quant_in_place(tmp,32);reverse_stats[4]++;
            int64_t dot=0;
            for(int i=0;i<32;i++)dot+=(int64_t)q[i]*kv_dequant((int8_t)tmp[i],m);
            scores[s]=(int32_t)int_rne(int_rne(dot,4096)*46341,262144);
            if(scores[s]>maximum)maximum=scores[s];
        }
        uint32_t den=0;
        for(int s=0;s<=pos;s++) {
            scores[s]=(int32_t)exp_weight((int64_t)maximum-scores[s]);den+=(uint32_t)scores[s];
        }
        for(int i=0;i<32;i++)acc[i]=0;
        for(int s=0;s<=pos;s++) {
            reverse_norm_codes(s,layer,scratch,&m);
            reverse_linear_head(scratch,m,layer,2,head,tmp);
            m=quant_in_place(tmp,32);reverse_stats[5]++;
#ifdef BAD_STREAM_VALUE
            tmp[0]^=1; /* Actual consumed signed8 V code bit corruption. */
#endif
            for(int i=0;i<32;i++) {
                acc[i]+=(int64_t)(uint32_t)scores[s]*kv_dequant((int8_t)tmp[i],m);
                uint64_t a=(uint64_t)(acc[i]<0?-acc[i]:acc[i]);
                if(a>reverse_stats[6])reverse_stats[6]=a;
            }
        }
        for(int i=0;i<32;i++)out[head*32+i]=sat(int_rne(acc[i],den));
    }
}
