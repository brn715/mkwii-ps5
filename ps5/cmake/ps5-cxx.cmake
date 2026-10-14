# Cross-compilation headers based on FreeBSD 9.3. These are not a Sony SDK:
# ABI-sensitive interfaces must be checked against SharpProspero and hardware.
#
# Host portability: upstream pins the Windows llvm-mingw archive and names its
# binaries with a .exe suffix. The same release ships a macOS universal archive;
# on a non-Windows host use the suffix-less names and the platform's own
# llvm-ar/llvm-ranlib from that toolchain. Nothing else about the target changes.
set(CMAKE_SYSTEM_NAME FreeBSD)
set(CMAKE_SYSTEM_VERSION 9.3)
set(CMAKE_SYSTEM_PROCESSOR x86_64)
set(CMAKE_TRY_COMPILE_TARGET_TYPE STATIC_LIBRARY)
get_filename_component(MKW_WORKSPACE "${CMAKE_CURRENT_LIST_DIR}/../.." ABSOLUTE)
if(WIN32)
    file(GLOB MKW_CLANG_CANDIDATES "${MKW_WORKSPACE}/.tools/llvm-mingw-*/bin/clang.exe")
else()
    file(GLOB MKW_CLANG_CANDIDATES "${MKW_WORKSPACE}/.tools/llvm-mingw-*/bin/clang")
endif()
list(GET MKW_CLANG_CANDIDATES 0 MKW_CLANG)
get_filename_component(MKW_LLVM_BIN "${MKW_CLANG}" DIRECTORY)
if(WIN32)
    set(MKW_EXE ".exe")
else()
    set(MKW_EXE "")
endif()
set(CMAKE_C_COMPILER "${MKW_LLVM_BIN}/clang${MKW_EXE}")
set(CMAKE_CXX_COMPILER "${MKW_LLVM_BIN}/clang++${MKW_EXE}")
set(CMAKE_ASM_COMPILER "${MKW_LLVM_BIN}/clang${MKW_EXE}")
set(CMAKE_C_COMPILER_TARGET x86_64-unknown-freebsd9.3)
set(CMAKE_CXX_COMPILER_TARGET x86_64-unknown-freebsd9.3)
set(CMAKE_ASM_COMPILER_TARGET x86_64-unknown-freebsd9.3)
set(CMAKE_AR "${MKW_LLVM_BIN}/llvm-ar${MKW_EXE}")
set(CMAKE_RANLIB "${MKW_LLVM_BIN}/llvm-ranlib${MKW_EXE}")
set(CMAKE_SYSROOT "${MKW_WORKSPACE}/.tools/ps5-sysroot")
set(CMAKE_C_FLAGS_INIT "--no-default-config -fPIC -fno-stack-protector")
set(CMAKE_CXX_FLAGS_INIT "--no-default-config -fPIC -fno-stack-protector")
set(CMAKE_ASM_FLAGS_INIT "--no-default-config --target=x86_64-unknown-freebsd9.3 --sysroot=\"${CMAKE_SYSROOT}\" -fPIC")
