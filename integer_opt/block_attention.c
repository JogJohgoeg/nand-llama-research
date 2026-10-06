static void attention(const int32_t *q,int pos,int32_t *out,int32_t *scratch) {
    int32_t scores[32];
    for(int h=0;h<4;h++) {
        int32_t *cache=out+h*32,maximum=INT32_MIN;
        for(int s=0;s<=pos;s++) {
            ring_cache(h,s,0,cache);int64_t dot=0;
            for(int i=0;i<32;i++)dot+=(int64_t)q[h*32+i]*cached_dequant(cache,i);
            scores[s]=(int32_t)int_rne(int_rne(dot,4096)*46341,262144);
            if(scores[s]>maximum)maximum=scores[s];
        }
        uint32_t denominator=0;
        for(int s=0;s<=pos;s++){scores[s]=(int32_t)exp_weight((int64_t)maximum-scores[s]);denominator+=(uint32_t)scores[s];}
        assert(acc_cursor==0);
        for(int i=0;i<32;i++)acc_store(scratch,i,0);
        for(int s=0;s<=pos;s++) {
            ring_cache(h,s,1,cache);
            for(int i=0;i<32;i++) {
                int64_t numerator=acc_load(scratch,i);
                numerator+=(int64_t)scores[s]*cached_dequant(cache,i);
                acc_store(scratch,i,numerator);
            }
        }
        for(int i=0;i<32;i++)out[h*32+i]=sat(int_rne(acc_load(scratch,i),denominator));
        acc_home();
    }
}
