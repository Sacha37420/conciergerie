"""Téléchargement d'un flux iCal sans jamais joindre une destination interne.

L'URL iCal d'un appartement est saisie par le gestionnaire, puis appelée par
ce backend — depuis l'intérieur du réseau des conteneurs du lab. Sans contrôle,
elle permettait de faire appeler n'importe quel service interne (`storage`,
Keycloak, une base, l'hôte) et d'en lire une partie de la réponse par le
message d'erreur conservé dans `dernier_sync_erreur`.

Règle : `http`/`https` seulement ; le nom est résolu et CHAQUE adresse obtenue
doit être publique (ni boucle locale, ni adresse privée, ni lien-local, ni
multidiffusion — les noms de conteneurs du lab se résolvent en adresses
privées et tombent donc ici). Le contrôle est fait à l'enregistrement
(`AppartementSerializer`) ET à chaque appel, redirections comprises : elles
sont suivies à la main, une par une, jamais par `requests`.

Ré-association DNS (lot 4) : le nom n'est résolu qu'UNE fois par étape, pour
le contrôle, et la connexion se fait à l'adresse contrôlée — jamais à une
seconde résolution. Le nom reste celui de l'URL pour tout le reste : en-tête
`Host`, SNI, et vérification du certificat (faite contre le nom, pas contre
l'adresse). Une redirection est une nouvelle étape : résolue, contrôlée,
épinglée à son tour. `requests` ne lit ni mandataire (`HTTP(S)_PROXY`) ni
`.netrc` : un mandataire se résoudrait lui-même, hors contrôle.
"""
import ipaddress
import socket
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

TIMEOUT = 15                      # par opération réseau (connexion, chaque lecture)
# Un flux iCal Airbnb fait quelques dizaines de ko. Au-delà de 5 Mo, ou après
# 30 s de transfert, le flux est refusé : un lien qui sert un flux sans fin (ou
# goutte à goutte) épuisait la mémoire du backend (1 Go) ou le bloquait.
MAX_OCTETS = 5 * 1024 * 1024
DELAI_TOTAL = 30                  # secondes, pour une étape (lecture du corps comprise)
_MORCEAU = 64 * 1024
MAX_REDIRECTIONS = 5
_REDIRECTIONS = (301, 302, 303, 307, 308)
_PORTS = {'http': 80, 'https': 443}
# Message unique, sans l'adresse obtenue : le contrôle ne doit pas servir à
# sonder les noms du réseau interne.
_REFUS = "Destination refusée : l'adresse doit être un site public joignable en http(s)."


# Vérification du certificat : `True` = magasin de certificats du système.
# Les tests le remplacent par leur autorité locale, jamais par `False`.
VERIFIER_CERTIFICAT = True


class DestinationInterdite(ValueError):
    """L'URL ne désigne pas (ou plus) une destination publique en http(s)."""


class ReponseRefusee(ValueError):
    """Le serveur a répondu, mais pas par un flux utilisable (code d'erreur
    HTTP, flux trop volumineux, transfert trop long). Message sans l'URL."""


def _resoudre(hote: str, port: int) -> list[str]:
    """Adresses du nom, telles que la connexion les obtiendrait."""
    return [info[4][0] for info in socket.getaddrinfo(hote, port, type=socket.SOCK_STREAM)]


def _est_publique(adresse: str) -> bool:
    try:
        ip = ipaddress.ip_address(adresse.split('%', 1)[0])
    except ValueError:
        return False
    if ip.version == 6:
        # Adresse IPv4 transportée dans une IPv6 (mappée, 6to4, Teredo) :
        # c'est l'adresse IPv4 qui est jointe.
        incluses = [ip.ipv4_mapped, ip.sixtofour, ip.teredo[1] if ip.teredo else None]
        if any(v4 is not None and not _est_publique(str(v4)) for v4 in incluses):
            return False
    return ip.is_global and not ip.is_multicast


def verifier_url(url: str) -> list[str]:
    """Lève DestinationInterdite si `url` n'est pas une URL http(s) dont
    toutes les adresses sont publiques ; sinon rend ces adresses, celles
    auxquelles la connexion doit se faire (`telecharger`)."""
    try:
        parties = urlsplit(url or '')
        schema, hote, port = parties.scheme.lower(), parties.hostname, parties.port
    except ValueError as exc:
        raise DestinationInterdite(_REFUS) from exc
    if schema not in _PORTS or not hote:
        raise DestinationInterdite(_REFUS)
    try:
        adresses = _resoudre(hote, port or _PORTS[schema])
    except (OSError, UnicodeError) as exc:
        raise DestinationInterdite(_REFUS) from exc
    if not adresses or not all(_est_publique(a) for a in adresses):
        raise DestinationInterdite(_REFUS)
    return adresses


def _pool_epingle(base_pool, base_conn, adresse: str, sockets: list):
    """Classe de pool urllib3 dont les connexions ouvrent leur socket vers
    `adresse` au lieu de résoudre le nom. Seule la création du socket est
    touchée : le nom (`self.host`) reste celui de l'URL pour l'en-tête
    `Host`, le SNI et la vérification du certificat, faits après."""

    class Connexion(base_conn):
        def _new_conn(self):
            # `_dns_host` est ce qu'urllib3 passe à create_connection ; il est
            # rétabli avant le TLS, qui lit le nom par `self.host`.
            nom = self._dns_host
            self._dns_host = adresse
            try:
                sock = super()._new_conn()
            finally:
                self._dns_host = nom
            sockets.append(sock)        # pour la minuterie du délai total (_get)
            return sock

    return type(f'Epingle{base_pool.__name__}', (base_pool,), {'ConnectionCls': Connexion})


