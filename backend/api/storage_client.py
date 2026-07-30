"""Client minimal de l'API fichiers + partages de l'app storage.

Le backend agit via le **service account** du client confidentiel compagnon
``conciergerie-admin`` (flux ``client_credentials``) — même mécanique de
token que ``lab-admin``/``restauration`` pour l'API Admin Keycloak (porté de
``lab-admin/backend/api/keycloak_admin.py``), mais appelée contre l'API
``storage`` au lieu de l'API Admin Keycloak.

Deux familles de fonctions :

- ``upload``/``download``/``delete`` : contenu des factures. L'autorisation
  fine (qui peut lire/écrire quelle facture) reste décidée par
  ``FraisViewSet`` *avant* d'appeler ce module : storage fait confiance au
  compte de service sur tout partage préfixé ``conciergerie-bien-`` (cf.
  ``KEYCLOAK_SERVICE_WRITE_PREFIXES`` côté storage) — ce module reste un
  simple transport pour ce chemin, resté proxié par le backend.
- ``add_member``/``remove_member`` (+ ``ensure_share``) : synchronisent les
  vrais ``ShareMember`` du partage du bien avec ``PartProprietaire`` (cf.
  ``api/signals.py``) — c'est ce qui rend possible un accès **direct**
  frontend → storage en lecture pour un co-propriétaire (``FraisSerializer.
  get_facture``), storage devenant alors le seul rempart pour ce chemin.
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


def ensure_share(bien_id: int) -> None:
    """Force la création du partage storage du bien s'il n'existe pas encore.

    ``resolve_namespace`` (storage) crée le ``Share`` au premier appel du
    compte de service sur un namespace préfixé ``conciergerie-bien-`` — y
    compris pour un simple ``GET`` de liste, pas seulement un upload (cf.
    ``FileListUploadView.get`` côté storage). Nécessaire avant un premier
    ``add_member``/``remove_member`` sur un bien qui n'a encore aucune facture
    (sinon ``ShareMembersView`` répond 404, le partage n'existant pas)."""
    url = f"{settings.STORAGE_INTERNAL_URL}/api/files/{_namespace(bien_id)}/"
    try:
        resp = requests.get(url, headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code != 200:
        raise StorageClientError(f'Création du partage storage échouée (HTTP {resp.status_code}).')


def add_member(bien_id: int, email: str, role: str = 'read') -> None:
    """Ajoute (ou met à jour le rôle de) ``email`` comme ``ShareMember`` du
    partage du bien. Le partage a été créé par ce même compte de service
    (``KEYCLOAK_SERVICE_WRITE_PREFIXES``), qui en est donc ``owner_username``
    au sens de storage — ``get_owned_share`` l'autorise à gérer les membres."""
    ensure_share(bien_id)
    url = f"{settings.STORAGE_INTERNAL_URL}/api/shares/{_namespace(bien_id)}/members/"
    try:
        resp = requests.post(
            url, headers=_headers(), data={'member_email': email, 'role': role}, timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code != 201:
        raise StorageClientError(
            f"Ajout du membre '{email}' échoué (HTTP {resp.status_code}) : {resp.text[:200]}"
        )


def remove_member(bien_id: int, email: str) -> None:
    """Retire ``email`` des membres du partage du bien. Idempotent (404 =
    déjà absent, traité comme un succès — même convention que ``delete()``)."""
    url = f"{settings.STORAGE_INTERNAL_URL}/api/shares/{_namespace(bien_id)}/members/{email}/"
    try:
        resp = requests.delete(url, headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    if resp.status_code not in (204, 404):
        raise StorageClientError(f"Retrait du membre '{email}' échoué (HTTP {resp.status_code}).")
