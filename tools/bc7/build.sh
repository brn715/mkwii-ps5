#!/usr/bin/env bash
# Build the BC7 encoder used to produce the PS5 title backgrounds.
# The PS5 shell renders title backgrounds as DX10 BC7_UNORM DDS (3840x2160),
# not PNG. bc7enc_rdo's encoder is the reference implementation; only the
# sources needed for the CLI are vendored here.
set -euo pipefail
here=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
out=${1:-$here/bc7tool}
"${CXX:-clang++}" -std=c++17 -O2 -o "$out" \
    "$here/bc7tool.cpp" "$here/bc7enc.cpp" "$here/lodepng.cpp"
echo "built $out"
