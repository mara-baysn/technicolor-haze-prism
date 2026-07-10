pub mod offload {
    pub mod v1 {
        tonic::include_proto!("prism.offload.v1");
    }
}

pub use offload::v1::*;
