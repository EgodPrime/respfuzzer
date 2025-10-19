
//！src/mutator.rs
//！This module implements various mutation operations for fuzz testing,

use crate::chain_rng::ri;

use std::ptr;

#[inline]
fn min(a: u32, b: u32) -> u32 {
    //! Get the minimum of two numbers
    if a < b {
        a
    } else {
        b
    }
}

#[inline]
fn swap16(x: u16) -> u16 {
    //! Swap the byte order of a 16-bit integer
    (x << 8) | (x >> 8)
}

#[inline]
fn swap32(x: u32) -> u32 {
    //! Swap the byte order of a 32-bit integer
    ((x << 24) | (x >> 24)) |
    ((x << 8) & 0x00FF0000) |
    ((x >> 8) & 0x0000FF00)
}

// Interesting byte values for mutation
const INTERSTING_8: [i8; 9] = [
    -128,
    -1,
    0,
    1,
    16,
    32,
    64,
    100,
    127
];

// Interesting 16-bit values for mutation
const INTERSTING_16: [i16; 19] = [
    -32768,
    -129,
    -128,
    -1,
    0,
    1,
    16,
    32,
    64,
    100,
    127,
    128,
    255,
    256,
    512,
    1000,
    1024,
    4096,
    32767
];

// Interesting 32-bit values for mutation
const INTERSTING_32: [i32; 27] = [
    -2147483648,
    -1006630464,
    -32769,
    -32768,
    -129,
    -128,
    -1,
    0,
    1,
    16,
    32,
    64,
    100,
    127,
    128,
    255,
    256,
    512,
    1000,
    1024,
    4096,
    32767,
    32768,
    65535,
    65536,
    100663045,
    2147483647
];

const ARITH_MAX: u32 = 35; // Maximum value for arithmetic operations
const MAX_STR_LEN: usize = 1*1024*1024; // Maximum string length for growth operations
const HAVOC_BLK_SMALL: usize = 32; // Small block size for havoc
const HAVOC_BLK_MEDIUM: usize = 128; // Medium block size for havoc
const HAVOC_BLK_LARGE: usize = 1500; // Large block size for havoc
const HAVOC_BLK_XL: usize = 32768; // Extra large block size for havoc

fn choose_block_len(limit: u32) -> u32{
    //! Helper to choose random block len for block operations in fuzz_one().
    //! Doesn't return zero, provided that max_len is > 0.
    let mut min_value: u32;
    let max_value: u32;
    match ri(3) {
        0 => {
            min_value = 1;
            max_value = HAVOC_BLK_SMALL as u32;
        }
        1 => {
            min_value = HAVOC_BLK_SMALL as u32;
            max_value = HAVOC_BLK_MEDIUM as u32;
        }
        _ => {
            if  ri(10)!=0 {
                min_value = HAVOC_BLK_MEDIUM as u32;
                max_value = HAVOC_BLK_LARGE as u32;
            }else {
                min_value = HAVOC_BLK_LARGE as u32;
                max_value = HAVOC_BLK_XL as u32;
            }
        }
    }

    if min_value >= limit {
        min_value = 1;
    }

    min_value + ri(min(max_value, limit)-min_value +1)
}

#[inline]
fn bitflip_1(_ar: &mut [u8], len: usize) {
    //! Randomly flip a single bit in the byte array
    if len == 0 {
        return;
    }
    let p: usize = ri(len as u32) as usize;
    _ar[p>>3] ^= (128 >> (p & 7)) as u8;
}

#[inline]
fn byte_intersting(_ar: &mut [u8], len: usize) {
    //! Set a random byte in the byte array to an interesting value
    if len == 0 {
        return;
    }
    let p: usize = ri(len as u32) as usize;
    let n = INTERSTING_8[ri(INTERSTING_8.len() as u32) as usize];
    _ar[p] = n as u8;
}

#[inline]
unsafe fn word_intersting(_ar: &mut [u8], len: usize) {
    //! Set a random word in the byte array to an interesting value
    if len < 2 {
        return;
    }
    let p: usize = ri((len - 1) as u32) as usize;
    let p_ptr = _ar.as_mut_ptr().add(p);
    let n = INTERSTING_16[ri(INTERSTING_16.len() as u32) as usize] as u16;
    let n = if ri(2) != 0 { n } else { swap16(n) };
    ptr::copy_nonoverlapping(&n as *const u16 as *const u8, p_ptr, 2);
}

