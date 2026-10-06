/* SPDX-License-Identifier: CC0-1.0
 * TC16I001: portable C99 integer-only golden. No floating types or libm.
 * One instance, one inference at a time; all logical state widths in INT_SPEC.md.
 */
#include <stdint.h>
#include <stddef.h>

static int8_t weights[972800], embedding[192*128];
static uint32_t alpha[35], escale[192], exptab[1025], sigtab[1025];
static int16_t norms[11*128];
static int32_t costab[32*16], sintab[32*16];
static int32_t x[32][128];
static int8_t keys[32][4][32], values[32][4][32];
static int32_t km[32][4], vm[32][4];
static uint64_t saturated, largest;
static int loaded;
static const int offsets[7]={0,16384,32768,49152,65536,108544,151552};

int64_t int_rne(int64_t n,int64_t d) {
    uint64_t a=(uint64_t)(n<0?-n:n),q=a/(uint64_t)d,r=a%(uint64_t)d;
#ifdef INT_BAD_ROUND
    if(2*r>=(uint64_t)d)q++;
#else
    if(2*r>(uint64_t)d || (2*r==(uint64_t)d && (q&1)))q++;
#endif
    return n<0?-(int64_t)q:(int64_t)q;
}
static int32_t sat(int64_t a) {
    uint64_t m=(uint64_t)(a<0?-a:a);if(m>largest)largest=m;
    if(a>524287){saturated++;return 524287;}
    if(a< -524288){saturated++;return -524288;}
    return (int32_t)a;
}
uint32_t int_sqrt(uint64_t a) {
    uint64_t r=0,b=(uint64_t)1<<46;
    for(int i=0;i<24;i++,b>>=2) {
        if(a>=r+b){a-=r+b;r=(r>>1)+b;}else r>>=1;
    }
    return (uint32_t)r;
}
static uint32_t read32(const uint8_t **p) {
    uint32_t r=(*p)[0]|((uint32_t)(*p)[1]<<8)|((uint32_t)(*p)[2]<<16)|((uint32_t)(*p)[3]<<24);
    *p+=4;return r;
}
static int32_t signed32(uint32_t u) {return u<=2147483647U?(int32_t)u:(int32_t)((int64_t)u-4294967296LL);}
int int_init(const uint8_t *blob,int length) {
    const char magic[]="TC16I001";if(length!=283804)return -1;
    for(int i=0;i<8;i++)if(blob[i]!=(uint8_t)magic[i])return -2;
    const uint8_t *p=blob+8;
    for(int i=0;i<972800;i++){
        unsigned q=(p[i/4]>>(2*(i%4)))&3;if(q==3)return -3;
        weights[i]=(int8_t)(q==2?-1:(int)q);
    }
    p+=243200;
    for(int i=0;i<35;i++) {alpha[i]=read32(&p);if(alpha[i]>=262144)return -4;}
    for(int i=0;i<192*128;i++){unsigned u=*p++;embedding[i]=(int8_t)(u<128?(int)u:(int)u-256);}
    for(int i=0;i<192;i++){escale[i]=read32(&p);if(escale[i]>=262144)return -5;}
    for(int i=0;i<11*128;i++){unsigned u=p[0]|((unsigned)p[1]<<8);p+=2;norms[i]=(int16_t)(u<32768?(int)u:(int)u-65536);}
    for(int i=0;i<32*16;i++)costab[i]=signed32(read32(&p));
    for(int i=0;i<32*16;i++)sintab[i]=signed32(read32(&p));
    for(int i=0;i<1025;i++)exptab[i]=read32(&p);
    for(int i=0;i<1025;i++)sigtab[i]=read32(&p);
    loaded=1;return p-blob==length?0:-6;
}
static int32_t quant(const int32_t *in,int n,int8_t *out) {
    int32_t m=1;for(int i=0;i<n;i++){int32_t v=in[i]<0?-in[i]:in[i];if(v>m)m=v;}
    for(int i=0;i<n;i++)out[i]=(int8_t)int_rne((int64_t)in[i]*127,m);
    return m;
}
static void norm(const int32_t *in,int which,int32_t *out) {
    uint64_t sum=0;for(int i=0;i<128;i++)sum+=(uint64_t)((int64_t)in[i]*in[i]);
    uint32_t root=int_sqrt(2*sum+4295);if(!root)root=1;
    for(int i=0;i<128;i++)out[i]=sat(int_rne((int64_t)in[i]*norms[which*128+i]*4,root));
}
static void linear(const int32_t *in,int layer,int matrix,int32_t *out) {
    int n=matrix==6?336:128, rows=(matrix==4||matrix==5)?336:128;
    int8_t q[336];int32_t m=quant(in,n,q);
    const int8_t *w=weights+layer*194560+offsets[matrix];
    for(int j=0;j<rows;j++) {
        int32_t dot=0;for(int i=0;i<n;i++)dot+=(int32_t)q[i]*w[j*n+i];
        out[j]=sat(int_rne((int64_t)dot*m*alpha[layer*7+matrix],33292288));
    }
}
static void rope(int32_t *in,int position) {
    for(int h=0;h<4;h++)for(int i=0;i<16;i++) {
        int32_t a=in[h*32+i],b=in[h*32+i+16],c=costab[position*16+i],s=sintab[position*16+i];
        in[h*32+i]=sat(int_rne((int64_t)a*c-(int64_t)b*s,32768));
        in[h*32+i+16]=sat(int_rne((int64_t)b*c+(int64_t)a*s,32768));
    }
}
static uint32_t exp_weight(int64_t delta) {
    int64_t j=int_rne(delta,64);return j>1024?0:exptab[j];
}
static int32_t kv_dequant(int8_t q,int32_t m) {
    return sat(int_rne((int64_t)q*m,127));
}
static void attention(const int32_t *q,int pos,int32_t *out) {
    int32_t scores[32];uint32_t w[32];
    for(int h=0;h<4;h++) {
        int32_t maximum=INT32_MIN;
        for(int s=0;s<=pos;s++) {
            int64_t dot=0;
            for(int i=0;i<32;i++)dot+=(int64_t)q[h*32+i]*kv_dequant(keys[s][h][i],km[s][h]);
            scores[s]=(int32_t)int_rne(int_rne(dot,4096)*46341,262144);
            if(scores[s]>maximum)maximum=scores[s];
        }
        uint32_t denominator=0;
        for(int s=0;s<=pos;s++){w[s]=exp_weight((int64_t)maximum-scores[s]);denominator+=w[s];}
        for(int i=0;i<32;i++) {
            int64_t numerator=0;
            for(int s=0;s<=pos;s++)numerator+=(int64_t)w[s]*kv_dequant(values[s][h][i],vm[s][h]);
            out[h*32+i]=sat(int_rne(numerator,denominator));
        }
    }
}
int int_run(const int32_t *tokens,int length,int32_t *logits,int32_t *trace) {
    if(!loaded || length<1 || length>32)return -1;
    saturated=0;largest=0;
    for(int p=0;p<length;p++) {
        int token=tokens[p];if(token<0||token>=192)return -2;
        for(int i=0;i<128;i++)x[p][i]=sat(int_rne((int64_t)embedding[token*128+i]*escale[token],4096));
    }
    if(trace)for(int p=0;p<length;p++)for(int i=0;i<128;i++)trace[p*128+i]=x[p][i];
    for(int layer=0;layer<5;layer++) {
        int32_t h[128],a[128],ff[336];
        /* All old-prefix K/V must be made before overwriting any X row. */
        for(int p=0;p<length;p++) {
            norm(x[p],2*layer,h);linear(h,layer,1,a);rope(a,p);
            for(int j=0;j<4;j++)km[p][j]=quant(a+j*32,32,keys[p][j]);
            linear(h,layer,2,a);
            for(int j=0;j<4;j++)vm[p][j]=quant(a+j*32,32,values[p][j]);
        }
        for(int p=0;p<length;p++) {
            norm(x[p],2*layer,h);linear(h,layer,0,a);rope(a,p);
            attention(a,p,h);linear(h,layer,3,a);
            for(int i=0;i<128;i++)x[p][i]=sat((int64_t)x[p][i]+a[i]);
            norm(x[p],2*layer+1,h);
            int8_t qh[128];int32_t m=quant(h,128,qh);
            for(int i=0;i<336;i++) {
                const int8_t *wg=weights+layer*194560+offsets[4]+i*128;
                const int8_t *wu=weights+layer*194560+offsets[5]+i*128;
                int32_t dg=0,du=0;
                for(int j=0;j<128;j++){dg+=(int32_t)qh[j]*wg[j];du+=(int32_t)qh[j]*wu[j];}
                int32_t g=sat(int_rne((int64_t)dg*m*alpha[layer*7+4],33292288));
                int32_t u=sat(int_rne((int64_t)du*m*alpha[layer*7+5],33292288));
                int64_t j=int_rne(g<0?-(int64_t)g:g,64);if(j>1024)j=1024;
                uint32_t s=g<0?65536-sigtab[j]:sigtab[j];
                ff[i]=sat(int_rne((int64_t)sat(int_rne((int64_t)g*s,65536))*u,4096));
            }
            linear(ff,layer,6,h);
            for(int i=0;i<128;i++)x[p][i]=sat((int64_t)x[p][i]+h[i]);
        }
        if(trace)for(int p=0;p<length;p++)for(int i=0;i<128;i++)trace[((layer+1)*length+p)*128+i]=x[p][i];
    }
    for(int p=0;p<length;p++) {
        int32_t h[128];int8_t q[128];norm(x[p],10,h);int32_t m=quant(h,128,q);
        for(int j=0;j<192;j++) {
            int32_t dot=0;for(int i=0;i<128;i++)dot+=(int32_t)q[i]*embedding[j*128+i];
            logits[p*192+j]=(int32_t)int_rne(int_rne((int64_t)dot*m,127)*escale[j],16777216);
        }
    }
    return 0;
}
uint64_t int_saturations(void){return saturated;}
uint64_t int_largest(void){return largest;}
uint32_t int_mutate(int table,int index,uint32_t value) {
    uint32_t *p=table?sigtab:exptab,old=p[index];p[index]=value;return old;
}
int int_pick(const int32_t *logits,uint32_t random,int sample) {
    int order[192];for(int i=0;i<192;i++) {
        int j=i;while(j>0 && logits[order[j-1]]<logits[i]){order[j]=order[j-1];j--;}order[j]=i;
    }
    if(!sample)return order[0];
    uint32_t w[40],total=0;
    for(int i=0;i<40;i++){w[i]=exp_weight(int_rne(5*((int64_t)logits[order[0]]-logits[order[i]]),4));total+=w[i];}
    uint32_t at=(uint32_t)(((uint64_t)random*total)>>32);
    for(int i=0;i<40;i++){if(at<w[i])return order[i];at-=w[i];}
    return order[39];
}