class _AdaptateurEpingle(HTTPAdapter):
    """Adaptateur `requests` lié à UNE adresse déjà contrôlée."""

    def __init__(self, adresse: str):
        self._adresse = adresse
        self.sockets = []
        super().__init__(max_retries=0)

    def init_poolmanager(self, *args, **kwargs):
        super().init_poolmanager(*args, **kwargs)
        self.poolmanager.pool_classes_by_scheme = {
            'http': _pool_epingle(HTTPConnectionPool, HTTPConnection, self._adresse, self.sockets),
            'https': _pool_epingle(HTTPSConnectionPool, HTTPSConnection, self._adresse, self.sockets),
        }


_TROP_LONG = 'Le serveur du calendrier met trop de temps à envoyer le flux.'
_TROP_GROS = 'Le flux du calendrier dépasse 5 Mo.'


def _lire_borne(resp: requests.Response) -> bytes:
    """Corps de `resp`, lu par morceaux : refusé dès qu'il dépasse
    MAX_OCTETS, annoncé par `Content-Length` (avant toute lecture) ou constaté
    en lisant. La taille comptée est celle du flux décompressé : une petite
    réponse gzip qui se décompresse en Go est coupée aussi."""
    annonce = resp.headers.get('Content-Length')
    if annonce is not None and annonce.strip().isdigit() and int(annonce) > MAX_OCTETS:
        raise ReponseRefusee(_TROP_GROS)
    morceaux, total = [], 0
    for morceau in resp.iter_content(chunk_size=_MORCEAU):
        total += len(morceau)
        if total > MAX_OCTETS:
            raise ReponseRefusee(_TROP_GROS)
        morceaux.append(morceau)
    return b''.join(morceaux)


def _get(url: str, adresse: str) -> requests.Response:
    """GET de `url` en se connectant à `adresse` (déjà contrôlée), sans suivre
    de redirection. Seul point de ce module qui ouvre une connexion. Le corps
    n'est lu que pour une réponse 2xx, et borné (`_lire_borne`) ; il est posé
    dans `resp.content` avant la fermeture de la session.

    Délai total (DELAI_TOTAL) : `TIMEOUT` ne borne qu'une lecture, et un
    serveur qui envoie un octet toutes les 10 s (en-têtes ou corps) ne le
    déclenche jamais. Une minuterie coupe alors le socket (`shutdown`, sûr
    depuis un autre fil) : la lecture bloquée rend la main aussitôt."""
    trop_long = threading.Event()
    with requests.Session() as session:
        session.trust_env = False          # ni mandataire, ni .netrc, ni CA de l'environnement
        adaptateur = _AdaptateurEpingle(adresse)
        session.mount('http://', adaptateur)
        session.mount('https://', adaptateur)

        def couper():
            trop_long.set()
            for sock in list(adaptateur.sockets):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass                   # déjà fermé

        minuterie = threading.Timer(DELAI_TOTAL, couper)
        minuterie.daemon = True
        minuterie.start()
        try:
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=False,
                               verify=VERIFIER_CERTIFICAT, stream=True)
            try:
                resp._content = _lire_borne(resp) if 200 <= resp.status_code < 300 else b''
            finally:
                resp.close()
        except (requests.RequestException, OSError):
            if trop_long.is_set():
                raise ReponseRefusee(_TROP_LONG) from None
            raise
        finally:
            minuterie.cancel()
        if trop_long.is_set():             # coupé : ce qui a été lu est tronqué
            raise ReponseRefusee(_TROP_LONG)
        return resp


def telecharger(url: str) -> bytes:
    """Contenu de `url`. Chaque étape (URL de départ, puis chaque redirection)
    est contrôlée, puis jointe à l'adresse contrôlée et à elle seule."""
    for _ in range(MAX_REDIRECTIONS + 1):
        adresses = verifier_url(url)
        resp = _get(url, adresse=adresses[0])
        if resp.status_code in _REDIRECTIONS:
            cible = resp.headers.get('Location')
            if not cible:
                raise DestinationInterdite('Redirection sans destination.')
            url = urljoin(url, cible)
            continue
        if resp.status_code >= 400:
            raise ReponseRefusee(f'Le serveur du calendrier a répondu {resp.status_code}.')
        return resp.content
    raise DestinationInterdite('Trop de redirections.')


def message_erreur(exc: Exception) -> str:
    """Message d'échec à conserver (`dernier_sync_erreur`) et à journaliser :
    JAMAIS l'URL. Les erreurs de `requests`/urllib3 la recopient (« for url:
    https://…?s=<secret> », « Max retries exceeded with url: … ») — or l'URL
    iCal est un lien secret réservé au gestionnaire."""
    if isinstance(exc, (DestinationInterdite, ReponseRefusee)):
        return str(exc)
    if isinstance(exc, requests.exceptions.SSLError):
        return 'Certificat du serveur du calendrier refusé.'
    if isinstance(exc, requests.Timeout):
        return 'Le serveur du calendrier ne répond pas (délai dépassé).'
    if isinstance(exc, requests.ConnectionError):
        return 'Connexion au serveur du calendrier impossible.'
    if isinstance(exc, requests.RequestException):
        return 'Échec de la requête vers le serveur du calendrier.'
    return "Le flux reçu n'est pas un calendrier iCal lisible."