#[inline]
unsafe fn dword_intersting(_ar: &mut [u8], len: usize) {
    //! Set a random dword in the byte array to an interesting value
    if len < 4 { return; }
    let p = ri((len - 3) as u32) as usize;
    let p_ptr = _ar.as_mut_ptr().add(p);
    let n = INTERSTING_32[ri(INTERSTING_32.len() as u32) as usize] as u32;
    let n = if ri(2) != 0 { n } else { swap32(n) };
    ptr::copy_nonoverlapping(&n as *const u32 as *const u8, p_ptr, 4);
}

#[inline]
fn byte_arith(_ar: &mut [u8], len: usize) {
    //! Perform arithmetic operations on a random byte in the byte array
    if len == 0 {
        return;
    }
    let p: usize = ri(len as u32) as usize;
    if ri(2)!= 0 {
        _ar[p] = _ar[p].wrapping_sub(1 + ri(ARITH_MAX) as u8);
    }else {
        _ar[p] = _ar[p].wrapping_add(1+ ri(ARITH_MAX) as u8);
    }
}

#[inline]
unsafe fn word_arith(_ar: &mut [u8], len: usize) {
    //! Perform arithmetic operations on a random word in the byte array
    if len < 2 {
        return;
    }
    let p = ri((len - 1) as u32) as usize;
    let p_ptr = _ar.as_mut_ptr().add(p);
    let n = 1 + ri(ARITH_MAX) as u16;

    let val = ptr::read_unaligned(p_ptr as *const u16);
    let mutated: u16;
    if ri(2) != 0 {
        if ri(2) != 0 {
            mutated = val.wrapping_sub(1 + n);
        } else {
            mutated = swap16(swap16(val).wrapping_sub(n));
        }
    } else {
        if ri(2) != 0 {
            mutated = val.wrapping_add(1 + n);
        } else {
            mutated = swap16(swap16(val).wrapping_add(n));
        }
    }
    ptr::write_unaligned(p_ptr as *mut u16, mutated);
}

#[inline]
unsafe fn dword_arith(_ar: &mut [u8], len: usize) {
    //! Perform arithmetic operations on a random dword in the byte array
    if len < 4 {
        return;
    }
    let p = ri((len - 3) as u32) as usize;
    let p_ptr = _ar.as_mut_ptr().add(p);
    let n = 1 + ri(ARITH_MAX) as u32;

    let val = ptr::read_unaligned(p_ptr as *const u32);
    let mutated: u32;
    if ri(2) != 0 {
        if ri(2) != 0 {
            mutated = val.wrapping_sub(1 + n);
        } else {
            mutated = swap32(swap32(val).wrapping_sub(n));
        }
    } else {
        if ri(2) != 0 {
            mutated = val.wrapping_add(1 + n);
        } else {
            mutated = swap32(swap32(val).wrapping_add(n));
        }
    }
    ptr::write_unaligned(p_ptr as *mut u32, mutated);
}

#[inline]
fn byte_random(_ar: &mut [u8], len: usize){
    //! Set a random byte in the byte array to a random value
    if len == 0 {
        return;
    }
    let p: usize = ri(len as u32) as usize;
    _ar[p] ^= 1 + ri(255) as u8;
}

#[inline]
unsafe fn bytes_random(_ar: &mut [u8], len: usize){
    //! Randomly modify a block of bytes in the byte array
    if len < 2 {
        return;
    }
    let copy_len = choose_block_len((len - 1) as u32) as usize;
    let copy_from = ri((len - copy_len + 1) as u32) as usize;
    let copy_to = ri((len - copy_len + 1) as u32) as usize;
    if ri(4) != 0 {
        if copy_from != copy_to {
            ptr::copy(
                _ar.as_ptr().add(copy_from),
                _ar.as_mut_ptr().add(copy_to),
                copy_len,
            );
        }
    } else {
        let fill_byte: u8 = if ri(2) != 0 {
            ri(255) as u8
        } else {
            _ar[ri(len as u32) as usize]
        };
        ptr::write_bytes(_ar.as_mut_ptr().add(copy_to), fill_byte, copy_len);   
    }
}

#[inline]
unsafe fn random_delete_bytes(_ar: &mut [u8], len: &mut usize){
    //! Randomly delete a block of bytes from the byte array
    if *len < 2 {
        return;
    }
    let del_len = ri((*len - 1) as u32) as usize;
    let del_from = ri((*len - del_len) as u32) as usize;
    ptr::copy(
        _ar.as_ptr().add(del_from + del_len),
        _ar.as_mut_ptr().add(del_from),
        *len - del_from - del_len,
    );
    *len -= del_len;
}

