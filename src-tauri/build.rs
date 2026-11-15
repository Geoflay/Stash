fn main() {
    // FFmpeg's own libraries are linked by ffmpeg-sys-next; the encoders, dav1d (the AV1
    // decoder), zlib and the system libraries they use are linked here. All come from
    // ffmpeg/build.sh.
    println!("cargo:rerun-if-env-changed=FFMPEG_DIR");
    let dir = std::env::var("FFMPEG_DIR").expect("FFMPEG_DIR is not set: run ffmpeg/build.sh");
    let target = std::env::var("CARGO_CFG_TARGET_OS").unwrap();
    let arch = if std::env::var("CARGO_CFG_TARGET_ARCH").as_deref() == Ok("aarch64") { "arm64" } else { "x64" };
    println!("cargo:rustc-link-search=native={dir}/lib/{arch}");
    let libraries: &[&str] = match target.as_str() {
        "windows" => &["static=libx264", "static=dav1d", "static=libmp3lame-static", "static=libmpghip-static", "static=zlib", "mfuuid", "ole32", "strmiids", "user32", "bcrypt", "secur32", "ncrypt", "crypt32", "ws2_32"],
        "android" => &["static=x264", "static=dav1d", "static=mp3lame", "static=z", "mediandk", "android"],
        _ => &[],
    };
    for library in libraries {
        println!("cargo:rustc-link-lib={library}");
    }
    tauri_build::build()
}
