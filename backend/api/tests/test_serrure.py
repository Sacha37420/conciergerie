"""Verrou 2 : la serrure de l'API, par de vrais jetons signés localement."""
import json
import time

import jwt
from django.test import override_settings
from rest_framework.test import APIClient

from api.models import Bien
from .base import AUTRE_RSA, CLIENT_ID, Base, jeton


class SerrureTests(Base):

    def test_sans_jeton_tout_est_refuse(self):
        self.monde()
        anonyme = APIClient()
        ressources = ['proprietaires', 'entreprises', 'biens', 'parts-proprietaire', 'appartements',
                      'reservations', 'taches', 'frais', 'remboursements', 'apports-initiaux',
                      'versements-revenu']
        appels = [('get', '/api/me/'), ('post', '/api/sync/airbnb/'),
                  ('get', f'/api/biens/{self.maison.pk}/bilan/'), ('get', '/api/frais/1/facture/'),
                  ('delete', f'/api/biens/{self.maison.pk}/')]
        for r in ressources:
            appels += [('get', f'/api/{r}/'), ('post', f'/api/{r}/'), ('get', f'/api/{r}/1/')]
        for methode, chemin in appels:
            with self.subTest(appel=f'{methode} {chemin}'):
                resp = getattr(anonyme, methode)(chemin, {}, format='json')
                self.assertIn(resp.status_code, (401, 403))
        self.assertTrue(Bien.objects.filter(pk=self.maison.pk).exists())

    def test_membres_acceptes_et_identite_tiree_du_jeton(self):
        resp = self.client_de('alice', groups=['proprietaires', 'amis']).get('/api/me/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {
            'email': 'alice@ssolab.local', 'username': 'alice', 'groups': ['proprietaires', 'amis'],
            'is_manager': False, 'proprietaire': None,
        })
        resp = self.client_de('gina', groups=['admins']).get('/api/me/')
        self.assertTrue(resp.json()['is_manager'])

    def test_azp_etranger_refuse(self):
        # admin-cli : client public du realm avec password grant. Sans ce
        # contrôle, tout compte du realm appelle l'API sans croiser le flow.
        for azp in ('admin-cli', 'conciergerie-admin', 'storage', 'lab-admin', '', None):
            with self.subTest(azp=azp):
                resp = self.client_de('gina', groups=['admins', 'proprietaires'], azp=azp).get('/api/me/')
                self.assertEqual(resp.status_code, 403)
                self.assertIn('autre client', resp.json()['detail'])

    def test_hors_groupe_refuse(self):
        for groups in (['famille'], ['developers', 'amis'], [], None):
            with self.subTest(groups=groups):
                resp = self.client_de('lea', groups=groups).get('/api/me/')
                self.assertEqual(resp.status_code, 403)
                self.assertIn('groupe(s)', resp.json()['detail'])

    def test_nom_de_groupe_approchant_refuse(self):
        for groups in (['admin'], ['proprietaire'], ['/admins'], ['Admins'], ['admins-adjoints']):
            with self.subTest(groups=groups):
                self.assertEqual(self.client_de('lea', groups=groups).get('/api/me/').status_code, 403)

    def test_jeton_expire_refuse(self):
        resp = self.client_de('gina', groups=['admins'], exp=-60).get('/api/me/')
        self.assertEqual(resp.status_code, 403)
        self.assertIn('Token invalide', resp.json()['detail'])

    def test_signature_d_une_autre_cle_refusee(self):
        resp = self.avec(jeton('gina', groups=['admins'], cle=AUTRE_RSA)).get('/api/me/')
        self.assertEqual(resp.status_code, 403)
        self.assertIn('Token invalide', resp.json()['detail'])

    def test_contenu_modifie_apres_signature_refuse(self):
        # Un propriétaire réécrit son jeton pour s'ajouter le groupe `admins`.
        entete, _, signature = jeton('alice').split('.')
        charge = jwt.utils.base64url_encode(json.dumps({
            'preferred_username': 'alice', 'azp': CLIENT_ID, 'groups': ['admins'],
            'email': 'alice@ssolab.local', 'exp': int(time.time()) + 300,
        }).encode()).decode()
        self.assertEqual(self.avec(f'{entete}.{charge}.{signature}').get('/api/me/').status_code, 403)

    def test_algorithme_autre_que_rs256_refuse(self):
        claims = {'preferred_username': 'gina', 'azp': CLIENT_ID, 'groups': ['admins'],
                  'email': 'gina@ssolab.local', 'exp': int(time.time()) + 300}
        sans_signature = jwt.encode(claims, None, algorithm='none')
        symetrique = jwt.encode(claims, 'secret-partage-suffisamment-long-pour-hs256', algorithm='HS256')
        for nom, tok in (('none', sans_signature), ('HS256', symetrique), ('charabia', 'pas.un.jeton')):
            with self.subTest(jeton=nom):
                self.assertEqual(self.avec(tok).get('/api/me/').status_code, 403)

    def test_jeton_sans_email_refuse(self):
        # Le cloisonnement par bien repose sur l'email du jeton.
        for email in (None, ''):
            with self.subTest(email=email):
                resp = self.client_de('gina', groups=['admins'], email=email).get('/api/me/')
                self.assertEqual(resp.status_code, 403)

    @override_settings(KEYCLOAK_REQUIRED_GROUPS='')
    def test_sans_groupe_requis_l_azp_reste_controle(self):
        self.assertEqual(self.client_de('lea', groups=[]).get('/api/me/').status_code, 200)
        self.assertEqual(self.client_de('lea', groups=[], azp='admin-cli').get('/api/me/').status_code, 403)

    @override_settings(KEYCLOAK_REQUIRED_GROUPS='proprietaires,admins,famille')
    def test_passer_la_porte_ne_donne_aucun_role(self):
        # Les permissions par vue ne s'en remettent pas à la porte : un groupe
        # ajouté un jour à KEYCLOAK_REQUIRED_GROUPS n'ouvre aucune donnée.
        self.monde()
        lea = self.client_de('lea', groups=['famille'])
        self.assertEqual(lea.get('/api/me/').status_code, 200)
        for chemin in ('/api/biens/', '/api/proprietaires/', '/api/frais/', '/api/reservations/',
                       '/api/remboursements/', f'/api/biens/{self.maison.pk}/bilan/'):
            with self.subTest(chemin=chemin):
                self.assertEqual(lea.get(chemin).status_code, 403)
        self.assertEqual(lea.post('/api/sync/airbnb/').status_code, 403)

    def test_documentation_de_l_api_absente_en_service(self):
        for c in (APIClient(), self.client_de('gina', groups=['admins'])):
            for chemin in ('/api/schema/', '/api/docs/', '/', '/media/factures/x.pdf'):
                with self.subTest(chemin=chemin):
                    self.assertEqual(c.get(chemin).status_code, 404)
