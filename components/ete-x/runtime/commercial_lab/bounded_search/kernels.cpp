#include <cstdint>
#include <cstddef>
#include <cfenv>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <functional>
#include <limits>
#if defined(__x86_64__) || defined(__i386__)
#include <immintrin.h>
#define X86 1
#else
#define X86 0
#endif

static void exact_scalar(const float* x, const double* q, size_t n, size_t d, double* out) {
    for (size_t r=0;r<n;++r) {
        double a[8]={0,0,0,0,0,0,0,0};
        for(size_t j=0;j<d;++j) a[j%8] += double(x[r*d+j])*q[j];
        double v=0; for(int j=0;j<8;++j) v+=a[j]; out[r]=v;
    }
}
static void float_scalar(const float* x, const float* q, size_t n, size_t d, float* out) {
    for(size_t r=0;r<n;++r) {
        float a[16]={};
        for(size_t j=0;j<d;++j) a[j%16]+=x[r*d+j]*q[j];
        float v=0;for(int j=0;j<16;++j)v+=a[j];out[r]=v;
    }
}
static void coarse_scalar(const int8_t* x,const float* scales,const int32_t*,const int8_t* q,
                          double qs,size_t n,size_t d,double* out) {
    for(size_t r=0;r<n;++r) {
        int32_t a=0;for(size_t j=0;j<d;++j)a+=int32_t(x[r*d+j])*int32_t(q[j]);
        out[r]=double(a)*double(scales[r])*qs;
    }
}
static inline int64_t dot_i8_scalar(const int8_t* x,const int8_t* q,size_t d,int32_t) {
    int32_t sum=0;for(size_t j=0;j<d;++j)sum+=int32_t(x[j])*int32_t(q[j]);
    return sum;
}
#if X86
__attribute__((target("avx2")))
static void exact_avx2(const float* x,const double* q,size_t n,size_t d,double* out) {
    for(size_t r=0;r<n;++r) {
        __m256d a=_mm256_setzero_pd(),b=_mm256_setzero_pd();size_t j=0;
        for(;j+8<=d;j+=8) {
            a=_mm256_add_pd(a,_mm256_mul_pd(_mm256_cvtps_pd(_mm_loadu_ps(x+r*d+j)),_mm256_loadu_pd(q+j)));
            b=_mm256_add_pd(b,_mm256_mul_pd(_mm256_cvtps_pd(_mm_loadu_ps(x+r*d+j+4)),_mm256_loadu_pd(q+j+4)));
        }
        double lanes[8];_mm256_storeu_pd(lanes,a);_mm256_storeu_pd(lanes+4,b);
        for(;j<d;++j)lanes[j%8]+=double(x[r*d+j])*q[j];
        double v=0;for(int z=0;z<8;++z)v+=lanes[z];out[r]=v;
    }
}
__attribute__((target("avx512f,avx512bw")))
static void exact_avx512(const float* x,const double* q,size_t n,size_t d,double* out) {
    for(size_t r=0;r<n;++r) {
        __m512d a=_mm512_setzero_pd();size_t j=0;
        for(;j+8<=d;j+=8)a=_mm512_add_pd(a,_mm512_mul_pd(_mm512_cvtps_pd(_mm256_loadu_ps(x+r*d+j)),_mm512_loadu_pd(q+j)));
        double lanes[8];_mm512_storeu_pd(lanes,a);
        for(;j<d;++j)lanes[j%8]+=double(x[r*d+j])*q[j];
        double v=0;for(int z=0;z<8;++z)v+=lanes[z];out[r]=v;
    }
}
__attribute__((target("avx512f")))
static void float_avx512(const float* x,const float* q,size_t n,size_t d,float* out) {
    for(size_t r=0;r<n;++r) {
        __m512 a=_mm512_setzero_ps();size_t j=0;
        for(;j+16<=d;j+=16)a=_mm512_add_ps(a,_mm512_mul_ps(_mm512_loadu_ps(x+r*d+j),_mm512_loadu_ps(q+j)));
        float lanes[16];_mm512_storeu_ps(lanes,a);
        for(;j<d;++j)lanes[j%16]+=x[r*d+j]*q[j];
        float v=0;for(int z=0;z<16;++z)v+=lanes[z];out[r]=v;
    }
}
__attribute__((target("avx2")))
static void float_avx2(const float* x,const float* q,size_t n,size_t d,float* out) {
    for(size_t r=0;r<n;++r) {
        __m256 a=_mm256_setzero_ps(),b=_mm256_setzero_ps();size_t j=0;
        for(;j+16<=d;j+=16) {
            a=_mm256_add_ps(a,_mm256_mul_ps(_mm256_loadu_ps(x+r*d+j),_mm256_loadu_ps(q+j)));
            b=_mm256_add_ps(b,_mm256_mul_ps(_mm256_loadu_ps(x+r*d+j+8),_mm256_loadu_ps(q+j+8)));
        }
        float lanes[16];_mm256_storeu_ps(lanes,a);_mm256_storeu_ps(lanes+8,b);
        for(;j<d;++j)lanes[j%16]+=x[r*d+j]*q[j];
        float v=0;for(int z=0;z<16;++z)v+=lanes[z];out[r]=v;
    }
}
__attribute__((target("avx2")))
static void coarse_avx2(const int8_t* x,const float* scales,const int32_t*,const int8_t* q,
                        double qs,size_t n,size_t d,double* out) {
    for(size_t r=0;r<n;++r) {
        __m256i a=_mm256_setzero_si256();size_t j=0;
        for(;j+16<=d;j+=16) {
            __m256i xv=_mm256_cvtepi8_epi16(_mm_loadu_si128((const __m128i*)(x+r*d+j)));
            __m256i qv=_mm256_cvtepi8_epi16(_mm_loadu_si128((const __m128i*)(q+j)));
            a=_mm256_add_epi32(a,_mm256_madd_epi16(xv,qv));
        }
        int32_t lanes[8];_mm256_storeu_si256((__m256i*)lanes,a);
        int64_t s=0;for(int z=0;z<8;++z)s+=lanes[z];
        for(;j<d;++j)s+=int32_t(x[r*d+j])*int32_t(q[j]);
        out[r]=double(s)*double(scales[r])*qs;
    }
}
__attribute__((target("avx512f,avx512bw,avx512vnni")))
static inline int64_t dot_i8_vnni(const int8_t* x,const int8_t* q,size_t d,int32_t row_sum) {
    // q+128 is unsigned; subtract the exact 128*sum(x) correction afterwards.
    const __m512i flip=_mm512_set1_epi8(char(0x80));
    __m512i a=_mm512_setzero_si512();size_t j=0;
    for(;j+64<=d;j+=64) {
        __m512i qv=_mm512_xor_si512(_mm512_loadu_si512((const void*)(q+j)),flip);
        __m512i xv=_mm512_loadu_si512((const void*)(x+j));
        a=_mm512_dpbusd_epi32(a,qv,xv);
    }
    if(j<d) {
        // Disabled lanes perform no memory access, even at the last row/page.
        const __mmask64 mask=__mmask64((uint64_t(1)<<(d-j))-1);
        __m512i qv=_mm512_xor_si512(_mm512_maskz_loadu_epi8(mask,q+j),flip);
        __m512i xv=_mm512_maskz_loadu_epi8(mask,x+j);
        a=_mm512_dpbusd_epi32(a,qv,xv);
    }
    int32_t lanes[16];_mm512_storeu_si512((void*)lanes,a);
    int64_t s=0;for(int z=0;z<16;++z)s+=lanes[z];
    return s-int64_t(128)*row_sum;
}
__attribute__((target("avx512f,avx512bw,avx512vnni")))
static void coarse_vnni(const int8_t* x,const float* scales,const int32_t* sums,const int8_t* q,
                        double qs,size_t n,size_t d,double* out) {
    for(size_t r=0;r<n;++r)out[r]=double(dot_i8_vnni(x+r*d,q,d,sums[r]))*double(scales[r])*qs;
}
__attribute__((target("avx2")))
static inline int64_t dot_i8_avx2(const int8_t* x,const int8_t* q,size_t d,int32_t) {
    __m256i a=_mm256_setzero_si256();size_t j=0;
    for(;j+16<=d;j+=16) {
        const __m256i xv=_mm256_cvtepi8_epi16(_mm_loadu_si128((const __m128i*)(x+j)));
        const __m256i qv=_mm256_cvtepi8_epi16(_mm_loadu_si128((const __m128i*)(q+j)));
        a=_mm256_add_epi32(a,_mm256_madd_epi16(xv,qv));
    }
    int32_t lanes[8];_mm256_storeu_si256((__m256i*)lanes,a);
    int64_t sum=0;for(int z=0;z<8;++z)sum+=lanes[z];
    for(;j<d;++j)sum+=int32_t(x[j])*int32_t(q[j]);
    return sum;
}
#endif

