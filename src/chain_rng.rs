//! src/chain_rng.rs
//! A simple chained RNG implementation for mutation operations.
//! This RNG is not cryptographically secure and is only intended for use in fuzz testing.

pub static mut _STATE: u64 = 4399;

#[inline]
fn hash64(input: u64) -> u64 {
    //! SplitMix64 hash function
    let mut x = input;
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d049bb133111eb);
    x = x ^ (x >> 31);
    x
}

pub fn ri(max: u32) -> u32 {
    //! Generate a random integer in [0, max)
    if max <= 1 {
        return 0;
    }
    unsafe {
        let mut t1 = _STATE;
        t1 = t1.wrapping_mul(0x5DEECE66D).wrapping_add(0xB);
        let t2 = t1 % max as u64;
        _STATE = hash64(_STATE);
        t2 as u32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    
    #[test]
    fn test_hash64() {
        //! Test for hash64 function not crash
        let input: u64 = 123456789;
        for _ in 0..100 {
            let _ = hash64(input);
        }
    }

    #[test]
    fn test_ri() {
        //! Test for ri function not crash
        let max: u32 = 100;
        for _ in 0..100 {
            let r = ri(max);
            assert!(r < max);
        }
    }
}