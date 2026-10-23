// Independent tile16 x quad4 variant. The v1 kernel and format remain unchanged.
#include <algorithm>
#include <cfenv>
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <functional>
#include <limits>

#if defined(__x86_64__) || defined(__i386__)
#include <immintrin.h>
#define PACKED_X86 1
#else
#define PACKED_X86 0
#endif

static_assert(sizeof(double)==8 && std::numeric_limits<double>::is_iec559,
              "The packed interval implementation requires IEEE binary64");

static inline bool ieee_environment() {
    if(std::fegetround()!=FE_TONEAREST)return false;
#if defined(__x86_64__)
    return (_mm_getcsr() & ((1u<<15)|(1u<<6)|(3u<<13)))==0;
#else
    return false;
#endif
}
static inline double next_up(double value) {
    if(std::isnan(value)||value==std::numeric_limits<double>::infinity())return value;
    uint64_t bits;std::memcpy(&bits,&value,8);
    if(value==0)bits=1;
    else if(bits>>63)--bits;
    else ++bits;
    std::memcpy(&value,&bits,8);return value;
}
static inline double next_down(double value) {
    if(std::isnan(value)||value==-std::numeric_limits<double>::infinity())return value;
    uint64_t bits;std::memcpy(&bits,&value,8);
    if(value==0)bits=(uint64_t(1)<<63)|1;
    else if(bits>>63)++bits;
    else --bits;
    std::memcpy(&value,&bits,8);return value;
}

struct CoarseOutput {
    double* output;
    inline void accept(size_t row,double score) {output[row]=score;}
};
struct GlobalFilter {
    double radius,*upper,*heap;
    size_t k,count=0;
    bool valid=true;
    inline void accept(size_t row,double coarse) {
        double hi=next_up(coarse+radius);
        if(!std::isfinite(coarse)||!std::isfinite(hi)) {
            valid=false;coarse=-std::numeric_limits<double>::infinity();
            hi=std::numeric_limits<double>::infinity();
        }
        upper[row]=hi;
        if(count<k) {
            heap[count++]=coarse;
            std::push_heap(heap,heap+count,std::greater<double>());
        } else if(coarse>heap[0]) {
            std::pop_heap(heap,heap+k,std::greater<double>());
            heap[k-1]=coarse;
            std::push_heap(heap,heap+k,std::greater<double>());
        }
    }
};

// Packed offset: (((tile*quads+quad)*16+lane)*4+byte).
// qwords contains unsigned(qcode+128), with zero bytes past the logical D.
template<class Sink>
static void scan_scalar(const int8_t* packed,const float* scales,const int32_t* sums,
                        const uint32_t* qwords,double qs,size_t n,size_t d,Sink& sink) {
    const size_t quads=(d+3)/4,tiles=(n+15)/16;
    const auto* qbytes=reinterpret_cast<const uint8_t*>(qwords);
    for(size_t tile=0;tile<tiles;++tile) {
        const size_t row0=tile*16,valid=std::min(size_t(16),n-row0);
        const int8_t* base=packed+tile*quads*64;
        int32_t lanes[16]={};
        for(size_t quad=0;quad<quads;++quad) {
            for(size_t lane=0;lane<valid;++lane) {
                const int8_t* v=base+quad*64+lane*4;
                int32_t term=0;
                for(size_t b=0;b<4;++b)term+=int32_t(v[b])*int32_t(qbytes[quad*4+b]);
                lanes[lane]+=term;
            }
        }
        for(size_t lane=0;lane<valid;++lane) {
            const size_t row=row0+lane;
            const int64_t dot=int64_t(lanes[lane])-int64_t(128)*sums[row];
            sink.accept(row,double(dot)*double(scales[row])*qs);
        }
    }
}

#if PACKED_X86
static inline int32_t signed_word(uint32_t value) {
    int32_t result;std::memcpy(&result,&value,4);return result;
}

