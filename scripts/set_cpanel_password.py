"""Set or verify cPanel credential encrypted with user-scoped Windows DPAPI.

Run under the same Windows identity that executes Ticketing app.py.
"""
import getpass
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cpanel_credentials import (CredentialError, credential_file_path,
                                  load_encrypted_password, protect_password)


def main():
    if os.name != 'nt':
        print('This setup must run on the Windows Ticketing server.')
        return 1
    try:
        path = Path(credential_file_path())
        if '--verify' in sys.argv[1:]:
            load_encrypted_password(str(path))
            print('OK: DPAPI credential decrypted under the current Windows account.')
            return 0
        if sys.argv[1:]:
            print(r'Usage: python scripts\set_cpanel_password.py [--verify]')
            return 2
        print('Windows identity:', getpass.getuser())
        print('Run this under the SAME account used to run the Ticketing service.')
        print('Credential target:', path)
        secret = getpass.getpass('New cPanel password (input hidden): ')
        second = getpass.getpass('Repeat cPanel password: ')
        if secret != second:
            print('Passwords do not match; no file changed.')
            return 2
        encrypted = protect_password(secret)
        # Decrypt and verify before changing the on-disk credential.
        from cpanel_credentials import _dpapi
        if _dpapi(encrypted, True).decode('utf-8') != secret:
            raise CredentialError('DPAPI self-test failed. No file changed.')
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix='.cpanel-', suffix='.tmp', dir=str(path.parent))
        try:
            with os.fdopen(fd, 'wb') as out:
                out.write(encrypted)
                out.flush()
                os.fsync(out.fileno())
            # Keep a recoverable encrypted copy when changing an existing value.
            if path.exists():
                backup_path = str(path) + '.bak'
                if not os.path.exists(backup_path):
                    import shutil
                    shutil.copy2(path, backup_path)
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
        load_encrypted_password(str(path))
        print('Saved and verified DPAPI credential. No plaintext password file was written.')
        print('Check NTFS permissions: icacls "' + str(path.parent) + '"')
        return 0
    except CredentialError as exc:
        print('ERROR:', exc)
        return 1
    except OSError:
        print('ERROR: Cannot write the encrypted credential file.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
