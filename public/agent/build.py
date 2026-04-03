#!/usr/bin/env python3
"""
StarlightRDT build script — run this on the target OS to produce the installer.

  Windows : produces  dist/starlight-rdt-setup.exe  (via PyInstaller + Inno Setup)
            and       dist/starlight-rdt-portable.zip
  macOS   : produces  dist/StarlightRDT.dmg        (via PyInstaller + create-dmg)
  Linux   : produces  dist/starlight-rdt.deb         (via PyInstaller + dpkg-deb)

Usage:
  pip install -r requirements.txt
  python build.py
"""

import os, sys, shutil, platform, subprocess, zipfile, tempfile
from pathlib import Path

HERE    = Path(__file__).parent.resolve()
DIST    = HERE / 'dist'
ROOT    = HERE.parent
ICON_WIN = HERE / 'icons' / 'icon.ico'
ICON_MAC = HERE / 'icons' / 'icon.icns'
ICON_LIN = HERE / 'icons' / 'icon.png'
PLAT    = platform.system()
VERSION = '1.0.0'


def run(cmd, **kw):
    print(f'  > {" ".join(str(c) for c in cmd)}')
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def check_pyinstaller():
    try:
        import PyInstaller
        print(f'PyInstaller {PyInstaller.__version__} found')
    except ImportError:
        print('Installing PyInstaller...')
        run([sys.executable, '-m', 'pip', 'install', 'pyinstaller'])


def pyinstaller_base(extra_args=None):
    DIST.mkdir(exist_ok=True)
    icon = None
    if PLAT == 'Windows' and ICON_WIN.exists():
        icon = ICON_WIN
    elif PLAT == 'Darwin' and ICON_MAC.exists():
        icon = ICON_MAC
    elif PLAT == 'Linux' and ICON_LIN.exists():
        icon = ICON_LIN

    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--onefile',
        '--name', 'StarlightRDT',
        '--distpath', DIST,
        '--workpath', HERE / 'build',
        '--specpath', HERE / 'build',
        '--clean',
        '--noconfirm',
    ]
    if PLAT in ('Windows', 'Darwin'):
        cmd.append('--windowed')
    if icon:
        cmd += ['--icon', icon]
    if extra_args:
        cmd += extra_args

    cmd += [
        '--hidden-import', 'mss',
        '--hidden-import', 'PIL',
        '--hidden-import', 'pyautogui',
        '--hidden-import', 'websockets',
        '--hidden-import', 'pystray',
        '--hidden-import', 'pyperclip',
    ]
    if PLAT == 'Darwin':
        cmd += ['--hidden-import', 'rumps']
    cmd += [HERE / 'agent.py']
    run(cmd)


def build_windows():
    print('\n── Windows build ──')
    check_pyinstaller()
    pyinstaller_base()

    exe = DIST / 'StarlightRDT.exe'
    if not exe.exists():
        print('ERROR: PyInstaller did not produce StarlightRDT.exe')
        sys.exit(1)

    portable_zip = DIST / 'starlight-rdt-portable.zip'
    with zipfile.ZipFile(portable_zip, 'w', zipfile.ZIP_DEFLATED) as z:
        z.write(exe, 'StarlightRDT.exe')
        readme = 'Run StarlightRDT.exe — no installation needed.\n'
        z.writestr('README.txt', readme)
    print(f'Portable zip: {portable_zip}')

    inno = shutil.which('iscc') or shutil.which('ISCC')
    if inno:
        iss = _write_inno_script(exe)
        run([inno, iss])
        setup_out = DIST / 'starlight-rdt-setup.exe'
        print(f'Installer: {setup_out}')
    else:
        print('Inno Setup not found — skipping installer (portable zip only)')
        print('Install from: https://jrsoftware.org/isdl.php')
        shutil.copy(exe, DIST / 'starlight-rdt-setup.exe')
        print('Copied exe as starlight-rdt-setup.exe (not a true installer)')

    print('\nWindows build complete:')
    for f in DIST.glob('starlight-rdt*'):
        print(f'  {f.name}  ({f.stat().st_size // 1024} KB)')


