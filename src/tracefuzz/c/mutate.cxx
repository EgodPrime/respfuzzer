#include<stdint.h>
#define PY_SSIZE_T_CLEAN
#include<Python.h>
#include<ctime>

#include "mutate.h"

uint64_t _state=4399;

inline uint64_t hash64(uint64_t input) {
    // 一个高质量的混合函数
    input = (input ^ 0x9E3779B97F4A7C15ULL) * 0xBF58476D1CE4E5B9ULL;
    input = (input ^ (input >> 30)) * 0x94D049BB133111EBULL;
    return input ^ (input >> 31);
}

void chain_rng_init(uint64_t seed) {
    _state = seed;
}

inline void chain_rng_next(void) {
    _state = hash64(_state);
}

uint32_t chain_rng_rand_range(uint32_t max) {
    if (max <= 1) return 0;
    
    // 使用当前状态生成随机数
    uint32_t result = (uint32_t)((_state * (uint64_t)max) >> 32);
    
    // ！！！关键：自动推进状态，为下一次调用做准备
    chain_rng_next();
    
    return result % max;
}

uint64_t chain_rng_get_current_state(void) {
    return _state;
}

/* Helper to choose random block len for block operations in fuzz_one().
   Doesn't return zero, provided that max_len is > 0. */
static u32 choose_block_len(u32 limit) {

  u32 min_value, max_value;

  switch (chain_rng_rand_range(3)) {

    case 0:  min_value = 1;
             max_value = HAVOC_BLK_SMALL;
             break;

    case 1:  min_value = HAVOC_BLK_SMALL;
             max_value = HAVOC_BLK_MEDIUM;
             break;

    default: 

             if (chain_rng_rand_range(10)) {

               min_value = HAVOC_BLK_MEDIUM;
               max_value = HAVOC_BLK_LARGE;

             } else {

               min_value = HAVOC_BLK_LARGE;
               max_value = HAVOC_BLK_XL;

             }

  }

  if (min_value >= limit) min_value = 1;

  return min_value + chain_rng_rand_range(MIN(max_value, limit) - min_value + 1);

}


inline void bitflip1(u8* _ar, u32 len){
    if (!len) return;
    u32 p = chain_rng_rand_range(len);
    _ar[(p)>>3] ^= (128 >> ((p) & 7));
}

inline void byte_interesting(u8* _ar, u32 len) {
    if (!len) return;
    _ar[chain_rng_rand_range(len)] = interesting_8[chain_rng_rand_range(sizeof(interesting_8))];
}

inline void word_interesting(u8* _ar, u32 len){
    if(len<2) return;
    s16 n = interesting_16[chain_rng_rand_range(sizeof(interesting_16))];
    if (chain_rng_rand_range(2)){
        *(u16*)(_ar + chain_rng_rand_range(len-1)) = n;
    } else {
        *(u16*)(_ar + chain_rng_rand_range(len-1)) = SWAP16(n);
    }
}

inline void dword_interesting(u8* _ar, u32 len){
    if (len<4) return;
    s32 n = interesting_32[chain_rng_rand_range(sizeof(interesting_32))];
    if (chain_rng_rand_range(2)){
        *(u32*)(_ar + chain_rng_rand_range(len-3)) = n;
    } else {
        *(u32*)(_ar + chain_rng_rand_range(len-3)) = SWAP32(n);
    }
}

inline void byte_arith(u8* _ar, u32 len){
    if (!len) return;
    if (chain_rng_rand_range(2)){
        _ar[chain_rng_rand_range(len)] -= 1 + chain_rng_rand_range(ARITH_MAX);
    }else{
        _ar[chain_rng_rand_range(len)] += 1 + chain_rng_rand_range(ARITH_MAX);
    }
}

inline void word_arith(u8* _ar, u32 len){
    if (len<2) return;
    u32 p = chain_rng_rand_range(len-1);
    u16 n = 1 + chain_rng_rand_range(ARITH_MAX);
    if (chain_rng_rand_range(2)){
        if (chain_rng_rand_range(2)){
            *(u16*)(_ar + p) -= 1 + n;
        }else{
            *(u16*)(_ar + p) = SWAP16(SWAP16(*(u16*)(_ar + p)) - n);
        }
    } else {
        if (chain_rng_rand_range(2)){
            *(u16*)(_ar + p) += 1 + n;
        }else{
            *(u16*)(_ar + p) = SWAP16(SWAP16(*(u16*)(_ar + p)) + n);
        }
    }
}