template<class Sink>
__attribute__((target("avx512f,avx512bw,avx512vnni")))
static void scan_vnni(const int8_t* packed,const float* scales,const int32_t* sums,
                      const uint32_t* qwords,double qs,size_t n,size_t d,Sink& sink) {
    const size_t quads=(d+3)/4,tiles=(n+15)/16;
    for(size_t tile=0;tile<tiles;++tile) {
        const size_t row0=tile*16,valid=std::min(size_t(16),n-row0);
        const int8_t* base=packed+tile*quads*64;
        // Every int32 lane belongs to a different original row. Four chains
        // reduce dpbusd dependency latency without horizontal row reductions.
        __m512i a0=_mm512_setzero_si512(),a1=a0,a2=a0,a3=a0;
        size_t quad=0;
        for(;quad+4<=quads;quad+=4) {
            a0=_mm512_dpbusd_epi32(a0,_mm512_set1_epi32(signed_word(qwords[quad])),
                                  _mm512_loadu_si512(base+(quad+0)*64));
            a1=_mm512_dpbusd_epi32(a1,_mm512_set1_epi32(signed_word(qwords[quad+1])),
                                  _mm512_loadu_si512(base+(quad+1)*64));
            a2=_mm512_dpbusd_epi32(a2,_mm512_set1_epi32(signed_word(qwords[quad+2])),
                                  _mm512_loadu_si512(base+(quad+2)*64));
            a3=_mm512_dpbusd_epi32(a3,_mm512_set1_epi32(signed_word(qwords[quad+3])),
                                  _mm512_loadu_si512(base+(quad+3)*64));
        }
        __m512i sum=_mm512_add_epi32(_mm512_add_epi32(a0,a1),_mm512_add_epi32(a2,a3));
        for(;quad<quads;++quad)
            sum=_mm512_dpbusd_epi32(sum,_mm512_set1_epi32(signed_word(qwords[quad])),
                                   _mm512_loadu_si512(base+quad*64));
        alignas(64) int32_t lanes[16];_mm512_store_si512(lanes,sum);
        // At D<=65536 even unsigned-query accumulation fits int32 per row:
        // [-128,127] * [0,255] * 65536 is within the signed 32-bit range.
        // Apply the correction in int64; scaled products retain v1 ordering.
        for(size_t lane=0;lane<valid;++lane) {
            const size_t row=row0+lane;
            const int64_t dot=int64_t(lanes[lane])-int64_t(128)*sums[row];
            sink.accept(row,double(dot)*double(scales[row])*qs);
        }
    }
}
#endif

template<class Sink>
static void dispatch(const int8_t* packed,const float* scales,const int32_t* sums,
                     const uint32_t* qwords,double qs,size_t n,size_t d,Sink& sink,bool portable) {
#if PACKED_X86
    if(!portable && __builtin_cpu_supports("avx512vnni") && __builtin_cpu_supports("avx512bw")
                 && __builtin_cpu_supports("avx512f")) {
        scan_vnni(packed,scales,sums,qwords,qs,n,d,sink);return;
    }
#endif
    scan_scalar(packed,scales,sums,qwords,qs,n,d,sink);
}

static int run_global(const int8_t* packed,const float* scales,const int32_t* sums,
                      const uint32_t* qwords,double qs,double radius,size_t n,size_t d,size_t k,
                      double* upper,double* heap,double* threshold,bool portable) {
    if(!ieee_environment())return -1;
    if(!n||!k||k>n||!d||d>65536||!std::isfinite(radius)||radius<0)return -2;
    GlobalFilter filter{radius,upper,heap,k};
    dispatch(packed,scales,sums,qwords,qs,n,d,filter,portable);
    threshold[0]=next_down(heap[0]-radius);
    return filter.valid && std::isfinite(threshold[0])?1:0;
}
static int run_coarse(const int8_t* packed,const float* scales,const int32_t* sums,
                      const uint32_t* qwords,double qs,size_t n,size_t d,double* output,bool portable) {
    if(!ieee_environment())return -1;
    if(!n||!d||d>65536)return -2;
    CoarseOutput sink{output};
    dispatch(packed,scales,sums,qwords,qs,n,d,sink,portable);
    return 1;
}

extern "C" {
const char* etex_packed_backend() {
#if PACKED_X86
    if(__builtin_cpu_supports("avx512vnni") && __builtin_cpu_supports("avx512bw")
       && __builtin_cpu_supports("avx512f"))return "avx512_vnni_tile16_quad4";
#endif
    return "portable_tile16_quad4";
}
int etex_packed_coarse(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                       double qs,size_t n,size_t d,double* out) {
    return run_coarse(p,s,sums,q,qs,n,d,out,false);
}
int etex_packed_coarse_portable(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                                double qs,size_t n,size_t d,double* out) {
    return run_coarse(p,s,sums,q,qs,n,d,out,true);
}
int etex_packed_global(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                       double qs,double radius,size_t n,size_t d,size_t k,double* upper,double* heap,double* threshold) {
    return run_global(p,s,sums,q,qs,radius,n,d,k,upper,heap,threshold,false);
}
int etex_packed_global_portable(const int8_t* p,const float* s,const int32_t* sums,const uint32_t* q,
                                double qs,double radius,size_t n,size_t d,size_t k,double* upper,double* heap,double* threshold) {
    return run_global(p,s,sums,q,qs,radius,n,d,k,upper,heap,threshold,true);
}
}