def _write_inno_script(exe: Path) -> Path:
    iss_path = HERE / 'build' / 'installer.iss'
    iss_path.parent.mkdir(exist_ok=True)
    iss_path.write_text(f"""
[Setup]
AppName=StarlightRDT
AppVersion={VERSION}
AppPublisher=StarlightRDT
DefaultDirName={{autopf}}\\StarlightRDT
DefaultGroupName=StarlightRDT
OutputDir={DIST}
OutputBaseFilename=starlight-rdt-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "{exe}"; DestDir: "{{app}}"; Flags: ignoreversion

[Icons]
Name: "{{group}}\\StarlightRDT"; Filename: "{{app}}\\StarlightRDT.exe"
Name: "{{group}}\\Uninstall StarlightRDT"; Filename: "{{uninstallexe}}"
Name: "{{commonstartup}}\\StarlightRDT"; Filename: "{{app}}\\StarlightRDT.exe"

[Run]
Filename: "{{app}}\\StarlightRDT.exe"; Description: "Launch StarlightRDT"; Flags: nowait postinstall skipifsilent
""")
    return iss_path


def build_macos():
    print('\n── macOS build ──')
    check_pyinstaller()
    pyinstaller_base()

    app = DIST / 'StarlightRDT.app'
    if not app.exists():
        print('ERROR: PyInstaller did not produce StarlightRDT.app')
        sys.exit(1)

    dmg = DIST / 'StarlightRDT.dmg'
    create_dmg = shutil.which('create-dmg')
    if create_dmg:
        run([
            create_dmg,
            '--volname', 'StarlightRDT',
            '--window-size', '540', '380',
            '--icon-size', '128',
            '--icon', 'StarlightRDT.app', '140', '180',
            '--hide-extension', 'StarlightRDT.app',
            '--app-drop-link', '400', '180',
            dmg, str(DIST),
        ])
    else:
        print('create-dmg not found — building plain DMG via hdiutil')
        tmp = tempfile.mkdtemp()
        shutil.copytree(app, Path(tmp) / 'StarlightRDT.app', dirs_exist_ok=True)
        run(['hdiutil', 'create', '-volname', 'StarlightRDT', '-srcfolder', tmp,
             '-ov', '-format', 'UDZO', dmg])
        shutil.rmtree(tmp)
        print('Install create-dmg for a nicer DMG: brew install create-dmg')

    print(f'\nmacOS build complete: {dmg}')


def build_linux():
    print('\n── Linux build ──')
    check_pyinstaller()
    pyinstaller_base(['--strip'])

    binary = DIST / 'StarlightRDT'
    if not binary.exists():
        print('ERROR: PyInstaller did not produce StarlightRDT binary')
        sys.exit(1)

    pkg = HERE / 'build' / 'deb'
    for d in ['usr/bin', 'usr/share/applications', 'usr/share/pixmaps',
              f'usr/share/doc/starlight-rdt', 'DEBIAN']:
        (pkg / d).mkdir(parents=True, exist_ok=True)

    shutil.copy(binary, pkg / 'usr/bin/starlight-rdt')
    (pkg / 'usr/bin/starlight-rdt').chmod(0o755)

    (pkg / 'usr/share/applications/starlight-rdt.desktop').write_text(
        '[Desktop Entry]\n'
        'Name=StarlightRDT\n'
        'Comment=Remote Desktop Agent\n'
        'Exec=starlight-rdt\n'
        'Icon=starlight-rdt\n'
        'Type=Application\n'
        'Categories=Network;RemoteAccess;\n'
        'StartupNotify=false\n'
    )

    if ICON_LIN.exists():
        shutil.copy(ICON_LIN, pkg / 'usr/share/pixmaps/starlight-rdt.png')

    binary_size = binary.stat().st_size // 1024
    (pkg / 'DEBIAN/control').write_text(
        f'Package: starlight-rdt\n'
        f'Version: {VERSION}\n'
        f'Section: net\n'
        f'Priority: optional\n'
        f'Architecture: amd64\n'
        f'Installed-Size: {binary_size}\n'
        f'Maintainer: StarlightRDT <support@starlight-rdt.app>\n'
        f'Description: StarlightRDT Remote Desktop Agent\n'
        f' Browser-based remote desktop — share your screen with one code.\n'
    )

    (pkg / 'DEBIAN/postinst').write_text(
        '#!/bin/sh\n'
        'chmod +x /usr/bin/starlight-rdt\n'
        'echo "StarlightRDT installed. Run: starlight-rdt"\n'
    )
    (pkg / 'DEBIAN/postinst').chmod(0o755)

    deb = DIST / 'starlight-rdt.deb'
    run(['dpkg-deb', '--build', '--root-owner-group', str(pkg), str(deb)])
    print(f'\nLinux build complete: {deb}')


if __name__ == '__main__':
    print(f'StarlightRDT v{VERSION} — building for {PLAT}')
    DIST.mkdir(exist_ok=True)
    if PLAT == 'Windows':
        build_windows()
    elif PLAT == 'Darwin':
        build_macos()
    elif PLAT == 'Linux':
        build_linux()
    else:
        print(f'Unknown platform: {PLAT}')
        sys.exit(1)