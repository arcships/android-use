# License audit — 2026-11-06

This review covers the Android Use source, dependency declarations, native-package construction and sampled build artifacts. The project remains **MIT**, with the project copyright attributed to **Archship**. A native archive is not uniformly MIT: bundled dependencies retain their own terms.

| Component | Observed terms | Packaging treatment |
| --- | --- | --- |
| Android Use | MIT | Ship `LICENSE` with the Archship project copyright notice. |
| [iPhone Use](https://github.com/zhongerxin/iPhone-use) | MIT in the referenced repository | Acknowledge the inspiration in both READMEs. This acknowledgement does not replace license/copyright retention if code is reused. |
| CPython | PSF and its bundled-component terms | Carry the actual interpreter's license and upstream component notices. Freeze from a modular interpreter so optional DBM/readline modules can be excluded. |
| Most declared Python packages | MIT, BSD, Apache or related permissive expressions | Copy installed package notices; record exact versions, declarations and native file hashes. The environment inventory can include build-only packages and is labelled as a superset. |
| certifi | MPL-2.0 | Include its unmodified Python source and CA data under `licenses/sources/certifi`, in addition to the MPL license. |
| PyInstaller | GPL with a bundling exception; some Apache files | The exception permits generated applications to use their own licenses, subject to dependency terms. It does not mean the PyInstaller tool itself is MIT. Preserve available upstream notices. [Upstream explanation](https://pyinstaller.org/en/stable/license.html). |
| FFmpeg | LGPL-2.1-or-later for this restricted configuration | Enforce the exact build flags; no GPL/nonfree/version3 or extra-library flags. Ship the corresponding unmodified tarball, LGPL text, configure record and empty changes diff with each native archive. [FFmpeg licensing](https://www.ffmpeg.org/legal.html). |
| ADB and runtime libraries | Per-component Android Platform Tools notices | Ship only ADB, required DLLs/lib64, notices and version metadata. Exclude fastboot and filesystem utilities rather than distributing the entire upstream tools directory. |
| Pillow and native image libraries | MIT-CMU plus the wheel's bundled-library terms | Preserve Pillow's full LICENSE, including FreeType/JPEG and other dependency notices; do not label all native image libraries MIT-CMU. |
| macOS OCR | Android Use code plus system Apple frameworks | The helper is optional. Apple system frameworks are used, not included in the archive. |

## Findings addressed

1. **A real build-interpreter issue:** the first local uv-managed Python exposed `_dbm` as a built-in module and reported GNU gdbm. Excluding a Python import cannot remove code already linked into the interpreter. New builds reject built-in `_dbm`, `_gdbm` and `readline`; CI uses `actions/setup-python` rather than assuming every uv-managed interpreter has the same redistribution footprint. PyInstaller excludes the optional DBM/readline/Tk modules and the native-file check rejects unexpected DBM/readline binaries.
2. **The whole Platform Tools ZIP was unnecessary:** it includes utilities outside Android Use's needs, with additional licensing considerations. The runtime now selects ADB and its runtime support files and retains the upstream notice. Original upstream build-input archives are not copied wholesale into the runtime.
3. **Package-level MIT metadata was insufficient:** certifi is MPL-2.0 and image/crypto wheels include native dependencies. Add source-form certifi files, original package notices, pinned native notices and a machine-readable inventory.
4. **FFmpeg flags could previously be a superset:** checking only required flags could allow an unexpected extra feature/license option in a supplied binary. The builder now requires an exact option set. FFmpeg is a separate executable; the program's Python code is not linked against libavcodec. The FFmpeg executable and its own static libraries remain covered by their applicable terms and corresponding source materials.

5. **Notice regeneration and checkout fidelity:** the browser build now reads the maintained native-distribution introduction instead of overwriting it with source-only text. Windows checkouts preserve pinned upstream license bytes rather than converting LF to CRLF; a real Git checkout regression verifies the SHA256 stays unchanged.

## Evidence carried by each runtime

- `licenses/CPython-LICENSE.txt`: from the interpreter actually used to build it.
- `licenses/native/`: upstream component notices and `sources.json` provenance/digests. Some notices are a superset of components used on a particular platform. Modified pinned notice text fails the build.
- `licenses/python/`: notices from installed Python distributions.
- `licenses/sources/certifi/`: unmodified source form and CA data.
- `licenses/FFmpeg-LGPL-2.1.txt`, `ffmpeg-8.0.tar.xz`, `ffmpeg-build.json`, `ffmpeg-changes.diff`.
- `licenses/inventory.json`: actual Python/OpenSSL versions, package metadata and native file hashes. It is an inventory, not a complete SPDX dependency graph.
- `tools/adb/NOTICE.txt`: upstream notices for Android Platform Tools, retained as supplied.

The FFmpeg source is inside the same downloadable native archive as its binary. The package builder does not enable optional GPL libraries such as libx264. The small H.264 test fixture is encoded data, not a redistribution of the encoder executable.

## Scope and remaining limits

Metadata and filename checks alone cannot prove every statically linked component's provenance. The pinned notices and Python build gate reduce known risks; they are not a blanket legal certification for every OS, wheel and future dependency update. In particular, Windows distributions may contain Microsoft runtime DLLs with separate redistribution terms; the native inventory records their actual filenames. Those binaries are not reclassified as MIT or open source by this project.

This PR does not publish a release. Keep the per-platform acceptance artifacts and inventory with the release review, and repeat the scan when the interpreter, wheels, compiler or FFmpeg configuration changes. The first pre-audit local artifacts are diagnostic evidence, not the release-approved packages.

## 中文

项目源码仍采用 MIT，项目版权归属为 **Archship**。**独立运行包不能整体称为 MIT**：FFmpeg 按当前配置适用 LGPL-2.1-or-later，certifi 采用 MPL-2.0，Python 和各原生库也保留自身条款。PyInstaller 有打包例外，不能仅看到 GPL 标签就推断应用必须改协议。

本轮发现首个构建 Python 内置 GNU gdbm；仅排除模块导入不能移除已静态链接的代码。因此增加构建门禁，改用可排除模块的 CPython，并检查原生文件。ADB 只带实际需要的文件，避免附带无关工具；certifi 源码、Python/原生库声明、FFmpeg 对应源码与精确编译选项一并随包提供。中英文 README 已感谢 iPhone Use 的启发；实际代码复用仍需要保留原许可证和版权，不能只用致谢替代。

审计按当前声明、构建和产物执行，不把元数据扫描等同于完整二进制溯源或法律认证。Windows 可能包含微软可再分发运行库，其条款单独适用。本次保持 PR 审阅，不发布新版本；后续更新基础环境或依赖时，应连同原生清单一起复查。
