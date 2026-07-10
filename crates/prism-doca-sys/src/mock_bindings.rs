// Mock DOCA Flow bindings for development without DOCA SDK

pub type doca_error_t = i32;
pub const DOCA_SUCCESS: doca_error_t = 0;
pub const DOCA_ERROR_INVALID_VALUE: doca_error_t = -1;
pub const DOCA_ERROR_NO_MEMORY: doca_error_t = -2;

#[repr(C)]
pub struct doca_flow_port {
    _private: [u8; 0],
}

#[repr(C)]
pub struct doca_flow_pipe {
    _private: [u8; 0],
}

#[repr(C)]
pub struct doca_flow_pipe_entry {
    _private: [u8; 0],
}

#[repr(C)]
pub struct doca_flow_query {
    pub total_pkts: u64,
    pub total_bytes: u64,
}

// Stub functions
pub unsafe fn doca_flow_init(_cfg: *const std::ffi::c_void) -> doca_error_t { DOCA_SUCCESS }
pub unsafe fn doca_flow_destroy() {}
pub unsafe fn doca_flow_port_start(_cfg: *const std::ffi::c_void) -> *mut doca_flow_port { std::ptr::null_mut() }
pub unsafe fn doca_flow_pipe_create(_cfg: *const std::ffi::c_void) -> *mut doca_flow_pipe { std::ptr::null_mut() }
pub unsafe fn doca_flow_pipe_add_entry(
    _pipe: *mut doca_flow_pipe,
    _match_: *const std::ffi::c_void,
    _actions: *const std::ffi::c_void,
) -> *mut doca_flow_pipe_entry { std::ptr::null_mut() }
pub unsafe fn doca_flow_pipe_rm_entry(_entry: *mut doca_flow_pipe_entry) -> doca_error_t { DOCA_SUCCESS }
pub unsafe fn doca_flow_query_entry(_entry: *mut doca_flow_pipe_entry, query: *mut doca_flow_query) -> doca_error_t {
    if !query.is_null() {
        (*query).total_pkts = 0;
        (*query).total_bytes = 0;
    }
    DOCA_SUCCESS
}
