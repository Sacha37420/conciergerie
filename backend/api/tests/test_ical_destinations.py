"""L'URL iCal est appelée par le backend, depuis le réseau des conteneurs du
lab : elle ne doit jamais désigner une destination interne (api/ical_fetch.py).
Contrôle à l'enregistrement ET à chaque appel, redirections comprises.
La résolution DNS est simulée."""
import datetime
from unittest import mock

from django.test import SimpleTestCase

from api import ical_fetch
from api.models import Appartement, Reservation
from .base import Base
from .test_sync_airbnb import URL_MAISON, SyncBase, evenement, ics, FUTUR

PUBLIQUE = '34.120.10.20'
INTERNES = [
    '127.0.0.1', '127.8.8.8', '10.0.0.5', '172.18.0.3', '192.168.1.50', '192.168.16.4',
    '169.254.169.254', '0.0.0.0', '100.64.0.1', '224.0.0.1', '255.255.255.255',
    '::1', '::', 'fe80::1', 'fe80::1%eth0', 'fc00::1', 'fd12:3456::1', 'ff02::1',
    '::ffff:10.0.0.1', '::ffff:127.0.0.1', '2002:0a00:0001::1',
]


class _DnsSimule(SimpleTestCase):

    def setUp(self):
        self.dns = {}
        self.resolutions = []

        def resoudre(hote, port):
            self.resolutions.append((hote, port))
            reponse = self.dns.get(hote, [PUBLIQUE])
            if isinstance(reponse, Exception):
                raise reponse
            return reponse

        p = mock.patch.object(ical_fetch, '_resoudre', resoudre)
        p.start()
        self.addCleanup(p.stop)

    def refuse(self, url):
        with self.assertRaises(ical_fetch.DestinationInterdite):
            ical_fetch.verifier_url(url)


class VerifierUrlTests(_DnsSimule):

    def test_destination_publique_acceptee(self):
        self.dns['www.airbnb.fr'] = [PUBLIQUE, '2a00:1450:4007:80e::200e']
        ical_fetch.verifier_url('https://www.airbnb.fr/calendar/ical/1.ics?s=x')
        ical_fetch.verifier_url('http://www.airbnb.fr:8080/c.ics')
        self.assertEqual(self.resolutions, [('www.airbnb.fr', 443), ('www.airbnb.fr', 8080)])

    def test_seuls_http_et_https(self):
        for url in ('ftp://www.airbnb.fr/c.ics', 'file:///etc/passwd', 'gopher://www.airbnb.fr/',
                    'javascript:alert(1)', 'www.airbnb.fr/c.ics', '//www.airbnb.fr/c.ics', '',
                    'https:///c.ics', 'https://www.airbnb.fr:port/c.ics'):
            with self.subTest(url=url):
                self.refuse(url)
        self.assertEqual(self.resolutions, [])

    def test_toute_adresse_interne_refusee(self):
        for adresse in INTERNES:
            with self.subTest(adresse=adresse):
                self.dns['hote.example'] = [adresse]
                self.refuse('https://hote.example/c.ics')

    def test_adresse_ecrite_en_clair_dans_l_url(self):
        # Le contrôle porte sur ce que rend la résolution, quelle que soit
        # l'écriture (décimale, hexadécimale, IPv6 entre crochets).
        cas = {'127.0.0.1': '127.0.0.1', '2130706433': '127.0.0.1', '0x7f.1': '127.0.0.1',
               '[::1]': '::1', '192.168.1.50': '192.168.1.50'}
        for ecriture, adresse in cas.items():
            with self.subTest(ecriture=ecriture):
                self.dns[ecriture.strip('[]')] = [adresse]
                self.refuse(f'http://{ecriture}/c.ics')

    def test_noms_de_conteneurs_du_lab(self):
        for nom in ('storage-backend', 'keycloak', 'dev-postgres', 'lab-mcp', 'localhost'):
            with self.subTest(nom=nom):
                self.dns[nom] = ['172.18.0.7']
                self.refuse(f'http://{nom}:8000/api/')

    def test_chaque_adresse_obtenue_est_controlee(self):
        # Un nom qui rend une adresse publique ET une interne est refusé.
        self.dns['double.example'] = [PUBLIQUE, '10.0.0.5']
        self.refuse('https://double.example/c.ics')
        self.dns['double.example'] = ['10.0.0.5', PUBLIQUE]
        self.refuse('https://double.example/c.ics')

    def test_nom_introuvable_ou_sans_adresse_refuse(self):
        self.dns['inconnu.example'] = OSError('Name or service not known')
        self.refuse('https://inconnu.example/c.ics')
        self.dns['vide.example'] = []
        self.refuse('https://vide.example/c.ics')

    def test_le_refus_ne_dit_pas_ce_que_le_nom_designe(self):
        # Même message pour « interne » et « introuvable » : le contrôle ne
        # sert pas à sonder les noms du réseau interne.
        messages = set()
        for reponse in (['172.18.0.7'], OSError('introuvable'), []):
            self.dns['x.example'] = reponse
            with self.assertRaises(ical_fetch.DestinationInterdite) as cm:
                ical_fetch.verifier_url('https://x.example/c.ics')
            messages.add(str(cm.exception))
            self.assertNotIn('172.18', str(cm.exception))
        self.assertEqual(len(messages), 1)


