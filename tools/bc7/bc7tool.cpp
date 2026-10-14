// Minimal BC7 encoder CLI: read a PNG/TGA, write a DX10 BC7_UNORM DDS.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include <string>
#include "bc7enc.h"
#include "dds_defs.h"
#include "lodepng.h"

int main(int argc, char** argv) {
    if (argc < 3) { std::fprintf(stderr, "usage: bc7tool in.png out.dds\n"); return 2; }
    std::vector<unsigned char> rgba;
    unsigned w=0,h=0;
    unsigned err = lodepng::decode(rgba, w, h, argv[1]);
    if (err) { std::fprintf(stderr, "png load failed: %s\n", lodepng_error_text(err)); return 1; }
    std::printf("input %ux%u\n", w, h);
    if ((w % 4) || (h % 4)) { std::fprintf(stderr, "dimensions must be multiples of 4\n"); return 1; }

    const unsigned blocks_x = w/4, blocks_y = h/4;
    std::vector<unsigned char> out(size_t(blocks_x)*blocks_y*16);

    bc7enc_compress_block_init();   // required: builds the endpoint tables
    bc7enc_compress_block_params params;
    bc7enc_compress_block_params_init(&params);
    params.m_uber_level = 0;      // default quality/speed
    params.m_perceptual = true;

    for (unsigned by=0; by<blocks_y; ++by) {
        for (unsigned bx=0; bx<blocks_x; ++bx) {
            unsigned char block[64];
            for (unsigned y=0; y<4; ++y)
                for (unsigned x=0; x<4; ++x) {
                    const unsigned char* p = &rgba[((by*4+y)*w + (bx*4+x))*4];
                    std::memcpy(&block[(y*4+x)*4], p, 4);
                }
            unsigned char* dst = &out[(size_t(by)*blocks_x + bx)*16];
            bc7enc_compress_block(dst, block, &params);
        }
    }

    // --- write DX10 BC7_UNORM DDS ---
    FILE* f = std::fopen(argv[2], "wb");
    if (!f) { std::fprintf(stderr, "cannot write %s\n", argv[2]); return 1; }
    DDSURFACEDESC2 dds{}; memset(&dds,0,sizeof(dds));
    dds.dwSize = sizeof(dds);
    dds.dwFlags = DDSD_CAPS|DDSD_HEIGHT|DDSD_WIDTH|DDSD_PIXELFORMAT|DDSD_LINEARSIZE|DDSD_MIPMAPCOUNT;
    dds.dwHeight = h; dds.dwWidth = w;
    dds.dwLinearSize = (uint32_t)out.size();
    dds.dwMipMapCount = 1;
    dds.ddsCaps.dwCaps = DDSCAPS_TEXTURE;
    dds.ddpfPixelFormat.dwSize = sizeof(DDPIXELFORMAT);
    dds.ddpfPixelFormat.dwFlags = DDPF_FOURCC;
    dds.ddpfPixelFormat.dwFourCC = 0x30315844u; /* 'DX10' as stored little-endian */
    std::fwrite("DDS ", 1, 4, f);
    std::fwrite(&dds, 1, sizeof(dds), f);
    unsigned dxgi[5] = { 98 /*BC7_UNORM*/, 3 /*TEXTURE2D*/, 0, 1, 1 };
    std::fwrite(dxgi, sizeof(unsigned), 5, f);
    std::fwrite(out.data(), 1, out.size(), f);
    std::fclose(f);
    std::printf("wrote %s: %zux%zu blocks, %zu bytes payload\n", argv[2],
                size_t(blocks_x), size_t(blocks_y), out.size());
    return 0;
}