#[inline]
unsafe fn random_grow_bytes(_ar: &mut Vec<u8>, len: &mut usize){
    //! Randomly grow the byte array by inserting or cloning a block of bytes
    if *len == 0 || *len > MAX_STR_LEN {
        return;
    }
    let clone_or_insert = ri(4);
    let growth_len: usize;
    let growth_from: usize;

    if clone_or_insert != 0 {
        growth_len = choose_block_len(*len as u32) as usize;
        growth_from = ri((*len - growth_len + 1) as u32) as usize;
    } else {
        growth_len = choose_block_len(HAVOC_BLK_XL as u32) as usize;
        growth_from = 0;
    }

    let growth_to = ri((*len) as u32) as usize;

    let mut new_buf: Vec<u8> = Vec::with_capacity(*len + growth_len + 1);
    new_buf.extend_from_slice(&_ar[..growth_to]);

    if clone_or_insert != 0 {
        new_buf.extend_from_slice(&_ar[growth_from..growth_from + growth_len]);
    } else {
        let fill_byte: u8 = if ri(2) != 0 {
            ri(256) as u8
        } else {
            _ar[ri(*len as u32) as usize]
        };
        new_buf.extend(std::iter::repeat(fill_byte).take(growth_len));
    }
    new_buf.extend_from_slice(&_ar[growth_to..*len]);
    *len += growth_len;
    new_buf.push(0); // null terminator
    _ar.clear();
    _ar.extend_from_slice(&new_buf);
}

