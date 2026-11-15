"""Chantier « storage-hierarchie » (contrat v2) : partages de bien rangés sous
le conteneur ``conciergerie`` (I1) et commande de rattachement de l'existant
(I3), contre un storage simulé au niveau HTTP (``requests`` du client)."""
import io
import re
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import requests
from django.core.management import call_command
from django.test import TestCase, override_settings

from api import storage_client
from api.models import Bien

from .base import REGLAGES, _reseau_interdit

C = storage_client.CONTAINER
URL = REGLAGES['STORAGE_INTERNAL_URL']


class _Rep:
    def __init__(self, status, corps=None):
        self.status_code = status
        self._corps = corps
        self.text = '' if corps is None else str(corps)

    def json(self):
        if self._corps is None:
            raise ValueError('pas de JSON')
        return self._corps


class FauxStorage:
    """Partages de storage et point de jeton de Keycloak, en mémoire.
    `membres` : {partage: {email: rôle}} (nos partages) ; `parents` ;
    `etrangers` : partages d'un autre propriétaire."""

    def __init__(self):
        self.membres: dict[str, dict[str, str]] = {}
        self.parents: dict[str, str | None] = {}
        self.etrangers: set[str] = set()
        self.fichiers: dict[tuple[str, str], bytes] = {}
        self.refuser_parent = False
        self.arrete = False
        self.appels: list[tuple[str, str]] = []

    def servir(self, verbe, url, headers=None, data=None, files=None, **_):
        if url.endswith('/protocol/openid-connect/token'):
            return _Rep(200, {'access_token': 'jeton-de-service', 'expires_in': 300})
        self.appels.append((verbe, url))
        if self.arrete:
            raise requests.ConnectionError('storage arrêté')
        assert headers == {'Authorization': 'Bearer jeton-de-service'}, headers
        chemin = url[len(URL):]
        if chemin == '/api/shares/' and verbe == 'GET':
            return _Rep(200, {'owned': [{'name': n, 'parent': self.parents.get(n)} for n in sorted(self.membres)],
                              'member_of': [], 'tree': []})
        if chemin == '/api/shares/' and verbe == 'POST':
            nom, parent = data['name'], data.get('parent') or None
            if nom in self.membres or nom in self.etrangers:
                return _Rep(400, {'name': ['Ce nom de partage est déjà pris.']})
            if parent is not None and (self.refuser_parent or parent not in self.membres):
                return _Rep(400, {'parent': ['refusé']})
            self.membres[nom], self.parents[nom] = {}, parent
            return _Rep(201, {'name': nom, 'parent': parent})
        if m := re.fullmatch(r'/api/shares/([^/]+)/', chemin):
            ns, parent = m.group(1), (data or {}).get('parent') or None
            assert verbe == 'PATCH', verbe
            if ns in self.etrangers:
                return _Rep(403)
            if ns not in self.membres:
                return _Rep(404)
            if parent is not None and (self.refuser_parent or parent not in self.membres):
                return _Rep(400, {'parent': ['refusé']})
            self.parents[ns] = parent
            return _Rep(200, {'name': ns, 'parent': parent})
        if m := re.fullmatch(r'/api/shares/([^/]+)/members/', chemin):
            ns = m.group(1)
            if ns in self.etrangers:
                return _Rep(403)
            if ns not in self.membres:
                return _Rep(404)
            if verbe == 'GET':
                return _Rep(200, [{'member_email': e, 'role': r} for e, r in self.membres[ns].items()])
            self.membres[ns][data['member_email']] = data['role']
            return _Rep(201, {})
        if m := re.fullmatch(r'/api/files/([^/]+)/', chemin):
            ns = m.group(1)                 # préfixe du compte de service : créé au besoin
            if ns not in self.membres:
                self.membres[ns], self.parents[ns] = {}, None
            self.fichiers[(ns, data['relative_path'])] = files['file'][1]
            return _Rep(201, {'relative_path': data['relative_path']})
        raise AssertionError(f'route storage non simulée : {verbe} {chemin}')

    def patches(self):
        return [a for a in self.appels if a[0] == 'PATCH']


