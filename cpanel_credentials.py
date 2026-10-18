"""Windows DPAPI encryption for the cPanel credential (current Windows identity).

Do not put passwords in code, .env, command-line arguments, URLs, or logs.
The encrypted file is not portable: only the same Windows account on the
same Windows installation can decrypt it. No machine-wide DPAPI mode is used.
"""
import ctypes
import os
from ctypes import wintypes
from pathlib import Path

DEFAULT_FILE = str(Path(__file__).resolve().parent / 'secrets' / 'cpanel_password.dpapi')
FLAG_UI_FORBIDDEN = 0x1


class CredentialError(Exception):
    pass


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_ubyte))]


def credential_file_path():
    path = os.path.realpath(os.getenv('CPANEL_PASSWORD_DPAPI_FILE', DEFAULT_FILE))
    static_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), 'static'))
    try:
        if os.path.commonpath((path, static_dir)) == static_dir:
            raise CredentialError('Credential file must be outside the static directory.')
    except ValueError:
        pass  # different drives on Windows
    return path


def _dpapi(raw, decrypt):
    if os.name != 'nt':
        raise CredentialError('Windows DPAPI is required for cPanel password encryption.')
    try:
        crypt = ctypes.WinDLL('crypt32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        rawbuf = ctypes.create_string_buffer(raw)
        source = _DATA_BLOB(len(raw), ctypes.cast(rawbuf, ctypes.POINTER(ctypes.c_ubyte)))
        output = _DATA_BLOB()
        if decrypt:
            method = crypt.CryptUnprotectData
            method.argtypes = [ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p,
                ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
            args = [ctypes.byref(source), None, None, None, None, FLAG_UI_FORBIDDEN, ctypes.byref(output)]
        else:
            method = crypt.CryptProtectData
            method.argtypes = [ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR,
                ctypes.POINTER(_DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
                wintypes.DWORD, ctypes.POINTER(_DATA_BLOB)]
            args = [ctypes.byref(source), 'POLO cPanel UAPI authentication', None, None, None,
                    FLAG_UI_FORBIDDEN, ctypes.byref(output)]
        method.restype = wintypes.BOOL
        if not method(*args):
            raise CredentialError('Windows DPAPI operation failed for this account.')
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree.restype = ctypes.c_void_p
            kernel.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
    except CredentialError:
        raise
    except Exception:
        raise CredentialError('Windows DPAPI is unavailable for this account.') from None


def protect_password(password):
    if not isinstance(password, str) or len(password) < 11 or len(password) > 512:
        raise CredentialError('Password length must be between 11 and 512 characters.')
    if any(c in password for c in ('\x00', '\r', '\n')):
        raise CredentialError('Password contains invalid control characters.')
    return _dpapi(password.encode('utf-8'), False)


def load_encrypted_password(path=None):
    path = path or credential_file_path()
    try:
        if os.path.getsize(path) > 16384:
            raise CredentialError('Encrypted credential file is invalid.')
        with open(path, 'rb') as f:
            encrypted = f.read(16385)
        if not encrypted or len(encrypted) > 16384:
            raise CredentialError('Encrypted credential file is invalid.')
    except OSError:
        raise CredentialError('Encrypted credential file is missing or unreadable.') from None
    try:
        result = _dpapi(encrypted, True).decode('utf-8')
    except (UnicodeDecodeError, ValueError):
        raise CredentialError('Encrypted credential file cannot be decrypted.') from None
    if not result or len(result) > 512:
        raise CredentialError('Encrypted credential file is invalid.')
    return result
