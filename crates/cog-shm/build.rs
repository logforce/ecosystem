// SPDX-License-Identifier: Apache-2.0
use std::{env, path::PathBuf, process::Command};
fn main() {
    println!("cargo:rerun-if-changed=src/linux.c");
    if env::var("CARGO_CFG_TARGET_OS").unwrap() != "linux" {
        return;
    }
    assert_eq!(
        env::var("HOST").unwrap(),
        env::var("TARGET").unwrap(),
        "use a native Linux build for the system-header shim"
    );
    let out = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    assert!(Command::new("cc")
        .args([
            "-std=c11",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-fPIC",
            "-c",
            "src/linux.c",
            "-o"
        ])
        .arg(out.join("linux.o"))
        .status()
        .unwrap()
        .success());
    assert!(Command::new("ar")
        .arg("crs")
        .arg(out.join("libcog_shm.a"))
        .arg(out.join("linux.o"))
        .status()
        .unwrap()
        .success());
    println!("cargo:rustc-link-search=native={}", out.display());
    println!("cargo:rustc-link-lib=static=cog_shm");
}