static_assert(sizeof(double)==8 && std::numeric_limits<double>::is_iec559,
              "The interval implementation requires IEEE binary64");
// Bitwise adjacent-number operations avoid per-row libm calls. They have the
// same result as nextafter(v,+/-inf), including signed zero and infinities.
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
static constexpr double unit_roundoff=0x1p-53;
static inline double gamma_n(size_t n) {return (n*unit_roundoff)/(1-n*unit_roundoff);}
static inline double outward(double value) {return next_up(value*(1+gamma_n(16)));}

struct BoundFilter {
    const double *residual,*xhat,*x;
    double qresidual,qhat,qnorm,gamma_ref,gamma_coarse;
    double *upper,*heap;
    size_t k,count=0;
    bool valid=true;
    inline void accept(size_t row,double coarse) {
        double radius=outward(qnorm*residual[row]);
        radius+=outward(qresidual*xhat[row]);
        radius+=outward(gamma_ref*qnorm*x[row]);
        radius+=outward(gamma_coarse*qhat*xhat[row]);
        radius=outward(radius);
        double lo=next_down(coarse-radius);
        double hi=next_up(coarse+radius);
        if(!std::isfinite(coarse)||!std::isfinite(lo)||!std::isfinite(hi)||lo>hi) {
            valid=false;lo=-std::numeric_limits<double>::infinity();hi=std::numeric_limits<double>::infinity();
        }
        upper[row]=hi;
        if(count<k) {
            heap[count++]=lo;std::push_heap(heap,heap+count,std::greater<double>());
        } else if(lo>heap[0]) {
            std::pop_heap(heap,heap+k,std::greater<double>());
            heap[k-1]=lo;std::push_heap(heap,heap+k,std::greater<double>());
        }
    }
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
        // A common radius makes lower endpoints monotone in the coarse score.
        // Keep coarse values in the heap; subtract/round the threshold once.
        if(count<k) {
            heap[count++]=coarse;std::push_heap(heap,heap+count,std::greater<double>());
        } else if(coarse>heap[0]) {
            std::pop_heap(heap,heap+k,std::greater<double>());
            heap[k-1]=coarse;std::push_heap(heap,heap+k,std::greater<double>());
        }
    }
};