pub unsafe fn havoc(ar: &mut Vec<u8>, len: &mut usize, is_str:bool){
    //! Perform a series of random mutations on the byte array
    let use_stacking = (1 as u32) << (1+ri(7));
    for _ in 0..use_stacking {
        let op = if is_str{
            ri(11)
        } else {
            ri(9)
        };
        match op {
            0 => bitflip_1(ar, *len),
            1 => byte_intersting(ar, *len),
            2 => word_intersting(ar, *len),
            3 => dword_intersting(ar, *len),
            4 => byte_arith(ar, *len),
            5 => word_arith(ar, *len),
            6 => dword_arith(ar, *len),
            7 => byte_random(ar, *len),
            8 => bytes_random(ar, *len),
            9 => {
                if is_str {
                    random_delete_bytes(ar, len);
                }
            }
            10 => {
                if is_str {
                    random_grow_bytes(ar, len);
                }
            }
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_mutations_zero_length() {
        //! Test mutation functions with zero-length input
        let mut byte_array: Vec<u8> = vec![];
        let mut len = byte_array.len();

        unsafe {
            bitflip_1(&mut byte_array, len);
            byte_intersting(&mut byte_array, len);
            word_intersting(&mut byte_array, len);
            dword_intersting(&mut byte_array, len);
            byte_arith(&mut byte_array, len);
            word_arith(&mut byte_array, len);
            dword_arith(&mut byte_array, len);
            byte_random(&mut byte_array, len);
            bytes_random(&mut byte_array, len);
            random_delete_bytes(&mut byte_array, &mut len);
            random_grow_bytes(&mut byte_array, &mut len);
        }
    }

    #[test]
    fn test_mutations_1_length() {
        //! Test mutation functions with 1-length input
        let mut byte_array: Vec<u8> = vec![0];
        let mut len = byte_array.len();

        unsafe {
            bitflip_1(&mut byte_array, len);
            byte_intersting(&mut byte_array, len);
            word_intersting(&mut byte_array, len);
            dword_intersting(&mut byte_array, len);
            byte_arith(&mut byte_array, len);
            word_arith(&mut byte_array, len);
            dword_arith(&mut byte_array, len);
            byte_random(&mut byte_array, len);
            bytes_random(&mut byte_array, len);
            random_delete_bytes(&mut byte_array, &mut len);
            random_delete_bytes(&mut byte_array, &mut len);
            random_grow_bytes(&mut byte_array, &mut len);
        }
    }

    #[test]
    fn test_mutations_2_length() {
        //! Test mutation functions with 2-length input
        let mut byte_array: Vec<u8> = vec![0, 1];
        let mut len = byte_array.len();

        unsafe {
            bitflip_1(&mut byte_array, len);
            byte_intersting(&mut byte_array, len);
            word_intersting(&mut byte_array, len);
            dword_intersting(&mut byte_array, len);
            byte_arith(&mut byte_array, len);
            word_arith(&mut byte_array, len);
            dword_arith(&mut byte_array, len);
            byte_random(&mut byte_array, len);
            bytes_random(&mut byte_array, len);
            random_delete_bytes(&mut byte_array, &mut len);
            random_delete_bytes(&mut byte_array, &mut len);
            random_grow_bytes(&mut byte_array, &mut len);
        }
    }


    #[test]
    fn test_mutations_4_length() {
        //! Test mutation functions with 4-length input
        let mut byte_array: Vec<u8> = vec![0, 1, 2, 3];
        let mut len = byte_array.len();


        unsafe {
            bitflip_1(&mut byte_array, len);
            byte_intersting(&mut byte_array, len);
            word_intersting(&mut byte_array, len);
            dword_intersting(&mut byte_array, len);
            byte_arith(&mut byte_array, len);
            word_arith(&mut byte_array, len);
            dword_arith(&mut byte_array, len);
            byte_random(&mut byte_array, len);
            bytes_random(&mut byte_array, len);
            random_delete_bytes(&mut byte_array, &mut len);
            random_delete_bytes(&mut byte_array, &mut len);
            random_grow_bytes(&mut byte_array, &mut len);
        }
    }

    #[test]
    fn test_mutations_8_length() {
        //! Test mutation functions with 8-length input
        let mut byte_array: Vec<u8> = vec![0,1,2,3,4,5,6,7];
        let mut len = byte_array.len();

        let mut previous_values: Vec<u8> = vec![];
        let mut previous_len = len;

        unsafe {
            previous_values.extend_from_slice(&byte_array);
            bitflip_1(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            byte_intersting(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            word_intersting(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            dword_intersting(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            byte_arith(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            word_arith(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            dword_arith(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            byte_random(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            bytes_random(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            random_delete_bytes(&mut byte_array, &mut len);
            assert_ne!(byte_array, previous_values);
            assert!(len < previous_len);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            previous_len = len;
            random_grow_bytes(&mut byte_array, &mut len);
            assert_ne!(byte_array, previous_values);
            assert!(len > previous_len);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
        }
    }

    #[test]
    fn test_mutations_max_length() {
        //! Test mutation functions with maximum-length input
        
        // Create a byte array of maximum length with random data
        let mut byte_array: Vec<u8> = vec![0; MAX_STR_LEN];
        for i in 0..MAX_STR_LEN {
            byte_array[i] = ri(256) as u8;
        }
        let mut len = byte_array.len();

        let mut previous_values: Vec<u8> = vec![];
        let mut previous_len = len;

        unsafe {
            previous_values.extend_from_slice(&byte_array);
            bitflip_1(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            byte_intersting(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            word_intersting(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            dword_intersting(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            byte_arith(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            word_arith(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            dword_arith(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            byte_random(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            bytes_random(&mut byte_array, len);
            assert_ne!(byte_array, previous_values);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            random_delete_bytes(&mut byte_array, &mut len);
            assert_ne!(byte_array, previous_values);
            assert!(len < previous_len);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
            previous_len = len;
            random_grow_bytes(&mut byte_array, &mut len);
            assert_ne!(byte_array, previous_values);
            assert!(len > previous_len);
            previous_values.clear();
            previous_values.extend_from_slice(&byte_array);
        }
    }

    #[test]
    fn test_havoc_1000_times() {
        //! Test havoc function by applying it 1000 times
        let mut byte_array: Vec<u8> = vec![0; 100];
        for i in 0..100 {
            byte_array[i] = ri(256) as u8;
        }
        let mut len = byte_array.len();

        let mut previous_values: Vec<u8> = vec![];
        previous_values.extend_from_slice(&byte_array);

        let mut eq_cnt = 0;
        unsafe {
            for _ in 0..500 {
                havoc(&mut byte_array, &mut len, true);
                if byte_array == previous_values {
                    eq_cnt += 1;
                }
                previous_values.clear();
                previous_values.extend_from_slice(&byte_array);
            }
            for _ in 0..500 {
                havoc(&mut byte_array, &mut len, false);
                if byte_array == previous_values {
                    eq_cnt += 1;
                }
                previous_values.clear();
                previous_values.extend_from_slice(&byte_array);
            }
        }
        assert!(eq_cnt < 50);
    }
}