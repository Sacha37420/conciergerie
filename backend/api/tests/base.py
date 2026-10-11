import time
from decimal import Decimal
from unittest import mock

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api import storage_client
from api.authentication import KeycloakJWTAuthentication
from api.models import Appartement, Bien, PartProprietaire, Proprietaire

CLIENT_ID = 'conciergerie'
RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
AUTRE_RSA = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ABSENT = object()

REGLAGES = dict(
    KEYCLOAK_CLIENT_ID=CLIENT_ID,
    KEYCLOAK_REQUIRED_GROUPS='proprietaires,admins',
    KEYCLOAK_ISSUER_URI='http://keycloak.test/realms/ssolab',
    KEYCLOAK_ADMIN_CLIENT_ID='conciergerie-admin',
    KEYCLOAK_ADMIN_CLIENT_SECRET='secret-de-test',
    STORAGE_INTERNAL_URL='http://storage.test',
    STORAGE_NAMESPACE_PREFIX='conciergerie-bien-',
    VALORISATION_HEURE_PROPRIETAIRE=Decimal('6.00'),
    FORCE_SCRIPT_NAME='/conciergerie-api',     # comme en service, derrière Caddy
)


class JwksLocal:
    """Remplace le TÉLÉCHARGEMENT du JWKS par une clé locale. Signature,
    expiration, azp, groupes et email sont vérifiés par le vrai code."""

    class _Cle:
        key = RSA.public_key()

    def get_signing_key_from_jwt(self, token):
        return self._Cle()


def jeton(uid='alice', groups=('proprietaires',), azp=CLIENT_ID, email=ABSENT,
          exp=300, cle=RSA, algorithm='RS256') -> str:
    claims = {'preferred_username': uid, 'azp': azp, 'exp': int(time.time()) + exp}
    if groups is not None:
        claims['groups'] = list(groups)
    if email is ABSENT:
        email = f'{uid}@ssolab.local'
    if email is not None:
        claims['email'] = email
    return jwt.encode(claims, cle, algorithm=algorithm)


def _reseau_interdit(*args, **kwargs):
    raise AssertionError(f'appel réseau non simulé : {args[1:3]}')


@override_settings(**REGLAGES)
class Base(TestCase):
    """Jetons réels, réseau fermé, `storage` simulé (self.storage.*)."""

    def setUp(self):
        self._patch(mock.patch.object(KeycloakJWTAuthentication, '_get_jwks_client',
                                      classmethod(lambda cls: JwksLocal())))
        self._patch(mock.patch('requests.sessions.Session.request', _reseau_interdit))
        self.storage = mock.Mock()
        self.storage.upload.side_effect = lambda bien_id, chemin, *a, **k: {'relative_path': chemin}
        for nom in ('add_member', 'remove_member', 'upload', 'download', 'delete'):
            self._patch(mock.patch.object(storage_client, nom, getattr(self.storage, nom)))

    def _patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def avec(self, tok: str) -> APIClient:
        c = APIClient()
        c.credentials(HTTP_AUTHORIZATION=f'Bearer {tok}')
        return c

    def client_de(self, uid, groups=('proprietaires',), **kw) -> APIClient:
        return self.avec(jeton(uid, groups=groups, **kw))

    def monde(self):
        """Deux biens : « Maison » (Alice et Bob), « Chalet » (Chloé)."""
        self.gina = self.client_de('gina', groups=['admins'])      # gestionnaire
        self.alice = self.client_de('alice')
        self.bob = self.client_de('bob')
        self.chloe = self.client_de('chloe')
        self.p_alice = Proprietaire.objects.create(nom='Alice', email='alice@ssolab.local')
        self.p_bob = Proprietaire.objects.create(nom='Bob', email='bob@ssolab.local')
        self.p_chloe = Proprietaire.objects.create(nom='Chloé', email='chloe@ssolab.local')
        self.maison = Bien.objects.create(nom='Maison', commission_gestion_pct=Decimal('10'),
                                          commission_gestion_fixe=Decimal('5'))
        self.chalet = Bien.objects.create(nom='Chalet')
        for bien, p in ((self.maison, self.p_alice), (self.maison, self.p_bob), (self.chalet, self.p_chloe)):
            PartProprietaire.objects.create(bien=bien, proprietaire=p)
        self.appt_maison = Appartement.objects.create(bien=self.maison, nom='RDC')
        self.appt_chalet = Appartement.objects.create(bien=self.chalet, nom='Étage')
        self.storage.reset_mock()
