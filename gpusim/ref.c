/* CPU reference / baseline: same records, 64 lanes per uint64 word, OpenMP over words.
 * Layout matches the GPU kernel: value[wire][word], inputs [t][i][word], outputs [t][o][word]. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
void ref_run(const uint32_t *rec,uint32_t n_in,uint32_t n_rec,uint32_t n_out,
             const uint32_t *latd,uint32_t n_lat,uint32_t W,uint32_t T,
             const uint64_t *inp,uint64_t *state,uint64_t *outp){
    uint32_t nw=2+n_in+n_rec,base=nw-n_out;
    #pragma omp parallel for schedule(static)
    for(uint32_t w=0;w<W;w++){
        uint64_t *v=malloc(sizeof(uint64_t)*nw);
        v[0]=0;v[1]=~0ull;
        for(uint32_t t=0;t<T;t++){
            for(uint32_t i=0;i<n_in;i++)v[2+i]=inp[((uint64_t)t*n_in+i)*W+w];
            uint32_t k=0;
            for(uint32_t r=0;r<n_rec;r++){
                uint32_t a=rec[2*r],b=rec[2*r+1];
                v[2+n_in+r]=(a>>31)?state[(uint64_t)(k++)*W+w]:~(v[a]&v[b]);
            }
            for(uint32_t j=0;j<n_lat;j++)state[(uint64_t)j*W+w]=v[latd[j]];
            for(uint32_t o=0;o<n_out;o++)outp[((uint64_t)t*n_out+o)*W+w]=v[base+o];
        }
        free(v);
    }
}
