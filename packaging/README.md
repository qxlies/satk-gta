# packaging/

Inputs of the portable release built by `satk dev release` (code: `src/satk/release/`; user guide:
[docs/en/release.md](../docs/en/release.md)).

| Path | What it is |
|---|---|
| `portable/` | files copied to the root of `satk-<version>-win64.zip`: launchers (ASCII `.cmd`, CRLF), `README-FIRST.txt` (English) and `README-FIRST.ru.txt` (Russian), `portable.txt`; `Start satk.cmd` is also shipped under its Russian name |
| `CHANGELOG.md` | release history (Keep a Changelog), shipped as `CHANGELOG.md` |
| `release-lock.json` | pinned embeddable CPython and runtime wheels with SHA-256; written by `satk dev release --relock` |
| `sandbox/check.ps1` | the check that runs inside Windows Sandbox (`--sandbox prepare\|run`) |

## Build

```powershell
satk dev release                    # zip + wheel + sdist + SHA256SUMS.txt, then a quick smoke run
satk dev release --smoke game       # also init + index build + asset find on the clean game copy
satk dev release --sandbox run      # also the Windows Sandbox check
```

## Update the pinned inputs

```powershell
satk dev release --relock           # closure from the venv, hashes from PyPI and python.org
git add packaging/release-lock.json
```

`--relock` needs the network and the development venv (it reads the installed versions, which must equal
`requirements.lock`, and uses `packaging` to evaluate markers and wheel tags). It verifies the embeddable
zip by size and MD5 (python.org release API) and by the Authenticode signatures of its binaries.

The OpenPGP signature is checked by hand, once per Python version (Git for Windows ships `gpg`; use a
throw-away key ring under `work/tmp`):

```bash
export GNUPGHOME=<workspace>/work/tmp/release-gpg && mkdir -p "$GNUPGHOME" && chmod 700 "$GNUPGHOME"
cd <workspace>/work/cache/release
curl -sSfLO https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip.asc
curl -sSfL -o stevedower.asc "https://keybase.io/stevedower/pgp_keys.asc?fingerprint=7ed10b6531d7c8e1bc296021fc624643487034e5"
gpg --batch --import stevedower.asc
gpg --batch --verify python-3.12.10-embed-amd64.zip.asc python-3.12.10-embed-amd64.zip
```

Expected: `Good signature from "Steve Dower (Python Release Signing)"`, primary key fingerprint
`7ED1 0B65 31D7 C8E1 BC29 6021 FC62 4643 4870 34E5` (the Windows key listed on
<https://www.python.org/downloads/metadata/pgp/>). Checked for 3.12.10 on 2026-10-05.
