use std::env;
use std::path::PathBuf;

fn main() {
    let out_path = PathBuf::from(env::var("OUT_DIR").unwrap());

    // When doca-mock feature is enabled, generate stub bindings
    if cfg!(feature = "doca-mock") {
        std::fs::write(
            out_path.join("bindings.rs"),
            include_str!("src/mock_bindings.rs"),
        ).unwrap();
        return;
    }

    // Try to find DOCA SDK headers
    let doca_root = env::var("DOCA_ROOT")
        .unwrap_or_else(|_| "/opt/mellanox/doca".to_string());

    let header_path = format!("{}/include", doca_root);

    if std::path::Path::new(&header_path).exists() {
        let bindings = bindgen::Builder::default()
            .header("wrapper.h")
            .clang_arg(format!("-I{}", header_path))
            .allowlist_function("doca_flow.*")
            .allowlist_type("doca_flow.*")
            .allowlist_var("DOCA_FLOW.*")
            .generate()
            .expect("Unable to generate DOCA bindings");

        bindings
            .write_to_file(out_path.join("bindings.rs"))
            .expect("Couldn't write bindings");
    } else {
        // Fallback to mock bindings for development without DOCA SDK
        eprintln!("cargo:warning=DOCA SDK not found at {}, using mock bindings", doca_root);
        std::fs::write(
            out_path.join("bindings.rs"),
            include_str!("src/mock_bindings.rs"),
        ).unwrap();
    }
}