inline void dword_arith(u8* _ar, u32 len){
    if (len<4) return;
    u32 p = chain_rng_rand_range(len-3);
    u32 n = 1 + chain_rng_rand_range(ARITH_MAX);
    if (chain_rng_rand_range(2)){
        if (chain_rng_rand_range(2)) {
            *((u32*)(_ar + p)) -= 1 + n;
        }else{
            *((u32*)(_ar + p)) = SWAP32(SWAP32(*(u32*)(_ar + p)) - n);
        }
    }else{
        if (chain_rng_rand_range(2)){
            *(u32*)(_ar + p) += 1 + n;
        }else{
            *(u32*)(_ar + p) = SWAP32( SWAP32(*(u32*)(_ar + p)) + n);
        }
    }
}

inline void byte_random(u8* _ar, u32 len){
    if (!len) return;
    _ar[chain_rng_rand_range(len)] ^= 1+ chain_rng_rand_range(255);
}

/* Overwrite bytes with a randomly selected chunk (75%) or fixed bytes (25%). */
inline void bytes_random(u8* _ar, u32 len){
    if (len<2) return;
    u32 copy_from, copy_to, copy_len;
    copy_len = choose_block_len(len-1);
    copy_from = chain_rng_rand_range(len - copy_len +1);
    copy_to = chain_rng_rand_range(len - copy_len +1);
    if(chain_rng_rand_range(4)){
        if(copy_from!=copy_to){
            memmove(_ar+copy_to, _ar+copy_from, copy_len);
        }
    }else{
        memset(_ar+copy_to, chain_rng_rand_range(2)?chain_rng_rand_range(255):_ar[chain_rng_rand_range(len)], copy_len);
    }
}

inline void random_delete_bytes(u8*& _ar, u32& len){
    if (len<2) return;
    u32 del_len = chain_rng_rand_range(len-1);
    u32 del_from = chain_rng_rand_range(len-del_len+1);
    memmove(_ar + del_from, _ar+del_from+del_len, len-del_from-del_len);
    len -= del_len;
}

/* Clone bytes (75%) or insert a block of constant bytes (25%). */
inline void str_growth(u8*& _ar, u32& len){
    if (!len) return;
    if(len > MAX_STR_LEN) return;
    u8 clone_or_insert = chain_rng_rand_range(4);
    u32 growth_from, growth_to, growth_len;
    u8* new_buf;

    if (clone_or_insert){
        growth_len = choose_block_len(len);
        growth_from = chain_rng_rand_range(len - growth_len + 1);
    }else{
        growth_len = choose_block_len(HAVOC_BLK_XL);
        growth_from = 0;
    }

    growth_to = chain_rng_rand_range(len);
    new_buf = (u8*)malloc(len+growth_len+1);
    
    memcpy(new_buf, _ar, growth_to);

    if(clone_or_insert){
        memcpy(new_buf+growth_to, _ar+growth_from, growth_len);
    }else{
        memset(new_buf+growth_to, chain_rng_rand_range(2)?chain_rng_rand_range(256):_ar[chain_rng_rand_range(len)], growth_len);
    }
    free(_ar);
    _ar = new_buf;
    len += growth_len;
    _ar[len] = '\0';
}

void havoc(u8 *ar, u32& len, bool is_str){
    u32 use_stacking = 1 << (1+chain_rng_rand_range(7));
    u16 op=0;
    for(size_t i=0; i< use_stacking; i++){
        op = chain_rng_rand_range(is_str?11:9);
        switch (op) {
            case 0:
                bitflip1(ar, len);
                break;
            case 1:
                byte_interesting(ar, len);
                break;
            case 2:
                word_interesting(ar, len);
                break;
            case 3:
                dword_interesting(ar, len);
                break;
            case 4:
                byte_arith(ar, len);
                break;
            case 5:
                word_arith(ar, len);
                break;
            case 6:
                dword_arith(ar, len);
                break;
            case 7:
                byte_random(ar, len);
                break;
            case 8:
                bytes_random(ar, len);
                break;
            case 9:
                random_delete_bytes(ar, len);
                break;
            case 10:
                str_growth(ar, len);
                break;
            default:
                break;
        }
    }
}