@override_settings(**REGLAGES)
class Base(TestCase):
    def setUp(self):
        self._patch(mock.patch('requests.sessions.Session.request', _reseau_interdit))
        self._patch(mock.patch.dict(storage_client._token_cache, {'value': '', 'exp': 0.0}))
        self._patch(mock.patch.object(storage_client, '_container_ready', False))
        self.storage = FauxStorage()
        for verbe in ('get', 'post', 'patch', 'delete'):
            self._patch(mock.patch.object(
                storage_client.requests, verbe,
                side_effect=lambda url, _v=verbe.upper(), **kw: self.storage.servir(_v, url, **kw)))

    def _patch(self, patcher):
        patcher.start()
        self.addCleanup(patcher.stop)

    def ns(self, bien):
        return storage_client.namespace_for_bien(bien.pk)

    def commande(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                call_command('rattacher_partages_storage', *args, stdout=out, stderr=err)
        except SystemExit as exc:
            code = exc.code
        return code, out.getvalue() + err.getvalue()


class CreationRattacheeTests(Base):

    def setUp(self):
        super().setUp()
        self.bien = Bien.objects.create(nom='Maison')

    def test_creation_avec_parent_conteneur_cree_au_besoin(self):
        storage_client.add_member(self.bien.pk, 'bob@ssolab.local', role='read')
        self.assertEqual(self.storage.parents, {C: None, self.ns(self.bien): C})
        self.assertEqual(self.storage.membres[self.ns(self.bien)], {'bob@ssolab.local': 'read'})

    def test_un_depot_cree_d_abord_le_partage_range(self):
        storage_client.upload(self.bien.pk, 'factures/a.pdf', b'pdf', 'a.pdf', 'application/pdf')
        self.assertEqual(self.storage.parents[self.ns(self.bien)], C)
        self.assertEqual(self.storage.fichiers[(self.ns(self.bien), 'factures/a.pdf')], b'pdf')

    def test_conteneur_memorise_et_partage_existant_non_recree(self):
        autre = Bien.objects.create(nom='Chalet')
        storage_client.ensure_share(self.bien.pk)
        storage_client.ensure_share(autre.pk)
        storage_client.ensure_share(autre.pk)
        sondes = [a for a in self.storage.appels if a == ('GET', f'{URL}/api/shares/{C}/members/')]
        self.assertEqual(len(sondes), 1)
        self.assertEqual(len([a for a in self.storage.appels if a == ('POST', f'{URL}/api/shares/')]), 3)

    def test_repli_sans_parent_sur_400_parent(self):
        self.storage.refuser_parent = True
        with self.assertLogs('api.storage_client', 'WARNING') as logs:
            storage_client.ensure_share(self.bien.pk)
        self.assertIsNone(self.storage.parents[self.ns(self.bien)])
        texte = ''.join(logs.output)
        self.assertNotIn('http', texte)
        self.assertNotIn('jeton', texte.lower())

    def test_repli_quand_le_conteneur_appartient_a_un_autre(self):
        self.storage.etrangers.add(C)
        with self.assertLogs('api.storage_client', 'WARNING'):
            storage_client.ensure_share(self.bien.pk)
        self.assertIsNone(self.storage.parents[self.ns(self.bien)])
        self.assertFalse(storage_client._container_ready)

    def test_400_name_a_la_creation_est_un_succes(self):
        reel = storage_client._share_is_ours
        sondes = []

        def sonde(nom):
            # Course : le partage apparaît entre la sonde et la création.
            if nom == self.ns(self.bien) and not sondes:
                sondes.append(nom)
                self.storage.membres[nom] = {}
                return False
            return reel(nom)
        with mock.patch.object(storage_client, '_share_is_ours', side_effect=sonde):
            storage_client.ensure_share(self.bien.pk)       # ne lève pas

    def test_echec_du_rattachement_ne_fait_pas_echouer_le_depot(self):
        self.storage.refuser_parent = True
        self.storage.etrangers.add(C)
        with self.assertLogs('api.storage_client', 'WARNING'):
            r = storage_client.upload(self.bien.pk, 'factures/a.pdf', b'pdf', 'a.pdf')
        self.assertEqual(r, {'relative_path': 'factures/a.pdf'})
        self.assertIn((self.ns(self.bien), 'factures/a.pdf'), self.storage.fichiers)

    def test_partage_non_preparable_le_depot_est_tente_quand_meme(self):
        reel = self.storage.servir

        def servir(verbe, url, **kw):
            if '/api/shares/' in url:
                return _Rep(500)
            return reel(verbe, url, **kw)
        self.storage.servir = servir
        with self.assertLogs('api.storage_client', 'WARNING'):
            r = storage_client.upload(self.bien.pk, 'factures/a.pdf', b'pdf', 'a.pdf')
        self.assertEqual(r, {'relative_path': 'factures/a.pdf'})


class CommandeRattachementTests(Base):

    def setUp(self):
        super().setUp()
        self.b3 = Bien.objects.create(nom='3')
        self.b4 = Bien.objects.create(nom='4')
        self.range = Bien.objects.create(nom='rangé')
        self.sans_partage = Bien.objects.create(nom='sans facture')
        for b in (self.b3, self.b4):
            self.storage.membres[self.ns(b)] = {}
        self.storage.membres[C] = {}
        self.storage.membres[self.ns(self.range)] = {}
        self.storage.parents[self.ns(self.range)] = C
        self.storage.membres['conciergerie-bien-999'] = {}   # sans bien : jamais touché

    def test_check_est_en_lecture_seule_et_sort_en_1(self):
        code, sortie = self.commande('--check')
        self.assertEqual(code, 1)
        self.assertIn(self.ns(self.b3), sortie)
        self.assertIn(self.ns(self.b4), sortie)
        self.assertNotIn(self.ns(self.range), sortie)
        self.assertEqual([a for a in self.storage.appels if a[0] != 'GET'], [])
        self.assertNotIn('http', sortie)

    def test_rattache_puis_rien_a_faire_au_second_passage(self):
        self.assertEqual(self.commande()[0], 0)
        self.assertEqual(self.storage.parents[self.ns(self.b3)], C)
        self.assertEqual(self.storage.parents[self.ns(self.b4)], C)
        self.assertEqual(len(self.storage.patches()), 2)
        self.assertNotIn(self.ns(self.sans_partage), self.storage.membres)   # ignoré, pas créé
        self.assertIsNone(self.storage.parents.get('conciergerie-bien-999'))
        self.assertEqual(self.commande('--check')[0], 0)
        self.assertEqual(self.commande()[0], 0)
        self.assertEqual(len(self.storage.patches()), 2)

    def test_cree_le_conteneur_s_il_manque(self):
        del self.storage.membres[C]
        self.assertEqual(self.commande('--check')[0], 1)
        self.assertEqual(self.commande()[0], 0)
        self.assertIn(C, self.storage.membres)
        self.assertEqual(self.storage.parents[self.ns(self.b3)], C)

    def test_conteneur_a_un_autre_code_1_rien_touche(self):
        del self.storage.membres[C]
        self.storage.etrangers.add(C)
        self.assertEqual(self.commande()[0], 1)
        self.assertEqual(self.storage.patches(), [])

    def test_patch_refuse_compte_en_echec_et_continue(self):
        self.storage.refuser_parent = True
        code, sortie = self.commande()
        self.assertEqual(code, 1)
        self.assertEqual(len(self.storage.patches()), 2)
        self.assertIn('2 échec', sortie)

    def test_patch_404_ignore(self):
        reel = self.storage.servir
        cible = self.ns(self.b3)

        def servir(verbe, url, **kw):
            if verbe == 'PATCH' and url.endswith(f'/api/shares/{cible}/'):
                self.storage.membres.pop(cible, None)       # disparu entre lecture et PATCH
            return reel(verbe, url, **kw)
        self.storage.servir = servir
        self.assertEqual(self.commande()[0], 0)
        self.assertEqual(self.storage.parents[self.ns(self.b4)], C)

    def test_storage_injoignable_code_1(self):
        self.storage.arrete = True
        self.assertEqual(self.commande('--check')[0], 1)
        self.assertEqual(self.commande()[0], 1)
