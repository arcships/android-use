"""Collect redistributable notices/source and audit the actual frozen native files."""

import hashlib
import importlib.metadata
import json
import platform
import shutil
import ssl
import sys
import sysconfig
from pathlib import Path

EXCLUDED_MODULES = ("_dbm", "_gdbm", "readline", "tkinter", "PIL.ImageTk")


def check_interpreter():
    forbidden = {"_dbm", "_gdbm", "readline"}.intersection(sys.builtin_module_names)
    if forbidden:
        raise ValueError(
            "Build Python statically includes optional DBM/readline: "
            + ", ".join(sorted(forbidden))
            + ". Use a modular CPython build; these modules must be excludable."
        )


def collect(root, bundle):
    licenses = bundle / "licenses"
    licenses.mkdir()
    for filename in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(root / filename, licenses / filename)
    vendor = root / "docs/licenses"
    if not (vendor / "sources.json").is_file():
        raise ValueError("Pinned native dependency notices are missing")
    sources = json.loads((vendor / "sources.json").read_text())
    (licenses / "native").mkdir()
    for source in sources:
        if Path(source["file"]).name != source["file"]:
            raise ValueError("Invalid native notice filename")
        if hashlib.sha256((vendor / source["file"]).read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("A pinned dependency notice was modified")
        shutil.copyfile(vendor / source["file"], licenses / "native" / source["file"])
    shutil.copyfile(vendor / "sources.json", licenses / "native/sources.json")
    if (vendor / "README.md").is_file():
        shutil.copyfile(vendor / "README.md", licenses / "native/README.md")
    python_license = Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"
    if not python_license.is_file():
        python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ValueError("CPython license missing from build interpreter")
    shutil.copyfile(python_license, licenses / "CPython-LICENSE.txt")
    packages = []
    for distribution in sorted(
        importlib.metadata.distributions(), key=lambda d: d.metadata["Name"].lower()
    ):
        copied = []
        for file in distribution.files or []:
            if any(part in {"..", "."} for part in file.parts) or file.is_absolute():
                continue
            if any(
                part.lower().startswith(("license", "copying", "notice", "copyright"))
                for part in file.parts
            ):
                path = Path(distribution.locate_file(file))
                if path.is_file():
                    destination = licenses / "python" / str(file)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
                    copied.append(str(destination.relative_to(bundle)))
        packages.append(
            {
                "name": distribution.metadata["Name"],
                "version": distribution.version,
                "declared_license": distribution.metadata.get("License-Expression")
                or distribution.metadata.get("License"),
                "license_classifiers": [
                    c
                    for c in distribution.metadata.get_all("Classifier", [])
                    if c.startswith("License")
                ],
                "notices": copied,
            }
        )
    # Certifi's MPL-2.0 applies at file level: carry its unmodified source form too.
    certifi = importlib.metadata.distribution("certifi")
    for file in certifi.files or []:
        if file.parts[0] == "certifi" and str(file).endswith((".py", ".pem")):
            source = Path(certifi.locate_file(file))
            destination = licenses / "sources" / str(file)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    native_files = []
    for file in sorted(bundle.rglob("*")):
        if file.is_file() and (
            file.suffix in {".dll", ".so", ".dylib", ".pyd", ".exe"}
            or str(file.relative_to(bundle))
            in {"app/android-use-runtime", "tools/adb/adb", "tools/ffmpeg"}
        ):
            if any(
                part in file.name.lower()
                for part in (
                    "libgdbm",
                    "readline",
                    "_gdbm",
                    "_dbm",
                    "fastboot",
                    "mke2fs",
                    "make_f2fs",
                )
            ):
                raise ValueError("Unexpected optional DBM/readline binary: " + file.name)
            with file.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            native_files.append({"file": str(file.relative_to(bundle)), "sha256": digest})
    report = {
        "scope": "Python build environment declarations (a superset of frozen imports), notices and native file hashes; not a complete binary provenance/legal certification",
        "python": platform.python_version(),
        "openssl": ssl.OPENSSL_VERSION,
        "excluded_optional_modules": list(EXCLUDED_MODULES),
        "packages": packages,
        "native_files": native_files,
    }
    (licenses / "inventory.json").write_text(json.dumps(report, indent=2) + "\n")
