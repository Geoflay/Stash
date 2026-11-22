#!/usr/bin/env bash
# Builds the static FFmpeg 8 + x264 + SVT-AV1 libraries the app links, with vcpkg (and, on
# Windows, the NVIDIA and AMD graphics card encoders, which load the driver when used):
#   ffmpeg/build.sh windows
#   ffmpeg/build.sh android
# Needs VCPKG_ROOT (a vcpkg checkout) and ANDROID_HOME (the SDK, with NDK 28.2, and an
# NDK that ships libclang.dll, which reads the FFmpeg headers for Rust); on Windows, LLVM too
# (in C:\Program Files\LLVM, or LLVM_HOME).
# vcpkg fetches its own MSYS2, nasm and pkgconf; nothing else to install.
# Result: ffmpeg/out/{include,lib/x64,lib/arm64}, outside target/ so that cargo clean keeps
# it, and ../.cargo/config.toml (at the
# repository root, where cargo finds it whether run by Tauri or in src-tauri).
set -euo pipefail
cd "$(dirname "$0")"

ndk="$ANDROID_HOME/ndk/28.2.13676358"
case "${1:-}" in
  windows) triplet=x64-windows-static-md-release arch=x64 ;;
  android) triplet=arm64-android-release arch=arm64 ; export ANDROID_NDK_HOME="$ndk" ;;
  *) echo "usage: $0 windows|android" >&2; exit 2 ;;
esac

# Every decoder and demuxer, to read any file; only the encoders, muxers and filters the app
# uses (media/encode.rs, Container::muxer), and local files only.
options="--disable-encoders --enable-encoder=libx264,libsvtav1,h264_nvenc,av1_nvenc,h264_amf,av1_amf,aac,flac,pcm_s16le,libmp3lame
  --disable-muxers --enable-muxer=mp4,mov,matroska,mp3,flac,wav
  --disable-filters --enable-filter=buffer,abuffer,buffersink,abuffersink,null,anull,trim,atrim,setpts,asetpts,fps,transpose,hflip,vflip,scale,pad,setsar,format,aformat,aresample,atempo
  --disable-protocols --enable-protocol=file --disable-network"
# On Windows, NVIDIA cards also resize the pictures they decode (scale_cuda), so that they
# never leave the card. Its program for the card is compiled by LLVM's clang.
if [ "$1" = windows ]; then
  clang="${LLVM_HOME:-/c/Program Files/LLVM}/bin/clang.exe"
  [ -x "$clang" ] || { echo "no $clang: install LLVM (winget install LLVM.LLVM) or set LLVM_HOME" >&2; exit 1; }
  options+=" --enable-filter=scale_cuda --enable-cuda-llvm --nvcc=$(cygpath -s -m "$clang")"
fi

# vcpkg's FFmpeg port takes no configure options: build from a copy of it that adds them.
root=out
port="$root/ports/ffmpeg"
rm -rf "$port"
mkdir -p "$root/ports"
cp -r "$VCPKG_ROOT/ports/ffmpeg" "$port"
anchor='set(OPTIONS_RELEASE "--enable-optimizations")'
grep -qxF "$anchor" "$port/portfile.cmake" || { echo "vcpkg's ffmpeg port changed: update $0" >&2; exit 1; }
sed -i "s|^set(OPTIONS_RELEASE \"--enable-optimizations\")\$|set(OPTIONS_RELEASE \"--enable-optimizations $(echo $options)\")|" "$port/portfile.cmake"
# Nor does it offer SVT-AV1 (the AV1 encoder): an "svt-av1" feature, next to x264's.
anchor='if("x264" IN_LIST FEATURES)'
grep -qxF "$anchor" "$port/portfile.cmake" && grep -q '^    "x264": {$' "$port/vcpkg.json" || { echo "vcpkg's ffmpeg port changed: update $0" >&2; exit 1; }
sed -i 's|^if("x264" IN_LIST FEATURES)$|if("svt-av1" IN_LIST FEATURES)\n    set(OPTIONS "${OPTIONS} --enable-libsvtav1")\nendif()\n&|' "$port/portfile.cmake"
sed -i 's|^    "x264": {$|    "svt-av1": {\n      "description": "AV1 encoding via SVT-AV1",\n      "dependencies": ["svt-av1"]\n    },\n&|' "$port/vcpkg.json"

"$VCPKG_ROOT/vcpkg" install --x-manifest-root=. --x-install-root="$root/vcpkg-$1" \
  --overlay-triplets=triplets --overlay-ports="$root/ports" --triplet "$triplet"

# The layout ffmpeg-sys-next reads from FFMPEG_DIR: one include folder (the headers
# are the same for both), the libraries per CPU.
installed="$root/vcpkg-$1/$triplet"
rm -rf "$root/include" "$root/lib/$arch"
mkdir -p "$root/lib/$arch"
cp -r "$installed/include" "$root/include"
cp "$installed"/lib/*.lib "$installed"/lib/*.a "$root/lib/$arch/" 2>/dev/null || true

libclang=$(ls -d "$ANDROID_HOME"/ndk/*/toolchains/llvm/prebuilt/windows-x86_64/bin/libclang.dll | tail -1)
# clang's own headers (stddef.h…), which it does not find when loaded as a library.
clang_headers=$(ls -d "$(dirname "$libclang")"/../lib/clang/*/include | tail -1)
# That clang does not find recent Visual Studio versions on its own: name their headers.
vs=$("/c/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe" -latest -prerelease -products '*' -property installationPath)
msvc=$(ls -d "$(cygpath -u "$vs")"/VC/Tools/MSVC/*/include | tail -1)
sdk=$(ls -d "/c/Program Files (x86)/Windows Kits/10/Include/"*/ | tail -1)
windows_headers="--target=x86_64-pc-windows-msvc -fms-extensions -fms-compatibility "
for dir in "$msvc" "$sdk/ucrt" "$sdk/um" "$sdk/shared"; do
  windows_headers+="-isystem '$(cygpath -m "$dir")' "
done
mkdir -p ../../.cargo
cat > ../../.cargo/config.toml <<EOF
# Written by ffmpeg/build.sh for this machine.
[env]
FFMPEG_DIR = { value = "src-tauri/ffmpeg/out", relative = true }
LIBCLANG_PATH = "$(cygpath -m "$(dirname "$libclang")")"
BINDGEN_EXTRA_CLANG_ARGS_x86_64_pc_windows_msvc = "$windows_headers"
BINDGEN_EXTRA_CLANG_ARGS_aarch64_linux_android = "--sysroot=$(cygpath -m "$ndk/toolchains/llvm/prebuilt/windows-x86_64/sysroot") -isystem '$(cygpath -m "$clang_headers")'"
EOF

# Rust bundles static libraries into the crate that links them: rebuild it with the new ones,
# in every profile (cargo clean -p only cleans the one it is given).
target=()
[ "$1" = android ] && target=(--target aarch64-linux-android)
for profile in dev release fast; do
  (cd .. && cargo clean -p ffmpeg-sys-next "${target[@]}" --profile "$profile")
done
