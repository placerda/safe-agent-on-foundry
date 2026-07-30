from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.request import urlopen


OPA_VERSION = "1.18.2"
OPA_SHA256 = "9903e5125ac281104f2c4b7371d10cc3b74a98933743fcbfc174f9bf0ab20de8"
OPA_URL = (
    "https://github.com/open-policy-agent/opa/releases/download/"
    f"v{OPA_VERSION}/opa_linux_amd64_static"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TARGET = REPOSITORY_ROOT / "src" / "helpdeskbot" / "opa"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    if TARGET.is_file() and sha256(TARGET) == OPA_SHA256:
        print(f"Using cached OPA {OPA_VERSION}: {TARGET}")
        return

    temporary = TARGET.with_suffix(".download")
    try:
        with urlopen(OPA_URL, timeout=120) as response, temporary.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        actual = sha256(temporary)
        if actual != OPA_SHA256:
            raise ValueError(
                f"OPA {OPA_VERSION} SHA-256 mismatch: expected {OPA_SHA256}, got {actual}"
            )
        temporary.replace(TARGET)
        print(f"Prepared OPA {OPA_VERSION}: {TARGET}")
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
