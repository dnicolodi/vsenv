import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import uuid


class Error(Exception):
    pass


def _windows_detect_native_arch():
    """The architecture of Windows itself: x86, amd64 or arm64."""
    if sys.platform != 'win32':
        return None

    try:
        import ctypes
        process_arch = ctypes.c_ushort()
        native_arch = ctypes.c_ushort()
        kernel32 = ctypes.windll.kernel32
        process = ctypes.c_void_p(kernel32.GetCurrentProcess())
        # This is the only reliable way to detect an arm system if we are an x86/x64 process being emulated.
        if kernel32.IsWow64Process2(process, ctypes.byref(process_arch), ctypes.byref(native_arch)):
            # https://docs.microsoft.com/en-us/windows/win32/sysinfo/image-file-machine-constants
            if native_arch.value == 0x8664:
                return 'amd64'
            elif native_arch.value == 0x014C:
                return 'x86'
            elif native_arch.value == 0xAA64:
                return 'arm64'
            elif native_arch.value == 0x01C4:
                return 'arm'
    except (OSError, AttributeError):
        pass

    # These env variables are always available. See:
    # https://msdn.microsoft.com/en-us/library/aa384274(VS.85).aspx
    # https://blogs.msdn.microsoft.com/david.wang/2006/03/27/howto-detect-process-bitness/
    arch = os.environ.get('PROCESSOR_ARCHITEW6432', '').lower()
    if not arch:
        try:
            # If this doesn't exist, something is messing with the environment.
            arch = os.environ['PROCESSOR_ARCHITECTURE'].lower()
        except KeyError:
            raise Error('Unable to detect native OS architecture')
    return arch


def _setup_env(arch, force):
    env = os.environ.copy()

    if sys.platform != 'win32':
        return env
    if os.environ.get('OSTYPE') == 'cygwin':
        return env

    if not force:
        for compiler in 'cc', 'gcc', 'clang', 'clang-cl':
            if shutil.which(compile):
                return env

    arch = arch or _windows_detect_native_arch()
            
    root = os.environ.get("ProgramFiles(x86)") or os.environ.get("ProgramFiles")

    vswhere = pathlib.Path(root, 'Microsoft Visual Studio/Installer/vswhere.exe')
    if not vswhere.exists():
        raise Error(f'Could not find {vswhere}')

    cmd = [
        os.fspath(vswhere),
        '-latest',
        '-prerelease',
        '-requiresAny',
        '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
        '-requires', 'Microsoft.VisualStudio.Workload.WDExpress',
        '-products', '*',
        '-utf8',
        '-format', 'json'
    ]
    out = subprocess.run(cmd, stdout=subprocess.PIPE, check=True).stdout

    info = json.loads(out)
    if not info:
        raise Error('Could not parse vswhere.exe output')

    installation_path = pathlib.Path(info[0]['installationPath'])
    
    if arch == 'arm64':
        vcvars = installation_path / 'VC/Auxiliary/Build/vcvarsarm64.bat'
        if not vcvars.exists():
            vcvars = installation_path / 'VC/Auxiliary/Build/vcvarsx86_arm64.bat'
    else:
        vcvars = installation_path / 'VC/Auxiliary/Build/vcvars64.bat'
        # If VS is not found try VS Express.
        if not vcvars.exists():
            vcvars = installation_path / 'VC/Auxiliary/Build/vcvarsx86_amd64.bat'

    if not vcvars.exists():
        raise Error('Could not find vcvars.bat')

    separator = str(uuid.uuid4())
    bat = textwrap.dedent(f'''
        @ECHO OFF
        call "{vcvars}"
        ECHO {separator}
        SET
        ''')

    tmp = tempfile.NamedTemporaryFile('w', suffix='.bat', encoding='utf-8', delete=False)
    tmp.write(bat)
    tmp.flush()
    tmp.close()

    try:
        out = subprocess.run(tmp.name, stdout=subprocess.PIPE, text=True, check=True).stdout
    finally:
        os.unlink(tmp.name)

    lines = iter(out.splitlines())
    for line in lines:
        if line == separator:
            break
    for line in lines:
        if not line:
            continue
        try:
            k, v = line.split('=', 1)
        except ValueError:
            # There is no "=", ignore.
            pass
        else:
            env[k] = v

    return env


def main():
    try:
        env = _setup_env(None, True)
    except Error as exc:
        print(f'error: {exc}', file=sys.stderr)
        os.exit(1)        
    os.execvpe(sys.argv[1], sys.argv[1:], env)


if __name__ == '__main__':
    main()
