"""Create an encrypted PostgreSQL and private-files backup during a quiet window."""

import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory


class BackupError(Exception):
    pass


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(*args, env=None):
    result = subprocess.run(args, env=env, capture_output=True, text=True)
    if result.returncode:
        # libpq/GnuPG errors can contain credentials, hosts or private paths.
        raise BackupError(f'{args[0]} fallo con codigo {result.returncode}.')
    return result.stdout.strip()


def quiet_window(units):
    snapshot = {}
    for unit in units:
        properties = dict(line.split('=', 1) for line in run(
            'systemctl', 'show', '--property=LoadState,ActiveState,InactiveEnterTimestampMonotonic,ActiveEnterTimestampMonotonic',
            '--', unit,
        ).splitlines())
        if properties.get('LoadState') != 'loaded' or properties.get('ActiveState') != 'inactive':
            raise BackupError(f'La unidad escritora {unit} debe estar cargada e inactiva.')
        snapshot[unit] = properties
    return snapshot


def private_entries(root):
    def unreadable(error):
        raise error

    yield root
    for folder, dirs, files in os.walk(root, onerror=unreadable):
        for name in sorted(dirs + files):
            path = Path(folder)/name
            mode = path.lstat().st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise BackupError('La raiz privada contiene enlaces o entradas especiales.')
            yield path


def create_backup():
    os.umask(0o077)
    environment = os.environ['BACKUP_ENVIRONMENT']
    if environment not in ('staging', 'production'):
        raise BackupError('Entorno de respaldo invalido.')
    units = os.environ['BACKUP_WRITER_UNITS'].split()
    if not units or any(not unit.startswith(f'fundetec-{environment}') for unit in units):
        raise BackupError('Configure las unidades escritoras del entorno.')
    root = Path(os.environ['BACKUP_PRIVATE_ROOT'])
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise BackupError('La raiz privada debe ser un directorio absoluto sin enlaces.')
    root = root.resolve()
    state = Path(os.environ['STATE_DIRECTORY']).resolve(strict=True)
    if state.is_relative_to(root) or root.is_relative_to(state):
        raise BackupError('La raiz privada y el directorio de respaldo deben estar separados.')
    credentials = Path(os.environ['CREDENTIALS_DIRECTORY'])
    recipient = os.environ['BACKUP_GPG_FINGERPRINT']
    if not re.fullmatch(r'[A-Fa-f0-9]{40}|[A-Fa-f0-9]{64}', recipient):
        raise BackupError('Configure la huella completa de la clave publica.')
    service = os.environ['BACKUP_PG_SERVICE']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', service):
        raise BackupError('Nombre de servicio PostgreSQL invalido.')
    pg = dict(PATH=os.environ['PATH'], PGSERVICE=service, PGSERVICEFILE=str(credentials/'pg_service.conf'),
              PGPASSFILE=str(credentials/'pgpass'), PGCONNECT_TIMEOUT='10')
    lock = state/'backup.lock'
    with lock.open('a') as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return package(state, root, units, pg, environment, credentials/'recipient.asc', recipient)


def package(state, private, units, pg, environment, public_key, recipient):
    before = quiet_window(units)
    local, version = run(
        'psql', '-XAt', '-w', '-F', '|', '-c',
        "SELECT inet_server_addr() IS NULL, current_setting('server_version_num')",
        env=pg,
    ).split('|')
    dump_version = run('pg_dump', '--version', env=pg)
    if local != 't' or int(version) // 10000 != int(re.search(r'\b(\d+)\.', dump_version).group(1)):
        raise BackupError('Se requiere socket local y pg_dump de la misma version mayor.')
    release = run('git', '--no-optional-locks', '-c', f'safe.directory={Path.cwd()}', 'rev-parse', 'HEAD')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    name = f'fundetec-{environment}-{stamp}'
    with TemporaryDirectory(prefix='fundetec-plaintext-') as directory, \
            TemporaryDirectory(prefix='.cifrado-', dir=state) as pending:
        work = Path(directory)
        dump = work/'database.dump'
        run('pg_dump', '-w', '--format=custom', '--no-owner', '--no-acl', '--file', str(dump), env=pg)
        run('pg_restore', '--exit-on-error', '--file=/dev/null', str(dump), env=pg)
        metadata = work/'metadata.json'
        metadata.write_text(json.dumps(dict(environment=environment, release=release,
            postgres_server_version_num=int(version), created_utc=stamp)), encoding='utf8')
        manifest = {'database.dump': sha256(dump), 'metadata.json': sha256(metadata)}
        archive = work/'backup.tar.gz'
        with tarfile.open(archive, 'w:gz', dereference=True) as target:
            for file in (dump, metadata):
                target.add(file, arcname=file.name)
            for file in private_entries(private):
                relative = (Path('private')/file.relative_to(private)).as_posix()
                if file.is_file():
                    manifest[relative] = sha256(file)
                target.add(file, arcname=relative, recursive=False)
            manifest_path = work/'SHA256SUMS.json'
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding='utf8')
            target.add(manifest_path, arcname=manifest_path.name)
        with tarfile.open(archive, 'r:gz') as source:
            for member in source:
                if member.name in manifest:
                    with source.extractfile(member) as stream:
                        if hashlib.file_digest(stream, 'sha256').hexdigest() != manifest[member.name]:
                            raise BackupError('Los bytes archivados no coinciden con el manifiesto.')
        if before != quiet_window(units):
            raise BackupError('La ventana de respaldo cambio durante la captura.')

        gnupg = work/'gnupg'
        gnupg.mkdir(mode=0o700)
        gpg = ['gpg', '--batch', '--no-tty', '--no-autostart', '--homedir', str(gnupg)]
        run(*gpg, '--import', str(public_key))
        publish = Path(pending)/name
        publish.mkdir(mode=0o700)
        encrypted = publish/(name+'.tar.gz.gpg')
        run(*gpg, '--trust-model', 'always', '--recipient', recipient,
            '--output', str(encrypted), '--encrypt', str(archive))
        digest = sha256(encrypted)
        encrypted.with_name(encrypted.name+'.sha256').write_text(
            f'{digest}  {encrypted.name}\n', encoding='ascii')
        publish.rename(state/name)
    return dict(bundle=name, sha256=digest, private_files=len(manifest)-2)


if __name__ == '__main__':
    try:
        print(json.dumps(create_backup()))
    except (BackupError, KeyError, OSError, ValueError) as error:
        message = str(error) if isinstance(error, BackupError) else type(error).__name__
        print(f'Respaldo no publicado. {message}', file=sys.stderr)
        sys.exit(1)