#define DEFINE_FILTER_LOOP(NAME,ATTR,DOT) \
template<class Filter> \
ATTR static void NAME(const int8_t* x,const float* scales,const int32_t* sums,const int8_t* q, \
                      double qs,size_t n,size_t d,Filter& filter) { \
    for(size_t r=0;r<n;++r) { \
        const double coarse=double(DOT(x+r*d,q,d,sums[r]))*double(scales[r])*qs; \
        filter.accept(r,coarse); \
    } \
}
DEFINE_FILTER_LOOP(filter_scalar,,dot_i8_scalar)
#if X86
DEFINE_FILTER_LOOP(filter_avx2,__attribute__((target("avx2"))),dot_i8_avx2)
DEFINE_FILTER_LOOP(filter_vnni,__attribute__((target("avx512f,avx512bw,avx512vnni"))),dot_i8_vnni)
#endif
#undef DEFINE_FILTER_LOOP

extern "C" {
int etex_round_nearest(){return std::fegetround()==FE_TONEAREST;}
int etex_ieee_environment(){
    if(std::fegetround()!=FE_TONEAREST)return 0;
#if defined(__x86_64__)
    // Also inspect SIMD rounding: another library could change MXCSR without
    // changing the x87 rounding mode queried by fegetround().
    if(_mm_getcsr() & ((1u<<15)|(1u<<6)|(3u<<13)))return 0;
    return 1;
#else
    // A tested platform-specific subnormal/precision-control check is needed
    // before enabling certificates on another architecture (including x87).
    return 0;
#endif
}
const char* etex_search_backend(){
#if X86
    if(__builtin_cpu_supports("avx512vnni") && __builtin_cpu_supports("avx512bw") && __builtin_cpu_supports("avx512f"))return "avx512_vnni";
    if(__builtin_cpu_supports("avx2"))return "avx2";
#endif
    return "portable";
}
void etex_exact(const float* x,const double* q,size_t n,size_t d,double* out){
#if X86
    if(__builtin_cpu_supports("avx512f") && __builtin_cpu_supports("avx512bw")){exact_avx512(x,q,n,d,out);return;}
    if(__builtin_cpu_supports("avx2")){exact_avx2(x,q,n,d,out);return;}
#endif
    exact_scalar(x,q,n,d,out);
}
void etex_float(const float* x,const float* q,size_t n,size_t d,float* out){
#if X86
    if(__builtin_cpu_supports("avx512f")){float_avx512(x,q,n,d,out);return;}
    if(__builtin_cpu_supports("avx2")){float_avx2(x,q,n,d,out);return;}
#endif
    float_scalar(x,q,n,d,out);
}
void etex_coarse(const int8_t* x,const float* scales,const int32_t* sums,const int8_t* q,double qs,size_t n,size_t d,double* out){
#if X86
    if(__builtin_cpu_supports("avx512vnni") && __builtin_cpu_supports("avx512bw") && __builtin_cpu_supports("avx512f")){coarse_vnni(x,scales,sums,q,qs,n,d,out);return;}
    if(__builtin_cpu_supports("avx2")){coarse_avx2(x,scales,sums,q,qs,n,d,out);return;}
#endif
    coarse_scalar(x,scales,sums,q,qs,n,d,out);
}
void etex_exact_portable(const float* x,const double* q,size_t n,size_t d,double* out){exact_scalar(x,q,n,d,out);}
void etex_coarse_portable(const int8_t* x,const float* scales,const int32_t* sums,const int8_t* q,double qs,size_t n,size_t d,double* out){coarse_scalar(x,scales,sums,q,qs,n,d,out);}
int etex_filter_bounds(const int8_t* x,const float* scales,const int32_t* sums,
                      const double* residual,const double* xhat,const double* xnorm,
                      const int8_t* q,double qs,double qresidual,double qhat,double qnorm,
                      size_t n,size_t d,size_t k,double* upper,double* heap,double* threshold) {
    if(!etex_ieee_environment())return -1;
    if(!k||k>n||!d||d>65536)return -2;
    BoundFilter filter{residual,xhat,xnorm,qresidual,qhat,qnorm,gamma_n(d+8),gamma_n(3),upper,heap,k};
#if X86
    if(__builtin_cpu_supports("avx512vnni") && __builtin_cpu_supports("avx512bw") && __builtin_cpu_supports("avx512f"))
        filter_vnni(x,scales,sums,q,qs,n,d,filter);
    else if(__builtin_cpu_supports("avx2"))filter_avx2(x,scales,sums,q,qs,n,d,filter);
    else
#endif
        filter_scalar(x,scales,sums,q,qs,n,d,filter);
    threshold[0]=heap[0];
    return filter.valid?1:0;
}
int etex_filter_global(const int8_t* x,const float* scales,const int32_t* sums,
                       const int8_t* q,double qs,double radius,
                       size_t n,size_t d,size_t k,double* upper,double* heap,double* threshold) {
    if(!etex_ieee_environment())return -1;
    if(!k||k>n||!d||d>65536||!std::isfinite(radius)||radius<0)return -2;
    GlobalFilter filter{radius,upper,heap,k};
#if X86
    if(__builtin_cpu_supports("avx512vnni") && __builtin_cpu_supports("avx512bw") && __builtin_cpu_supports("avx512f"))
        filter_vnni(x,scales,sums,q,qs,n,d,filter);
    else if(__builtin_cpu_supports("avx2"))filter_avx2(x,scales,sums,q,qs,n,d,filter);
    else
#endif
        filter_scalar(x,scales,sums,q,qs,n,d,filter);
    threshold[0]=next_down(heap[0]-radius);
    return filter.valid && std::isfinite(threshold[0])?1:0;
}
}
