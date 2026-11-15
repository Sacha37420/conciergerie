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
- ``ensure_share`` : seule fonction de création d'un partage de bien, rangé
  sous le partage d'app ``conciergerie`` (chantier « storage-hierarchie »,
  voir plus bas) — appelée avant tout dépôt et tout ajout de membre.
- ``add_member``/``remove_member`` : synchronisent les
  vrais ``ShareMember`` du partage du bien avec ``PartProprietaire`` (cf.
  ``api/signals.py``) — c'est ce qui rend possible un accès **direct**
  frontend → storage en lecture pour un co-propriétaire (``FraisSerializer.
  get_facture``), storage devenant alors le seul rempart pour ce chemin.
"""
from __future__ import annotations

import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

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
    _ensure_share_best_effort(bien_id)
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


# ── Hiérarchie : conteneur de l'app et rattachement des partages de bien ────
#
# Chantier « storage-hierarchie » (contrat v2, I1/I3) : chaque partage de bien
# est rangé sous le partage d'app ``conciergerie`` (le « conteneur »), qui
# appartient au même compte de service — storage exige que parent et enfant
# aient le même propriétaire. Organisation pure (storage ne fait hériter aucun
# droit d'un parent) : la hiérarchie ne doit JAMAIS faire échouer un dépôt de
# facture ni un ajout de co-propriétaire. Conteneur indisponible ou parent
# refusé ⇒ partage créé à la racine, avertissement à message fixe ; la
# commande ``rattacher_partages_storage`` le rangera.
#
# Avant ce chantier, le partage naissait implicitement au premier appel du
# compte de service sur le préfixe (``GET /api/files/<ns>/``, cf.
# ``resolve_namespace`` côté storage) — donc toujours à la racine. Il est
# maintenant créé explicitement par ``POST /api/shares/``.

CONTAINER = 'conciergerie'

# Conteneur prêt (il nous appartient) — mémorisé par processus. Un échec n'est
# pas mémorisé : la prochaine création réessaie.
_container_ready = False

_MSG_SANS_PARENT = (
    "storage : partage de bien créé sans rattachement au conteneur de l'app "
    "(conteneur indisponible ou parent refusé) — à ranger par rattacher_partages_storage"
)
_MSG_PARTAGE_NON_PREPARE = (
    "storage : partage du bien non préparé avant le dépôt — le dépôt est tenté quand même"
)


def _shares_url(suffix: str = '') -> str:
    return f'{settings.STORAGE_INTERNAL_URL}/api/shares/{suffix}'


def _share_is_ours(name: str) -> bool:
    """Lecture seule : le partage existe-t-il et nous appartient-il ?
    (``GET /api/shares/<nom>/members/`` : 200, sinon 404/403)."""
    try:
        resp = requests.get(_shares_url(f'{name}/members/'), headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc
    return resp.status_code == 200


def container_is_ours() -> bool:
    try:
        return _share_is_ours(CONTAINER)
    except StorageClientError:
        return False


def ensure_container() -> bool:
    """S'assure que le conteneur existe et nous appartient (I1, étape 1).
    Rend False s'il est indisponible (nom pris par un autre propriétaire,
    storage en erreur) — ne lève jamais."""
    global _container_ready
    if _container_ready:
        return True
    if container_is_ours():
        _container_ready = True
        return True
    try:
        resp = requests.post(
            _shares_url(), headers=_headers(), data={'name': CONTAINER}, timeout=_TIMEOUT,
        )
    except (requests.RequestException, StorageClientError):
        return False
    if resp.status_code == 201:
        _container_ready = True
        return True
    return False


def _error_keys(resp) -> set:
    if resp.status_code != 400:
        return set()
    try:
        body = resp.json()
    except ValueError:
        return set()
    return set(body) if isinstance(body, dict) else set()


def _post_share(data: dict):
    try:
        return requests.post(_shares_url(), headers=_headers(), data=data, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageClientError(f'storage injoignable : {exc}') from exc


def ensure_share(bien_id: int) -> None:
    """Crée le partage storage du bien s'il n'existe pas encore (idempotent),
    rattaché au conteneur (I1). Nécessaire avant un premier ``add_member`` sur
    un bien qui n'a encore aucune facture (``ShareMembersView`` répond 404 sur
    un partage absent), et appelé avant chaque dépôt."""
    namespace = _namespace(bien_id)
    if _share_is_ours(namespace):
        return  # existe déjà et nous appartient : rien à faire (I3 le range).

    if ensure_container():
        resp = _post_share({'name': namespace, 'parent': CONTAINER})
        if 'parent' in _error_keys(resp):
            logger.warning(_MSG_SANS_PARENT)
            resp = _post_share({'name': namespace})
    else:
        logger.warning(_MSG_SANS_PARENT)
        resp = _post_share({'name': namespace})
    if resp.status_code == 201 or 'name' in _error_keys(resp):
        return  # créé, ou 400 `name` : il existe déjà (course) — succès.
    if _share_is_ours(namespace):
        return
    raise StorageClientError(f'Création du partage storage échouée (HTTP {resp.status_code}).')


def _ensure_share_best_effort(bien_id: int) -> None:
    """Avant un dépôt : préparer le partage ne doit jamais faire échouer le
    dépôt lui-même (storage crée encore le partage au premier dépôt, préfixe
    du compte de service)."""
    try:
        ensure_share(bien_id)
    except StorageClientError:
        logger.warning(_MSG_PARTAGE_NON_PREPARE)


def list_owned_shares() -> dict[str, str | None]:
    """``{nom: parent}`` de chaque partage du compte de service
    (``GET /api/shares/``, clé ``owned``) — lecture de l'état pour I3."""
    try:
        resp = requests.get(_shares_url(), headers=_headers(), timeout=_TIMEOUT)
    except requests.RequestException as exc:
        raise StorageClientError('storage injoignable (liste des partages).') from exc
    if resp.status_code != 200:
        raise StorageClientError(f'Liste des partages storage échouée (HTTP {resp.status_code}).')
    return {s['name']: s.get('parent') for s in resp.json().get('owned', [])}


def set_parent(namespace: str, parent: str) -> int:
    """``PATCH /api/shares/<namespace>/ {parent}`` — rend le code HTTP, sans
    l'interpréter (I3 décide : 200 fait, 404 ignoré, 400/403 échec)."""
    try:
        resp = requests.patch(
            _shares_url(f'{namespace}/'), headers=_headers(), data={'parent': parent},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise StorageClientError('storage injoignable (rattachement).') from exc
    return resp.status_code


def namespace_for_bien(bien_id: int) -> str:
    return _namespace(bien_id)


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
