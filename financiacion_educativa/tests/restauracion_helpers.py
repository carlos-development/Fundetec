"""Destructive commands are restricted to labelled, localhost-only test containers."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile


def validar_contenedores(origen, destino, conexion):
    def puerto(datos):
        if datos.get('Config', {}).get('Labels', {}).get('fundetec.purpose') != 'restore-test':
            raise ValueError('Contenedor no autorizado para ensayo sintetico.')
        if not datos.get('State', {}).get('Running') or any(m['Type'] != 'tmpfs' for m in datos.get('Mounts', [])):
            raise ValueError('El ensayo requiere contenedores activos y almacenamiento efimero sin volumenes de host.')
        puertos = datos.get('NetworkSettings', {}).get('Ports', {}).get('5432/tcp') or []
        if len(puertos) != 1 or puertos[0]['HostIp'] != '127.0.0.1':
            raise ValueError('PostgreSQL debe estar publicado exclusivamente en localhost.')
        return int(puertos[0]['HostPort'])

    p_origen, p_destino = puerto(origen), puerto(destino)
    if origen['Id'] == destino['Id'] or p_origen == p_destino:
        raise ValueError('Origen y destino deben ser contenedores distintos.')
    if conexion['HOST'] != '127.0.0.1' or int(conexion['PORT']) != p_origen or not conexion['NAME'].startswith('test_'):
        raise ValueError('Solo se respalda la base test_ del runner local, nunca una base compartida.')
    return p_destino


def docker(*argumentos):
    resultado = subprocess.run(['docker', *argumentos], capture_output=True, timeout=120, check=False)
    if resultado.returncode:
        # stderr can contain SQL, paths or environment. Do not echo it into CI.
        raise RuntimeError('Fallo de Docker en el ensayo aislado; revisar permisos y disponibilidad local.')
    return resultado.stdout


def inspeccionar(nombre):
    return json.loads(docker('inspect', nombre))[0]


def manifiesto(raiz):
    raiz = Path(raiz)
    resultado = {}
    for ruta in sorted(raiz.rglob('*')):
        if ruta.is_symlink():
            raise ValueError('No se admiten enlaces simbolicos en el ensayo.')
        if ruta.is_file():
            resultado[ruta.relative_to(raiz).as_posix()] = hashlib.sha256(ruta.read_bytes()).hexdigest()
    return resultado


def respaldar_privados(origen, archivo):
    previo = manifiesto(origen)
    with tarfile.open(archivo, 'w:gz') as salida:
        salida.add(origen, arcname='private', recursive=True)
    return previo


def restaurar_privados(archivo, destino):
    destino = Path(destino).resolve()
    if destino.exists():
        raise ValueError('El destino privado debe ser nuevo.')
    with tarfile.open(archivo, 'r:gz') as entrada:
        for miembro in entrada.getmembers():
            ruta = (destino / miembro.name).resolve()
            if (not ruta.is_relative_to(destino) or not (miembro.name == 'private' or miembro.name.startswith('private/'))
                    or not (miembro.isfile() or miembro.isdir())):
                raise ValueError('Archivo privado con entrada insegura.')
        destino.mkdir()
        entrada.extractall(destino, filter='data')
    return destino / 'private'
