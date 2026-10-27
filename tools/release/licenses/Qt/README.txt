Qt's third-party code
=====================

Qt version: 6.12

Qt Core, Qt GUI, Qt Network and Qt SVG, which the Studio's zip carries, contain
code from other projects under licences of their own. Each file here is one of
them, as Qt's documentation for Qt 6.12 gives it: what it is, where in Qt it is
used, and its copyright notices and licence text, verbatim.

Taken from https://doc.qt.io/qt-6.12/licenses-used-in-qt.html and the page it
links for each entry, on 2026-10-26, for the Qt version the release lock pins.
Each entry's version was checked against the qt_attribution.json files of
qtbase at tag v6.12.0. Entries for macOS, Android, Linux (X11 and Wayland) and
Qt modules the zip does not carry are left out.

XSVG's code moved from Qt SVG into Qt GUI in 6.12, and Qt 6.12's documentation
has no page for it. Its file is made from qtbase v6.12.0's
src/gui/util/qt_attribution.json and LICENSE.XSVG.txt, in the same shape.

The build reads "Qt version" above and refuses to package a Qt of another
major.minor version. A new Qt needs these files saved again, and that line
changed with them.

- qtcore-attribution-blake2.txt: BLAKE2 (reference implementation), version ed1974ea83433eba7b2d95c5dcd9ac33cb847913
- qtcore-attribution-doubleconversion.txt: Efficient Binary-Decimal and Decimal-Binary Conversion Routines for IEEE Doubles, version 3.4.0
- qtcore-attribution-easing.txt: Easing Equations by Robert Penner
- qtcore-attribution-forkfd.txt: forkfd
- qtcore-attribution-md4.txt: MD4
- qtcore-attribution-md5.txt: MD5
- qtcore-attribution-pcre2-sljit.txt: PCRE2 - Stack-less Just-In-Time Compiler, version 10.48
- qtcore-attribution-pcre2.txt: PCRE2, version 10.48
- qtcore-attribution-rfc6234.txt: Secure Hash Algorithms SHA-384 and SHA-512
- qtcore-attribution-sha1.txt: Secure Hash Algorithm SHA-1
- qtcore-attribution-sha3-endian.txt: Secure Hash Algorithm SHA-3 - brg_endian, version 1.0.0
- qtcore-attribution-sha3-keccak.txt: Secure Hash Algorithm SHA-3 - Keccak, version 3.2
- qtcore-attribution-siphash.txt: SipHash Algorithm
- qtcore-attribution-tika-mimetypes.txt: Apache Tika MimeType Definitions, version c29724782854bb5a319f4a1940d0828a58ace3f9
- qtcore-attribution-tinycbor.txt: TinyCBOR, version 7.0
- qtcore-attribution-tlexpected.txt: tl::expected, version 41d3e1f48d682992a2230b2a715bca38b848b269
- qtcore-attribution-unicode-character-database.txt: Unicode Character Database (UCD), version 36
- qtcore-attribution-unicode-cldr.txt: Unicode Common Locale Data Repository (CLDR), version v48.2
- qtcore-attribution-zlib.txt: Data Compression Library (zlib), version 1.3.2
- qtgui-attribution-aglfn.txt: Adobe Glyph List For New Fonts, version 1.7
- qtgui-attribution-d3d12memoryallocator.txt: D3D12 Memory Allocator, version v3.1.0
- qtgui-attribution-dejayvu.txt: DejaVu Fonts, version 2.37
- qtgui-attribution-emoji-segmenter.txt: Emoji Segmenter, version 0.4.0
- qtgui-attribution-freetype-bdf.txt: Freetype 2 - Bitmap Distribution Format (BDF) support
- qtgui-attribution-freetype-pcf.txt: Freetype 2 - Portable Compiled Format (PCF) support
- qtgui-attribution-freetype-zlib.txt: Freetype 2 - zlib
- qtgui-attribution-freetype.txt: Freetype 2, version 2.14.3
- qtgui-attribution-grayraster.txt: Anti-aliasing rasterizer from FreeType 2
- qtgui-attribution-harfbuzz-ng.txt: HarfBuzz-NG, version 14.5.0
- qtgui-attribution-icc-srgb-color-profile.txt: sRGB color profile icc file
- qtgui-attribution-libjpeg.txt: LibJPEG-turbo, version 3.2.0
- qtgui-attribution-libpng.txt: LibPNG, version 1.6.58
- qtgui-attribution-md4c.txt: MD4C, version 0.5.3
- qtgui-attribution-opengl-es2-headers.txt: OpenGL ES 2 Headers, version Revision 27673
- qtgui-attribution-opengl-headers.txt: OpenGL Headers, version Revision 27684
- qtgui-attribution-pixman.txt: Pixman, version 0.17.12
- qtgui-attribution-rhi-miniengine-d3d12-mipmap.txt: Mipmap generator for D3D12, version 0aa79bad78992da0b6a8279ddb9002c1753cb849
- qtgui-attribution-smooth-scaling-algorithm.txt: Smooth Scaling Algorithm
- qtgui-attribution-vulkan-xml-spec.txt: Vulkan API Registry, version 1.4.308
- qtgui-attribution-vulkanmemoryallocator.txt: Vulkan Memory Allocator, version 3.3.0
- qtgui-attribution-webgradients.txt: WebGradients
- qtgui-attribution-wintab.txt: Wintab API
- qtnetwork-attribution-libpsl.txt: libpsl - C library to handle the Public Suffix List, version 664f3dc85259ec65e30248a61fa1c45b7b0e4c3f
- qtnetwork-attribution-psl-data.txt: The Public Suffix List, version 2026-05-14_08-35-31_UTC
- qtsvg-attribution-xsvg.txt: XSVG