static PyObject* mutate_int(PyObject* self, PyObject* args) {
    s32 val;
    if (!PyArg_ParseTuple(args, "l", &val)){
        return NULL;
    }
    u32 len = 4;
    havoc((u8 *)&val, len, false);
    return PyLong_FromLong(val);
}

static PyObject* mutate_float(PyObject* self, PyObject* args){
    double val;
    if (!PyArg_ParseTuple(args, "d", &val)){
        return NULL;
    }
    u32 len = sizeof(double);
    havoc((u8 *)&val, len, false);
    return PyFloat_FromDouble(val);
}

static PyObject* mutate_str(PyObject* self, PyObject* args){
    char* str, *new_str;
    int len;
    if (!PyArg_ParseTuple(args, "s#", &str, &len)){
        return NULL;
    }
    new_str = (char*)malloc(len+1);
    strcpy(new_str, str);
    new_str[len] = '\0';
    u32 x = (u32)len;
    havoc((u8 *)new_str, x, true);
    #if PY_MINOR_VERSION == 13
    return PyUnicode_FromKindAndData(PyUnicode_1BYTE_KIND, new_str, x);
    #else
    return _PyUnicode_FromASCII(new_str, x);
    #endif
}

static PyObject* mutate_bytes(PyObject* self, PyObject* args){
    u8* bytes, *new_bytes;
    int len;
    if (!PyArg_ParseTuple(args, "y#", &bytes, &len)){
        return NULL;
    }
    new_bytes = (u8*)malloc(len+1);
    strcpy((char*)new_bytes, (const char*)bytes);
    new_bytes[len] = '\0';
    u32 x = (u32)len;
    havoc((u8 *)new_bytes, x, true);
    return PyBytes_FromStringAndSize((const char*)new_bytes, x);
}

static PyObject* chain_rng_init(PyObject* self, PyObject* args) {
    uint64_t seed;
    if (!PyArg_ParseTuple(args, "K", &seed)) {
        return NULL;
    }
    chain_rng_init(seed);
    Py_RETURN_NONE;
}

static PyObject* chain_rng_rand_range(PyObject* self, PyObject* args) {
    uint32_t max;
    if (!PyArg_ParseTuple(args, "I", &max)) {
        return NULL;
    }
    uint32_t result = chain_rng_rand_range(max);
    return PyLong_FromUnsignedLong(result);
}

static PyObject* chain_rng_get_current_state(PyObject* self, PyObject* args) {
    uint64_t state = chain_rng_get_current_state();
    return PyLong_FromUnsignedLongLong(state);
}


static PyMethodDef mutate_methods[] = {
    {"mutate_int", mutate_int, METH_VARARGS, "Mutates the integer like AFL does"},
    {"mutate_float", mutate_float, METH_VARARGS, "Mutates the float like AFL does"},
    {"mutate_str", mutate_str, METH_VARARGS, "Mutates the string like AFL does"},
    {"mutate_bytes", mutate_bytes, METH_VARARGS, "Mutates the bytes like AFL does"},
    {"chain_rng_init", chain_rng_init, METH_VARARGS, "Initializes the RNG with a seed"},
    {"chain_rng_rand_range", chain_rng_rand_range, METH_VARARGS, "Generates a random number in the range [0, max)"},
    {"chain_rng_get_current_state", chain_rng_get_current_state, METH_NOARGS, "Returns the current RNG state"},
    { NULL, NULL, 0, NULL }
};

static struct PyModuleDef mutate = {
    PyModuleDef_HEAD_INIT,
    "mutate",   /* name of module */
    NULL, /* module documentation, may be NULL */
    -1,       /* size of per-interpreter state of the module,
                 or -1 if the module keeps state in global variables. */
    mutate_methods
};

PyMODINIT_FUNC
PyInit_mutate(void)
{
    return PyModule_Create(&mutate);
}