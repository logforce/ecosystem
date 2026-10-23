// Separate v3: reuse the unchanged tile16/quad4 scan, adding exact FP32 columns.
// The build cache hashes this file and kernels_packed.cpp together.
#include "kernels_packed.cpp"

static inline double mixed_score(double tail,const float* values,const float* query,size_t k) {
    // Each product of two finite FP32 values is exact in binary64. The sums
    // round in this declared column order, with contraction disabled at build.
    for(size_t j=0;j<k;++j)tail+=double(values[j])*double(query[j]);
    return tail;
}

struct MixedOutput {
    const float* values;
    const float* query;
    size_t columns;
    double* output;
    inline void accept(size_t row,double score) {
        output[row]=mixed_score(score,values+row*columns,query,columns);
    }
};
struct MixedFilter {
    const float* values;
    const float* query;
    size_t columns;
    GlobalFilter bounds;
    inline void accept(size_t row,double score) {
        bounds.accept(row,mixed_score(score,values+row*columns,query,columns));
    }
};

static int mixed_coarse(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                         const float* values,const float* exact_query,double qs,
                         size_t n,size_t d,size_t columns,double* output,bool portable) {
    if(!ieee_environment())return -1;
    if(!n||!d||d>65536||!columns||columns>d)return -2;
    MixedOutput sink{values,exact_query,columns,output};
    dispatch(p,s,sums,q,qs,n,d,sink,portable);
    return 1;
}
static int mixed_global(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                         const float* values,const float* exact_query,double qs,double radius,
                         size_t n,size_t d,size_t columns,size_t k,
                         double* upper,double* heap,double* threshold,bool portable) {
    if(!ieee_environment())return -1;
    if(!n||!d||d>65536||!columns||columns>d||!k||k>n||!std::isfinite(radius)||radius<0)return -2;
    MixedFilter sink{values,exact_query,columns,GlobalFilter{radius,upper,heap,k}};
    dispatch(p,s,sums,q,qs,n,d,sink,portable);
    threshold[0]=next_down(heap[0]-radius);
    return sink.bounds.valid && std::isfinite(threshold[0])?1:0;
}

extern "C" {
const char* etex_mixed_backend() {
#if PACKED_X86
    if(__builtin_cpu_supports("avx512vnni") && __builtin_cpu_supports("avx512bw")
       && __builtin_cpu_supports("avx512f"))return "avx512_vnni_tile16_quad4_fp32_columns";
#endif
    return "portable_tile16_quad4_fp32_columns";
}
int etex_mixed_coarse(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                       const float* values,const float* eq,double qs,
                       size_t n,size_t d,size_t columns,double* out) {
    return mixed_coarse(p,s,sums,q,values,eq,qs,n,d,columns,out,false);
}
int etex_mixed_coarse_portable(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                                const float* values,const float* eq,double qs,
                                size_t n,size_t d,size_t columns,double* out) {
    return mixed_coarse(p,s,sums,q,values,eq,qs,n,d,columns,out,true);
}
int etex_mixed_global(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                       const float* values,const float* eq,double qs,double radius,
                       size_t n,size_t d,size_t columns,size_t k,double* upper,double* heap,double* threshold) {
    return mixed_global(p,s,sums,q,values,eq,qs,radius,n,d,columns,k,upper,heap,threshold,false);
}
int etex_mixed_global_portable(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                                const float* values,const float* eq,double qs,double radius,
                                size_t n,size_t d,size_t columns,size_t k,double* upper,double* heap,double* threshold) {
    return mixed_global(p,s,sums,q,values,eq,qs,radius,n,d,columns,k,upper,heap,threshold,true);
}
}
