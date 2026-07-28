"""Client minimal de l'API fichiers de l'app storage.

Le backend agit via le **service account** du client confidentiel compagnon
``conciergerie-admin`` (flux ``client_credentials``) — même mécanique de
token que ``lab-admin``/``restauration`` pour l'API Admin Keycloak (porté de
``lab-admin/backend/api/keycloak_admin.py``), mais appelée contre l'API
``storage`` au lieu de l'API Admin Keycloak.

L'autorisation fine (qui peut lire/écrire quelle facture) est décidée par
``FraisViewSet`` *avant* d'appeler ce module : storage fait confiance au
compte de service sur tout partage préfixé ``conciergerie-bien-`` (cf.
``KEYCLOAK_SERVICE_WRITE_PREFIXES`` côté storage), sans notion de
co-propriétaire — ce module reste un simple transport.
"""
from __future__ import annotations

import time

import requests
from django.conf import settings

_TIMEOUT = 15


class StorageClientError(RuntimeError):
    """Erreur de communication ou de configuration avec l'API storage."""


def _split_issuer() -> tuple[str, str]:
    issuer = (settings.KEYCLOAK_ISSUER_URI or '').rstrip('/')
    if '/realms/' not in issuer:
        raise StorageClientError(
            f"KEYCLOAK_ISSUER_URI invalide : {issuer!r} (attendu …/realms/<realm>)."
        )
    base, realm = issuer.split('/realms/', 1)
    return base, realm.split('/', 1)[0]


# Cache de token au niveau process (chaque worker gunicorn a le sien).
_token_cache: dict[str, float | str] = {'value': '', 'exp': 0.0}


def _token() -> str:
    now = time.time()
    if _token_cache['value'] and float(_token_cache['exp']) > now + 5:
        return str(_token_cache['value'])

    client_id = getattr(settings, 'KEYCLOAK_ADMIN_CLIENT_ID', '') or ''
    secret = getattr(settings, 'KEYCLOAK_ADMIN_CLIENT_SECRET', '') or ''
    if not client_id or not secret:
        raise StorageClientError(
            "KEYCLOAK_ADMIN_CLIENT_ID / KEYCLOAK_ADMIN_CLIENT_SECRET manquants. "
            "Lancez setup2.sh conciergerie (provisionne le client conciergerie-admin)."
        )

    base, realm = _split_issuer()
    try:
        resp = requests.post(
            f'{base}/realms/{realm}/protocol/openid-connect/token',
            data={
                'grant_type': 'client_credentials',
                'client_id': client_id,
                'client_secret': secret,
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'Keycloak injoignable : {exc}') from exc
    if resp.status_code != 200:
        raise StorageClientError(
            f"Auth service account échouée (HTTP {resp.status_code}) : {resp.text[:200]}"
        )
    data = resp.json()
    _token_cache['value'] = data['access_token']
    _token_cache['exp'] = now + int(data.get('expires_in', 60))
    return str(_token_cache['value'])


def _namespace(bien_id: int) -> str:
    return f'{settings.STORAGE_NAMESPACE_PREFIX}{bien_id}'


def _headers() -> dict[str, str]:
    return {'Authorization': f'Bearer {_token()}'}


def upload(bien_id: int, relative_path: str, fileobj, filename: str, content_type: str = '') -> dict:
    """Dépose `fileobj` dans le partage du bien, sous `relative_path`. Retourne
    le JSON de StoredFileSerializer (voir storage/backend/api/views.py)."""
    url = f"{settings.STORAGE_INTERNAL_URL}/api/files/{_namespace(bien_id)}/"
    files = {'file': (filename, fileobj, content_type or 'application/octet-stream')}
    data = {'relative_path': relative_path}
    try:
        resp = requests.post(url, headers=_headers(), data=data, files=files, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code != 201:
        raise StorageClientError(f'Upload storage échoué (HTTP {resp.status_code}) : {resp.text[:200]}')
    return resp.json()


def download(bien_id: int, relative_path: str) -> requests.Response:
    """Retourne la réponse streamée (`stream=True`) — à l'appelant de la
    consommer et de la fermer (cf. FraisViewSet.facture, StreamingHttpResponse)."""
    url = f"{settings.STORAGE_INTERNAL_URL}/api/files/{_namespace(bien_id)}/content/{relative_path}"
    try:
        resp = requests.get(url, headers=_headers(), stream=True, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code != 200:
        raise StorageClientError(f'Téléchargement storage échoué (HTTP {resp.status_code}).')
    return resp


def delete(bien_id: int, relative_path: str) -> None:
    url = f"{settings.STORAGE_INTERNAL_URL}/api/files/{_namespace(bien_id)}/content/{relative_path}"
    try:
        resp = requests.delete(url, headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code not in (204, 404):
        raise StorageClientError(f'Suppression storage échouée (HTTP {resp.status_code}).')
