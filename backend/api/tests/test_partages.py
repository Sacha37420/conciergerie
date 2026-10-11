"""PartProprietaire ↔ ShareMember du partage `storage` du bien (signaux), et
client HTTP de `storage` (compte de service) — tout le réseau est simulé."""
from unittest import mock

import requests
from django.db import connection
from django.test import SimpleTestCase, override_settings

from api import storage_client
from api.models import PartProprietaire, Proprietaire
from .base import REGLAGES, Base


class SynchronisationDesMembresTests(Base):
    """La lecture directe d'une facture dans storage par un co-propriétaire ne
    tient que si les membres du partage suivent exactement les parts."""

    def setUp(self):
        super().setUp()
        self.monde()
        self.dan = Proprietaire.objects.create(nom='Dan', email='dan@ssolab.local')

    def test_creer_une_part_ouvre_la_lecture_seule(self):
        resp = self.gina.post('/api/parts-proprietaire/',
                              {'bien': self.maison.pk, 'proprietaire': self.dan.pk}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.storage.add_member.assert_called_once_with(self.maison.pk, 'dan@ssolab.local', role='read')

    def test_proprietaire_sans_email_aucun_membre(self):
        sans = Proprietaire.objects.create(nom='Sans compte')
        resp = self.gina.post('/api/parts-proprietaire/',
                              {'bien': self.maison.pk, 'proprietaire': sans.pk}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.gina.delete(f"/api/parts-proprietaire/{resp.json()['id']}/")
        self.assertEqual(self.storage.method_calls, [])

    def test_storage_en_panne_la_part_n_est_pas_creee(self):
        # Sinon : un co-propriétaire en base sans accès à ses factures.
        self.storage.add_member.side_effect = storage_client.StorageClientError('panne')
        self.gina.raise_request_exception = False
        resp = self.gina.post('/api/parts-proprietaire/',
                              {'bien': self.maison.pk, 'proprietaire': self.dan.pk}, format='json')
        self.assertEqual(resp.status_code, 500)
        self.assertFalse(PartProprietaire.objects.filter(proprietaire=self.dan).exists())

    def test_supprimer_une_part_retire_le_membre(self):
        part = PartProprietaire.objects.get(bien=self.maison, proprietaire=self.p_bob)
        self.assertEqual(self.gina.delete(f'/api/parts-proprietaire/{part.pk}/').status_code, 204)
        self.storage.remove_member.assert_called_once_with(self.maison.pk, 'bob@ssolab.local')

    def test_retrait_impossible_la_part_reste(self):
        # Sinon : un accès storage orphelin pour quelqu'un qui n'a plus de part.
        self.storage.remove_member.side_effect = storage_client.StorageClientError('panne')
        self.gina.raise_request_exception = False
        part = PartProprietaire.objects.get(bien=self.maison, proprietaire=self.p_bob)
        self.assertEqual(self.gina.delete(f'/api/parts-proprietaire/{part.pk}/').status_code, 500)
        # La suppression s'exécute dans la transaction de Django (sans point
        # de sauvegarde) : l'échec du signal la condamne à l'annulation. Sous
        # TestCase on ne peut pas relire la ligne, on constate la condamnation.
        self.assertTrue(connection.needs_rollback)

    def test_deplacer_une_part_deplace_le_membre(self):
        part = PartProprietaire.objects.get(bien=self.maison, proprietaire=self.p_bob)
        resp = self.gina.patch(f'/api/parts-proprietaire/{part.pk}/', {'bien': self.chalet.pk}, format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.storage.method_calls, [
            mock.call.remove_member(self.maison.pk, 'bob@ssolab.local'),
            mock.call.add_member(self.chalet.pk, 'bob@ssolab.local', role='read'),
        ])

    def test_enregistrer_une_part_inchangee_ne_touche_a_rien(self):
        part = PartProprietaire.objects.get(bien=self.maison, proprietaire=self.p_bob)
        self.gina.put(f'/api/parts-proprietaire/{part.pk}/',
                      {'bien': self.maison.pk, 'proprietaire': self.p_bob.pk}, format='json')
        self.assertEqual(self.storage.method_calls, [])

    def test_changer_d_email_migre_l_acces_sur_chaque_bien(self):
        PartProprietaire.objects.create(bien=self.chalet, proprietaire=self.p_bob)
        self.storage.reset_mock()
        resp = self.gina.patch(f'/api/proprietaires/{self.p_bob.pk}/', {'email': 'robert@ssolab.local'},
                               format='json')
        self.assertEqual(resp.status_code, 200)
        self.assertCountEqual(self.storage.remove_member.call_args_list, [
            mock.call(self.maison.pk, 'bob@ssolab.local'), mock.call(self.chalet.pk, 'bob@ssolab.local')])
        self.assertCountEqual(self.storage.add_member.call_args_list, [
            mock.call(self.maison.pk, 'robert@ssolab.local', role='read'),
            mock.call(self.chalet.pk, 'robert@ssolab.local', role='read')])
        # L'ancien compte perd aussi la vue dans l'app, le nouveau la gagne.
        self.assertEqual(self.bob.get('/api/biens/').json(), [])
        self.assertEqual(len(self.client_de('robert').get('/api/biens/').json()), 2)

    def test_changement_d_email_annule_si_storage_echoue(self):
        self.storage.add_member.side_effect = storage_client.StorageClientError('panne')
        self.gina.raise_request_exception = False
        resp = self.gina.patch(f'/api/proprietaires/{self.p_bob.pk}/', {'email': 'robert@ssolab.local'},
                               format='json')
        self.assertEqual(resp.status_code, 500)
        self.p_bob.refresh_from_db()
        self.assertEqual(self.p_bob.email, 'bob@ssolab.local')

    def test_modifier_autre_chose_que_l_email_ne_touche_a_rien(self):
        self.gina.patch(f'/api/proprietaires/{self.p_bob.pk}/', {'telephone': '0600000000'}, format='json')
        self.assertEqual(self.storage.method_calls, [])

    def test_supprimer_un_proprietaire_retire_tous_ses_acces(self):
        # Par cascade : aucune vue de part n'est traversée.
        PartProprietaire.objects.create(bien=self.chalet, proprietaire=self.p_bob)
        self.storage.reset_mock()
        self.assertEqual(self.gina.delete(f'/api/proprietaires/{self.p_bob.pk}/').status_code, 204)
        self.assertCountEqual(self.storage.remove_member.call_args_list, [
            mock.call(self.maison.pk, 'bob@ssolab.local'), mock.call(self.chalet.pk, 'bob@ssolab.local')])

    def test_supprimer_un_bien_retire_ses_membres(self):
        pk = self.maison.pk
        self.assertEqual(self.gina.delete(f'/api/biens/{pk}/').status_code, 204)
        self.assertCountEqual(self.storage.remove_member.call_args_list, [
            mock.call(pk, 'alice@ssolab.local'), mock.call(pk, 'bob@ssolab.local')])


def _rep(code=200, corps=None, texte=''):
    return mock.Mock(status_code=code, json=mock.Mock(return_value=corps or {}), text=texte)


@override_settings(**REGLAGES)
class ClientStorageTests(SimpleTestCase):
    """api/storage_client.py contre un `requests` simulé."""

    def setUp(self):
        storage_client._token_cache.update(value='', exp=0.0)
        self.addCleanup(lambda: storage_client._token_cache.update(value='', exp=0.0))
        self.post = self._patch('post')
        self.get = self._patch('get')
        self.delete = self._patch('delete')
        self.jeton = _rep(200, {'access_token': 'jeton-de-service', 'expires_in': 300})

    def _patch(self, nom):
        p = mock.patch.object(storage_client.requests, nom)
        m = p.start()
        self.addCleanup(p.stop)
        return m

    def test_jeton_du_compte_de_service_demande_une_fois(self):
        self.post.return_value = self.jeton
        self.assertEqual(storage_client._token(), 'jeton-de-service')
        self.assertEqual(storage_client._token(), 'jeton-de-service')
        self.post.assert_called_once()
        (url,), kw = self.post.call_args
        self.assertEqual(url, 'http://keycloak.test/realms/ssolab/protocol/openid-connect/token')
        self.assertEqual(kw['data'], {'grant_type': 'client_credentials', 'client_id': 'conciergerie-admin',
                                      'client_secret': 'secret-de-test'})
        self.assertTrue(kw['timeout'])

    def test_jeton_redemande_a_l_expiration(self):
        self.post.return_value = self.jeton
        storage_client._token()
        storage_client._token_cache['exp'] = 0.0
        storage_client._token()
        self.assertEqual(self.post.call_count, 2)

    def test_erreurs_d_obtention_du_jeton(self):
        with override_settings(KEYCLOAK_ADMIN_CLIENT_SECRET=''):
            with self.assertRaisesMessage(storage_client.StorageClientError, 'manquants'):
                storage_client._token()
        with override_settings(KEYCLOAK_ISSUER_URI='http://keycloak.test/auth'):
            with self.assertRaisesMessage(storage_client.StorageClientError, 'invalide'):
                storage_client._token()
        self.post.assert_not_called()
        self.post.return_value = _rep(401, texte='unauthorized_client')
        with self.assertRaisesMessage(storage_client.StorageClientError, 'HTTP 401'):
            storage_client._token()
        self.post.side_effect = requests.ConnectionError('refusé')
        with self.assertRaisesMessage(storage_client.StorageClientError, 'Keycloak injoignable'):
            storage_client._token()

    def test_depot_dans_le_partage_du_bien(self):
        self.post.side_effect = [self.jeton, _rep(201, {'relative_path': 'factures/a.pdf'})]
        resultat = storage_client.upload(7, 'factures/a.pdf', b'octets', 'a.pdf', 'application/pdf')
        self.assertEqual(resultat, {'relative_path': 'factures/a.pdf'})
        (url,), kw = self.post.call_args
        self.assertEqual(url, 'http://storage.test/api/files/conciergerie-bien-7/')
        self.assertEqual(kw['headers'], {'Authorization': 'Bearer jeton-de-service'})
        self.assertEqual(kw['data'], {'relative_path': 'factures/a.pdf'})
        self.assertEqual(kw['files']['file'], ('a.pdf', b'octets', 'application/pdf'))

    def test_depot_refuse_ou_storage_injoignable(self):
        self.post.side_effect = [self.jeton, _rep(403, texte='Forbidden')]
        with self.assertRaisesMessage(storage_client.StorageClientError, 'HTTP 403'):
            storage_client.upload(7, 'a', b'', 'a')
        self.post.side_effect = requests.Timeout('trop long')
        with self.assertRaisesMessage(storage_client.StorageClientError, 'storage injoignable'):
            storage_client.upload(7, 'a', b'', 'a')

    def test_telechargement(self):
        self.post.return_value = self.jeton
        self.get.return_value = _rep(200)
        self.assertIs(storage_client.download(7, 'factures/a.pdf'), self.get.return_value)
        (url,), kw = self.get.call_args
        self.assertEqual(url, 'http://storage.test/api/files/conciergerie-bien-7/content/factures/a.pdf')
        self.assertTrue(kw['stream'])
        self.get.return_value = _rep(404)
        with self.assertRaises(storage_client.StorageClientError):
            storage_client.download(7, 'factures/a.pdf')

    def test_suppression_idempotente(self):
        self.post.return_value = self.jeton
        for code in (204, 404):
            self.delete.return_value = _rep(code)
            storage_client.delete(7, 'factures/a.pdf')
        self.assertEqual(self.delete.call_args.args[0],
                         'http://storage.test/api/files/conciergerie-bien-7/content/factures/a.pdf')
        for code in (403, 500):
            self.delete.return_value = _rep(code)
            with self.assertRaises(storage_client.StorageClientError):
                storage_client.delete(7, 'factures/a.pdf')

    def test_ajout_d_un_membre_cree_d_abord_le_partage(self):
        appels = []
        self.get.side_effect = lambda url, **kw: appels.append(('GET', url)) or _rep(200)

        def post(url, **kw):
            if url.endswith('/token'):
                return self.jeton
            appels.append(('POST', url, kw['data']))
            return _rep(201)

        self.post.side_effect = post
        storage_client.add_member(7, 'bob@ssolab.local', role='read')
        self.assertEqual(appels, [
            ('GET', 'http://storage.test/api/files/conciergerie-bien-7/'),
            ('POST', 'http://storage.test/api/shares/conciergerie-bien-7/members/',
             {'member_email': 'bob@ssolab.local', 'role': 'read'}),
        ])

    def test_ajout_d_un_membre_echecs(self):
        self.post.side_effect = lambda url, **kw: self.jeton if url.endswith('/token') else _rep(400, texte='non')
        self.get.return_value = _rep(403)
        with self.assertRaisesMessage(storage_client.StorageClientError, 'Création du partage'):
            storage_client.add_member(7, 'bob@ssolab.local')
        self.get.return_value = _rep(200)
        with self.assertRaisesMessage(storage_client.StorageClientError, 'HTTP 400'):
            storage_client.add_member(7, 'bob@ssolab.local')

    def test_retrait_d_un_membre_idempotent(self):
        self.post.return_value = self.jeton
        for code in (204, 404):
            self.delete.return_value = _rep(code)
            storage_client.remove_member(7, 'bob@ssolab.local')
        self.assertEqual(self.delete.call_args.args[0],
                         'http://storage.test/api/shares/conciergerie-bien-7/members/bob@ssolab.local/')
        self.delete.return_value = _rep(500)
        with self.assertRaises(storage_client.StorageClientError):
            storage_client.remove_member(7, 'bob@ssolab.local')
