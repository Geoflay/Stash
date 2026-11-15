set(VCPKG_TARGET_ARCHITECTURE x64)
set(VCPKG_CRT_LINKAGE dynamic)
set(VCPKG_LIBRARY_LINKAGE static)
set(VCPKG_BUILD_TYPE release)
# dav1d's own atomics for MSVC no longer compile with recent MSVC: use MSVC's C11 ones.
if(PORT STREQUAL "dav1d")
    set(VCPKG_C_FLAGS "/std:c11 /experimental:c11atomics")
    set(VCPKG_CXX_FLAGS "")
endif()