def _rep(code=200, contenu=b'', location=None):
    return mock.Mock(status_code=code, content=contenu, headers={'Location': location} if location else {},
                     raise_for_status=mock.Mock())


class TelechargerTests(_DnsSimule):

    def setUp(self):
        super().setUp()
        p = mock.patch.object(ical_fetch.requests, 'get')
        self.get = p.start()
        self.addCleanup(p.stop)

    def urls_appelees(self):
        return [c.args[0] for c in self.get.call_args_list]

    def test_telechargement_sans_suivi_automatique_des_redirections(self):
        self.get.return_value = _rep(200, b'BEGIN:VCALENDAR')
        self.assertEqual(ical_fetch.telecharger('https://www.airbnb.fr/c.ics'), b'BEGIN:VCALENDAR')
        self.get.assert_called_once_with('https://www.airbnb.fr/c.ics', timeout=15, allow_redirects=False)

    def test_destination_interne_aucun_appel(self):
        self.dns['storage-backend'] = ['172.18.0.7']
        with self.assertRaises(ical_fetch.DestinationInterdite):
            ical_fetch.telecharger('http://storage-backend:8000/api/')
        self.get.assert_not_called()

    def test_redirection_publique_suivie_et_recontrolee(self):
        self.get.side_effect = [_rep(302, location='/v2/c.ics'), _rep(301, location='https://cdn.example/c.ics'),
                                _rep(200, b'ok')]
        self.assertEqual(ical_fetch.telecharger('https://www.airbnb.fr/c.ics'), b'ok')
        self.assertEqual(self.urls_appelees(), ['https://www.airbnb.fr/c.ics', 'https://www.airbnb.fr/v2/c.ics',
                                                'https://cdn.example/c.ics'])
        self.assertEqual([h for h, _ in self.resolutions], ['www.airbnb.fr', 'www.airbnb.fr', 'cdn.example'])

    def test_redirection_vers_l_interieur_jamais_suivie(self):
        self.dns['interne.example'] = ['10.0.0.5']
        for cible in ('http://127.0.0.1:8080/admin', 'http://keycloak:8080/realms/ssolab',
                      'https://interne.example/', 'file:///etc/passwd', '//169.254.169.254/latest/meta-data/'):
            for code in (301, 302, 303, 307, 308):
                with self.subTest(cible=cible, code=code):
                    self.dns.update({'127.0.0.1': ['127.0.0.1'], 'keycloak': ['172.18.0.2'],
                                     '169.254.169.254': ['169.254.169.254']})
                    self.get.reset_mock()
                    self.get.side_effect = [_rep(code, location=cible), _rep(200, b'SECRET')]
                    with self.assertRaises(ical_fetch.DestinationInterdite):
                        ical_fetch.telecharger('https://www.airbnb.fr/c.ics')
                    self.assertEqual(self.urls_appelees(), ['https://www.airbnb.fr/c.ics'])

    def test_redirections_en_boucle_bornees(self):
        self.get.side_effect = lambda url, **kw: _rep(302, location='https://www.airbnb.fr/encore')
        with self.assertRaises(ical_fetch.DestinationInterdite):
            ical_fetch.telecharger('https://www.airbnb.fr/c.ics')
        self.assertLessEqual(self.get.call_count, 6)

    def test_redirection_sans_destination(self):
        self.get.return_value = _rep(302)
        with self.assertRaises(ical_fetch.DestinationInterdite):
            ical_fetch.telecharger('https://www.airbnb.fr/c.ics')


