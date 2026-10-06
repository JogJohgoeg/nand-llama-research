/* Storage refinement of INT_SPEC selection: 40 scores + IDs, no weight cache.
 * Exact second-pass EXP recomputation is already an allowed implementation.
 * Host entry arrays contain 1,600 logical bits; counter fields are diagnostics.
 */
#include "../integer/int_model.c"

static int32_t top_score[40];
static uint8_t top_id[40];
static unsigned top_count;
static uint64_t comparisons, moves, weight_evaluations;

void stream_begin(void) {
    top_count=0;comparisons=moves=weight_evaluations=0;
}

void stream_logit(unsigned id,int32_t score) {
    unsigned j=top_count;
    if(top_count<40)top_count++;
    while(j>0) {
        comparisons++;
#ifdef BAD_STREAM_TIE
        if(top_score[j-1]>score)break;
#else
        if(top_score[j-1]>=score)break;
#endif
        if(j<40) {
            top_score[j]=top_score[j-1];top_id[j]=top_id[j-1];moves++;
        }
        j--;
    }
    if(j<40) {top_score[j]=score;top_id[j]=(uint8_t)id;moves++;}
}

static uint32_t stream_weight(unsigned i) {
    weight_evaluations++;
    return exp_weight(int_rne(5*((int64_t)top_score[0]-top_score[i]),4));
}

int stream_pick(uint32_t random,int sample) {
    if(top_count!=40)return -1;
    if(!sample)return top_id[0];
    uint32_t total=0;
    for(unsigned i=0;i<40;i++)total+=stream_weight(i);
    uint32_t at=(uint32_t)(((uint64_t)random*total)>>32);
    for(unsigned i=0;i<40;i++) {
#ifdef BAD_REPLAY_WEIGHT
        uint32_t w=stream_weight((i+1)%40);
#else
        uint32_t w=stream_weight(i);
#endif
        if(at<w)return top_id[i];
        at-=w;
    }
    return top_id[39];
}

uint64_t stream_count(unsigned field) {
    return field==0?comparisons:field==1?moves:weight_evaluations;
}
unsigned stream_order(unsigned i) {return i<top_count?top_id[i]:255;}
