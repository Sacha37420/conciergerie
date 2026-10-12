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

Limite connue : le nom est résolu une fois pour le contrôle, puis une seconde
fois par `requests` pour la connexion. Un DNS qui change de réponse entre les
deux n'est pas couvert (il faudrait épingler l'adresse contrôlée).
"""
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import requests

TIMEOUT = 15
MAX_REDIRECTIONS = 5
_REDIRECTIONS = (301, 302, 303, 307, 308)
_PORTS = {'http': 80, 'https': 443}
# Message unique, sans l'adresse obtenue : le contrôle ne doit pas servir à
# sonder les noms du réseau interne.
_REFUS = "Destination refusée : l'adresse doit être un site public joignable en http(s)."


class DestinationInterdite(ValueError):
    """L'URL ne désigne pas (ou plus) une destination publique en http(s)."""


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


def verifier_url(url: str) -> None:
    """Lève DestinationInterdite si `url` n'est pas une URL http(s) dont
    toutes les adresses sont publiques."""
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


def telecharger(url: str) -> bytes:
    """Contenu de `url`. Chaque étape (URL de départ, puis chaque redirection)
    est contrôlée avant d'être appelée."""
    for _ in range(MAX_REDIRECTIONS + 1):
        verifier_url(url)
        resp = requests.get(url, timeout=TIMEOUT, allow_redirects=False)
        if resp.status_code in _REDIRECTIONS:
            cible = resp.headers.get('Location')
            if not cible:
                raise DestinationInterdite('Redirection sans destination.')
            url = urljoin(url, cible)
            continue
        resp.raise_for_status()
        return resp.content
    raise DestinationInterdite('Trop de redirections.')
