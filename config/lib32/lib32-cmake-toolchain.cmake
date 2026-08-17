# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 InterGenJLU
#
# config/lib32/lib32-cmake-toolchain.cmake — THE cmake half of the lib32
# build profile (G2), the toolchain-file twin of lib32-cross.ini. Keep the
# two in LOCKSTEP: compilers, -m32 -U_TIME_BITS, the i686 identity, the
# pinned pkg-config personality, and the llvm-config32 pin all mirror the
# meson cross file's [binaries]/[built-in options]/[host_machine] sections.
#
# Native 32-bit-on-x86_64 build. Deliberately does NOT set
# CMAKE_SYSTEM_NAME: setting it flips CMAKE_CROSSCOMPILING on, which (for
# LLVM and others) triggers a native-tools/cross split a same-OS -m32 build
# does not need — the target platform is still Linux; only the ABI narrows.
#
# Deliberately does NOT set CMAKE_LIBRARY_ARCHITECTURE: that variable
# models Debian-style multiarch (<prefix>/lib/<triple>), not our flat
# /usr/lib32 — setting it would misdirect find_library to paths that do
# not exist here. Do not "helpfully" add it.

# --- compilers + ABI flags (mirror lib32-cross.ini [binaries] c/cpp) ---
set(CMAKE_C_COMPILER   gcc)
set(CMAKE_CXX_COMPILER g++)
# -U_TIME_BITS is the RT-8 scrub half: a lib32 build must never compile
# with 64-bit time_t (struct-layout skew against time32 prebuilt binaries);
# the archive-time build-log assertion is the enforcement twin.
set(CMAKE_C_FLAGS_INIT   "-m32 -U_TIME_BITS")
set(CMAKE_CXX_FLAGS_INIT "-m32 -U_TIME_BITS")
set(CMAKE_EXE_LINKER_FLAGS_INIT    "-m32")
set(CMAKE_SHARED_LINKER_FLAGS_INIT "-m32")
set(CMAKE_MODULE_LINKER_FLAGS_INIT "-m32")

# --- target identity (mirror [host_machine]) ---
set(CMAKE_SYSTEM_PROCESSOR i686)

# --- find_library answers /usr/lib32 first and NEVER the 64-bit dirs ---
# CMAKE_FIND_LIBRARY_CUSTOM_LIB_SUFFIX: documented as replacing every lib/
# match with lib32/ in the search paths — the lever for a flat-/usr/lib32
# layout. CMAKE_IGNORE_PATH on the 64-bit libdirs is the belt to that
# suspender: a recipe-local `PATHS /usr/lib` cannot leak a 64-bit answer
# (the RT-7 leakage class, closed for cmake consumers).
set(CMAKE_FIND_LIBRARY_CUSTOM_LIB_SUFFIX 32)
list(APPEND CMAKE_IGNORE_PATH /usr/lib /lib /usr/lib64 /lib64)
set(CMAKE_FIND_ROOT_PATH /usr)
set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)   # config tools run on the host
set(CMAKE_FIND_ROOT_PATH_MODE_LIBRARY ONLY)
set(CMAKE_FIND_ROOT_PATH_MODE_INCLUDE BOTH)    # headers are shared/bit-agnostic
set(CMAKE_FIND_ROOT_PATH_MODE_PACKAGE ONLY)

# --- pinned config tools (mirror [binaries]) ---
set(PKG_CONFIG_EXECUTABLE /usr/bin/i686-igos-linux-gnu-pkg-config)
set(LLVM_CONFIG_EXECUTABLE /usr/bin/llvm-config32)