class EnregistrementTests(Base):

    def setUp(self):
        super().setUp()
        self.monde()
        self.dns = {'www.airbnb.fr': [PUBLIQUE], 'storage-backend': ['172.18.0.7'], 'rebond.example': ['10.0.0.5']}
        self.resolutions = []

        def resoudre(hote, port):
            self.resolutions.append(hote)
            if hote not in self.dns:
                raise OSError('Name or service not known')
            return self.dns[hote]

        self._patch(mock.patch.object(ical_fetch, '_resoudre', resoudre))
        self.url = f'/api/appartements/{self.appt_maison.pk}/'

    def test_url_interne_refusee_a_l_enregistrement(self):
        for valeur in ('http://storage-backend:8000/api/files/', 'https://rebond.example/c.ics',
                       'https://inconnu.example/c.ics', 'ftp://www.airbnb.fr/c.ics'):
            with self.subTest(valeur=valeur):
                resp = self.gina.patch(self.url, {'airbnb_ical_url': valeur}, format='json')
                self.assertEqual(resp.status_code, 400)
                self.assertIn('airbnb_ical_url', resp.json())
                resp = self.gina.post('/api/appartements/', {'bien': self.maison.pk, 'nom': 'N',
                                                             'airbnb_ical_url': valeur}, format='json')
                self.assertEqual(resp.status_code, 400)
        self.appt_maison.refresh_from_db()
        self.assertEqual(self.appt_maison.airbnb_ical_url, '')
        self.assertEqual(Appartement.objects.count(), 2)

    def test_url_publique_acceptee_et_url_vide(self):
        resp = self.gina.patch(self.url, {'airbnb_ical_url': URL_MAISON}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.resolutions, ['www.airbnb.fr'])
        self.assertEqual(self.gina.patch(self.url, {'airbnb_ical_url': ''}, format='json').status_code, 200)

    def test_url_inchangee_non_recontrolee_a_l_enregistrement(self):
        # Renommer un appartement ne dépend pas du DNS : l'URL déjà en base
        # sera de toute façon contrôlée à chaque appel.
        Appartement.objects.filter(pk=self.appt_maison.pk).update(airbnb_ical_url='https://inconnu.example/c.ics')
        resp = self.gina.put(self.url, {'bien': self.maison.pk, 'nom': 'RDC bis',
                                        'airbnb_ical_url': 'https://inconnu.example/c.ics'}, format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.resolutions, [])


class SynchroDestinationTests(SyncBase):

    def setUp(self):
        super().setUp()
        Reservation.objects.create(appartement=self.appt_maison, source='airbnb', uid_externe='a',
                                   date_debut=datetime.date(FUTUR, 7, 1), date_fin=datetime.date(FUTUR, 7, 5))

    def test_url_devenue_interne_entre_l_enregistrement_et_l_appel(self):
        # Le nom, public à l'enregistrement, désigne maintenant un conteneur.
        self.dns['www.airbnb.fr'] = ['172.18.0.7']
        self.flux[URL_MAISON] = ics()
        _, erreurs = self.synchroniser()
        self.http.assert_not_called()
        self.assertIn('échec sync', erreurs)
        self.assertEqual(self.statuts(), {'a': 'confirmee'})
        self.appt_maison.refresh_from_db()
        self.assertNotEqual(self.appt_maison.dernier_sync_erreur, '')
        self.assertNotIn('172.18', self.appt_maison.dernier_sync_erreur)

    def test_url_interne_deja_en_base_jamais_appelee(self):
        Appartement.objects.filter(pk=self.appt_maison.pk).update(airbnb_ical_url='http://storage-backend:8000/api/')
        self.dns['storage-backend'] = ['172.18.0.7']
        self.synchroniser()
        self.http.assert_not_called()

    def test_flux_qui_redirige_vers_l_interieur(self):
        self.dns['keycloak'] = ['172.18.0.2']
        self.http.side_effect = [mock.Mock(status_code=302, headers={'Location': 'http://keycloak:8080/'})]
        _, erreurs = self.synchroniser()
        self.assertEqual(self.http.call_count, 1)
        self.assertIn('échec sync', erreurs)
        self.assertEqual(self.statuts(), {'a': 'confirmee'})

    def test_flux_qui_redirige_vers_une_destination_publique(self):
        self.http.side_effect = [
            mock.Mock(status_code=301, headers={'Location': 'https://cdn.example/c.ics'}),
            mock.Mock(status_code=200, headers={}, raise_for_status=mock.Mock(),
                      content=ics(evenement('a', f'{FUTUR}0701', f'{FUTUR}0705'),
                                  evenement('b', f'{FUTUR}0801', f'{FUTUR}0805'))),
        ]
        sortie, erreurs = self.synchroniser()
        self.assertEqual(erreurs, '')
        self.assertEqual(self.statuts(), {'a': 'confirmee', 'b': 'confirmee'})
