# DeltaRDT Downloads

Put compiled installer files here. The website's `triggerDownload()` function
does a HEAD request for each file — if it exists it downloads, if not it shows
a "coming soon" message.

## Expected filenames

| File | Platform |
|------|----------|
| `deltardt-setup.exe` | Windows installer (Inno Setup) |
| `deltardt-portable.zip` | Windows portable (no install) |
| `DeltaRDT.dmg` | macOS disk image |
| `deltardt.deb` | Debian/Ubuntu package |

## How to build

Run `agent/build.py` on each target OS — it produces the right file and puts
it in `agent/dist/`. Copy the output here.

### Windows

```
pip install -r agent/requirements.txt
python agent/build.py
copy agent\dist\deltardt-setup.exe downloads\
copy agent\dist\deltardt-portable.zip downloads\
```

Requires [Inno Setup](https://jrsoftware.org/isdl.php) for the installer.
Without it, `build.py` copies the raw `.exe` as `deltardt-setup.exe`.

### macOS

```
pip3 install -r agent/requirements.txt
python3 agent/build.py
cp agent/dist/DeltaRDT.dmg downloads/
```

Optional: `brew install create-dmg` for a nicer DMG with drag-to-Applications.

### Linux

```
pip3 install -r agent/requirements.txt
python3 agent/build.py
cp agent/dist/deltardt.deb downloads/
```

Requires `dpkg-deb` (installed by default on Debian/Ubuntu).

## GitHub Actions (automated builds)

See `.github/workflows/build.yml` to build all three platforms automatically
on every release tag using GitHub-hosted runners.