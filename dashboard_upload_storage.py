"""Private QNAP storage for Ticketing uploads (Revisi 9).

The same QNAP shared folder was verified from .74 and .99. Primary is preferred;
secondary is selected only when the primary path is unavailable at operation
start. Permanent uploads never fall back to local storage.
"""
import ctypes
from ctypes import wintypes
import ntpath
import os
import re
import unicodedata
from pathlib import Path
from secrets import token_hex

PRIVATE_FOLDER = 'root_dashboard'
PHOTO_FOLDER = 'Ticket_Photos'
DISCUSSION_FOLDER = 'Project_Discussion'
GUIDE_FOLDER = 'Guide'


class QnapUploadsUnavailable(OSError):
    """QNAP upload storage cannot currently be accessed."""


class DashboardUploadStorage:
    def __init__(self, share, legacy_root, username='', password='', domain='',
                 secondary_share=None, private_relative=r'POLO\root_dashboard'):
        if not share or not str(share).strip():
            raise ValueError('Primary QNAP share path is required')
        parts = str(private_relative).replace('\\', '/').split('/')
        if not parts or any(p in ('', '.', '..') for p in parts):
            raise ValueError('Invalid QNAP relative storage root')
        self.shares = [Path(share)]
        if secondary_share and str(secondary_share).strip() and str(secondary_share).lower() != str(share).lower():
            self.shares.append(Path(secondary_share))
        self.roots = [s.joinpath(*parts) for s in self.shares]
        self.share = self.shares[0]
        self.root = self.roots[0]
        self.legacy = Path(legacy_root)
        self.username = username
        self.password = password
        self.domain = domain

    def _connect(self, share=None):
        share = share or self.share
        if share.is_dir():
            return
        if os.name != 'nt' or not self.username:
            raise QnapUploadsUnavailable(f'QNAP share unavailable: {share}. Check SMB access and service account.')

        class NETRESOURCEW(ctypes.Structure):
            _fields_ = [('dwScope', wintypes.DWORD), ('dwType', wintypes.DWORD),
                        ('dwDisplayType', wintypes.DWORD), ('dwUsage', wintypes.DWORD),
                        ('lpLocalName', wintypes.LPWSTR), ('lpRemoteName', wintypes.LPWSTR),
                        ('lpComment', wintypes.LPWSTR), ('lpProvider', wintypes.LPWSTR)]

        username = f'{self.domain}\\{self.username}' if self.domain else self.username
        resource = NETRESOURCEW(0, 1, 0, 0, None, str(share), None, None)
        rc = ctypes.windll.mpr.WNetAddConnection2W(
            ctypes.byref(resource), self.password or None, username, 0)
        if rc not in (0, 1219) or not share.is_dir():
            raise QnapUploadsUnavailable(f'QNAP SMB connection failed (Windows code {rc}): {share}')

    def active_root(self):
        """Select reachable root, never fabricate a missing directory."""
        problems = []
        for share, root in zip(self.shares, self.roots):
            try:
                self._connect(share)
                if root.is_dir():
                    return root
                problems.append(f'{root}: directory not found or access denied')
            except OSError as exc:
                problems.append(str(exc))
        raise QnapUploadsUnavailable('QNAP upload storage unavailable on primary and secondary: ' + '; '.join(problems))

    def ensure_root(self):
        return self.active_root()

    @staticmethod
    def _name(name):
        name = str(name)
        if not name or name in ('.', '..') or any(c in name for c in '/\\:\x00') or Path(name).name != name:
            raise ValueError('Unsafe stored upload filename')
        return name

    @staticmethod
    def project_folder_name(title, pid):
        """Stable, readable, collision-safe folder for a new project."""
        if type(pid) is not int or pid <= 0:
            raise ValueError('Invalid project id')
        # QNAP is accessed from Windows: remove Windows-invalid, control and
        # invisible formatting characters, without losing meaningful words.
        cleaned = unicodedata.normalize('NFKC', str(title))
        cleaned = ''.join(' ' if not c.isprintable() else c for c in cleaned)
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', cleaned)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip(' .-')
        if not cleaned:
            cleaned = 'Project'
        # Suffix makes duplicates safe and the path stable if titles change.
        return f'{cleaned[:85].rstrip(" .-")} - P{pid:06d}'

    @classmethod
    def _folder(cls, root, category, pid=None, project_folder=None):
        if category == 'ticket':
            return root / PHOTO_FOLDER
        if category == 'project' and type(pid) is int and pid > 0:
            folder = cls._name(project_folder) if project_folder else str(pid)
            return root / DISCUSSION_FOLDER / folder
        if category == 'guide' and type(pid) is int and pid > 0:
            return root / GUIDE_FOLDER / f'G{pid:06d}'
        raise ValueError('Invalid dashboard upload category or project id')

    def ensure_project_folder(self, title, pid):
        """Create a new project folder before the project's DB commit."""
        name = self.project_folder_name(title, pid)
        folder = self._folder(self.active_root(), 'project', pid, project_folder=name)
        try:
            # Never silently reuse a directory from a failed/old transaction.
            folder.mkdir(parents=True, exist_ok=False)
            if not folder.is_dir():
                raise OSError('Project folder could not be verified')
        except OSError as exc:
            raise QnapUploadsUnavailable(f'Cannot create QNAP project folder: {exc}') from exc
        return name, folder

    def _legacy_folder(self, category, pid=None):
        if category == 'ticket':
            return self.legacy
        if category == 'project' and type(pid) is int and pid > 0:
            return self.legacy / 'project_comments' / str(pid)
        if category == 'guide' and type(pid) is int and pid > 0:
            return self.legacy / 'guide' / str(pid)
        raise ValueError('Invalid dashboard upload category or project id')

    def write(self, category, name, data, pid=None, project_folder=None):
        name = self._name(name)
        root = self.active_root()  # choose primary/secondary before beginning the write
        folder = self._folder(root, category, pid, project_folder=project_folder)
        tmp = None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / name
            if target.exists():
                raise FileExistsError(f'Duplicate stored filename: {name}')
            tmp = folder / ('.' + token_hex(12) + '.part')
            with tmp.open('xb') as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if tmp.stat().st_size != len(data):
                raise OSError('QNAP upload size verification failed')
            os.replace(tmp, target)
            return target
        except OSError as exc:
            # Do not retry a possibly partially committed write on the other IP.
            # The next independent upload will select the available interface.
            raise QnapUploadsUnavailable(f'Unable to write QNAP attachment: {exc}') from exc
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass

    def resolve(self, category, name, pid=None, project_folder=None):
        name = self._name(name)
        for share, root in zip(self.shares, self.roots):
            try:
                self._connect(share)
                candidate = self._folder(root, category, pid, project_folder=project_folder) / name
                if candidate.is_file():
                    return candidate
                # Original numeric folders remain a read-only compatibility
                # source for legacy projects after verified migrations.
                if category == 'project' and project_folder:
                    previous = self._folder(root, category, pid) / name
                    if previous.is_file():
                        return previous
            except OSError:
                pass
        local = self._legacy_folder(category, pid) / name
        if local.is_file():
            return local
        return None

    def remove(self, category, name, pid=None, project_folder=None):
        name = self._name(name)
        problems = []
        accessible = False
        for share, root in zip(self.shares, self.roots):
            try:
                self._connect(share)
                if root.is_dir():
                    accessible = True
                    self._folder(root, category, pid, project_folder=project_folder).joinpath(name).unlink(missing_ok=True)
            except OSError as exc:
                problems.append(str(exc))
        # Do not remove legacy copy if all NAS routes are unavailable: it might
        # be the last usable copy and the remote delete cannot be verified.
        if not accessible:
            raise QnapUploadsUnavailable('QNAP unavailable during attachment delete: ' + '; '.join(problems))
        try:
            self._legacy_folder(category, pid).joinpath(name).unlink(missing_ok=True)
        except OSError as exc:
            raise QnapUploadsUnavailable(str(exc)) from exc

    def status(self):
        try:
            active = self.active_root()
            return {'available': True, 'share': str(self.share),
                    'root': str(active), 'primary_root': str(self.root),
                    'secondary_root': str(self.roots[1]) if len(self.roots) > 1 else None,
                    'active': 'primary' if active == self.roots[0] else 'secondary',
                    'folder_exists': active.is_dir(), 'write_tested': False}
        except QnapUploadsUnavailable as exc:
            return {'available': False, 'share': str(self.share),
                    'primary_root': str(self.root),
                    'secondary_root': str(self.roots[1]) if len(self.roots) > 1 else None,
                    'error': str(exc)}
