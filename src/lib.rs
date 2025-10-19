
//！src/lib.rs
//! A Python module implemented in Rust.
//! This library contains all the semantically equivalent mutation operations for AFL.

mod chain_rng;
mod mutator;

use chain_rng::{ri, _STATE};
use mutator::havoc;


use pyo3::{prelude::*, types::{PyBytes}};

#[pyfunction]
#[pyo3(name = "set_random_state")]
fn chain_rng_set_state(seed: u32) -> PyResult<()> {
    //! Set the internal RNG state
    unsafe {
        _STATE = seed;
    }
    Ok(())
}

#[pyfunction]
#[pyo3(name = "get_random_state")]
fn chain_rng_get_state() -> PyResult<u32> {
    //! Get the internal RNG state
    unsafe {
        Ok(_STATE)
    }
}

#[pyfunction]
#[pyo3(name = "randint")]
fn chain_rng_randint(max: u32) -> PyResult<u32> {
    //! Generate a random integer in [0, max)
    let r = ri(max);
    Ok(r)
}

#[pyfunction]
#[pyo3(name = "mutate_int")]
fn py_int_mutate(value: i32) -> PyResult<i32> {
    //! Mutate an integer by treating it as a byte array
    let mut byte_array = value.to_le_bytes().to_vec(); // 转换为小端字节序的字节数组
    let mut len = byte_array.len();
    unsafe {
        havoc(&mut byte_array, &mut len, false);
    }
    Ok(i32::from_le_bytes(byte_array.as_slice().try_into().unwrap()))
}

#[pyfunction]
#[pyo3(name = "mutate_float")]
fn py_float_mutate(value: f32) -> PyResult<f32> {
    //! Mutate a float by treating it as a byte array
    let mut byte_array = value.to_le_bytes().to_vec(); // 转换为小端字节序的字节数组
    let mut len = byte_array.len();
    unsafe {
        havoc(&mut byte_array, &mut len, false);
    }
    Ok(f32::from_le_bytes(byte_array.as_slice().try_into().unwrap()))
}

#[pyfunction]
#[pyo3(name = "mutate_str")]
fn py_str_mutate(value: &str) -> PyResult<String> {
    //! Mutate a string by treating it as a byte array
    let mut byte_array = value.as_bytes().to_vec();
    let mut len = byte_array.len();
    unsafe {
        havoc(&mut byte_array, &mut len, true);
    }
    Ok(String::from_utf8_lossy(&byte_array).to_string())
}

#[pyfunction]
#[pyo3(name = "mutate_bytes")]
fn py_bytes_mutate(py: Python, value: &[u8]) -> PyResult<Py<PyBytes>> {
    //! Mutate a bytes object by treating it as a byte array
    let mut byte_array = value.to_vec();
    let mut len = byte_array.len();
    unsafe {
        havoc(&mut byte_array, &mut len, false);
    }
    Ok(PyBytes::new_bound(py, &byte_array).into())
}

/// A Python module implemented in Rust. The name of this function must match
/// the `lib.name` setting in the `Cargo.toml`, else Python will not be able to
/// import the module.
#[pymodule]
fn mutate(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(chain_rng_set_state, m)?)?;
    m.add_function(wrap_pyfunction!(chain_rng_get_state, m)?)?;
    m.add_function(wrap_pyfunction!(chain_rng_randint, m)?)?;
    m.add_function(wrap_pyfunction!(py_int_mutate, m)?)?;
    m.add_function(wrap_pyfunction!(py_float_mutate, m)?)?;
    m.add_function(wrap_pyfunction!(py_str_mutate, m)?)?;
    m.add_function(wrap_pyfunction!(py_bytes_mutate, m)?)?;
    Ok(())
}