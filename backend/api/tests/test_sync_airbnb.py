"""Synchronisation des calendriers iCal Airbnb — le flux est simulé."""
import datetime
from decimal import Decimal
from io import StringIO
from unittest import mock

import requests
from django.core.management import call_command

from api import ical_fetch
from api.models import Appartement, Reservation
from .base import Base

URL_MAISON = 'https://www.airbnb.fr/calendar/ical/111.ics?s=secret-maison'
URL_CHALET = 'https://www.airbnb.fr/calendar/ical/222.ics?s=secret-chalet'
FUTUR, PASSE = 2099, 2001


def evenement(uid, debut, fin=None, *lignes):
    e = [f'UID:{uid}', f'DTSTART;VALUE=DATE:{debut}', 'SUMMARY:Reserved', *lignes]
    if fin:
        e.append(f'DTEND;VALUE=DATE:{fin}')
    return e


def ics(*evenements) -> bytes:
    lignes = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Airbnb Inc//Hosting Calendar//EN']
    for e in evenements:
        lignes += ['BEGIN:VEVENT', *e, 'END:VEVENT']
    return '\r\n'.join(lignes + ['END:VCALENDAR', '']).encode()


class SyncBase(Base):

    def setUp(self):
        super().setUp()
        self.monde()
        Appartement.objects.filter(pk=self.appt_maison.pk).update(airbnb_ical_url=URL_MAISON)
        self.flux = {}
        self.http = mock.Mock(side_effect=self._get)
        self._patch(mock.patch.object(ical_fetch.requests, 'get', self.http))
        # DNS simulé : tout nom rend une adresse publique, sauf mention contraire.
        self.dns = {}
        self._patch(mock.patch.object(ical_fetch, '_resoudre',
                                      lambda hote, port: self.dns.get(hote, ['34.120.10.20'])))

    def _get(self, url, **kw):
        contenu = self.flux[url]
        if isinstance(contenu, Exception):
            raise contenu
        return mock.Mock(status_code=200, headers={}, content=contenu, raise_for_status=mock.Mock())

    def synchroniser(self):
        out, err = StringIO(), StringIO()
        call_command('sync_airbnb_calendars', stdout=out, stderr=err)
        return out.getvalue(), err.getvalue()

    def statuts(self, appartement=None):
        return dict(Reservation.objects.filter(appartement=appartement or self.appt_maison)
                    .values_list('uid_externe', 'statut'))


class SyncAirbnbTests(SyncBase):

    def test_importe_les_sejours_du_flux(self):
        self.flux[URL_MAISON] = ics(
            evenement('a@airbnb.com', f'{FUTUR}0710', f'{FUTUR}0715'),
            evenement('b@airbnb.com', f'{FUTUR}0801'),                               # sans DTEND
            ['UID:c@airbnb.com', f'DTSTART:{FUTUR}0901T150000Z', f'DTEND:{FUTUR}0903T100000Z'],
            evenement('', f'{FUTUR}1001', f'{FUTUR}1002'),                           # sans UID : ignoré
        )
        sortie, erreurs = self.synchroniser()
        self.assertEqual(erreurs, '')
        self.assertIn('3 événement(s)', sortie)
        self.http.assert_called_once_with(URL_MAISON, timeout=15, allow_redirects=False)
        a = Reservation.objects.get(uid_externe='a@airbnb.com')
        self.assertEqual((a.appartement, a.source, a.statut, a.libelle),
                         (self.appt_maison, 'airbnb', 'confirmee', 'Reserved'))
        self.assertEqual((a.date_debut, a.date_fin), (datetime.date(FUTUR, 7, 10), datetime.date(FUTUR, 7, 15)))
        # Airbnb ne donne jamais le montant : il reste à saisir.
        self.assertIsNone(a.montant_revenu)
        b = Reservation.objects.get(uid_externe='b@airbnb.com')
        self.assertEqual(b.date_fin, b.date_debut)
        self.assertEqual(Reservation.objects.get(uid_externe='c@airbnb.com').date_fin,
                         datetime.date(FUTUR, 9, 3))
        self.assertEqual(Reservation.objects.count(), 3)
        self.appt_maison.refresh_from_db()
        self.assertIsNotNone(self.appt_maison.dernier_sync_at)
        self.assertEqual(self.appt_maison.dernier_sync_erreur, '')

    def test_rejouer_ne_duplique_pas_et_garde_le_revenu_saisi(self):
        self.flux[URL_MAISON] = ics(evenement('a@airbnb.com', f'{FUTUR}0710', f'{FUTUR}0715'))
        self.synchroniser()
        Reservation.objects.filter(uid_externe='a@airbnb.com').update(
            montant_revenu=Decimal('420.00'), date_paiement=datetime.date(2026, 1, 5), notes='Famille Martin')
        # Airbnb décale le séjour d'un jour.
        self.flux[URL_MAISON] = ics(evenement('a@airbnb.com', f'{FUTUR}0711', f'{FUTUR}0716'))
        self.synchroniser()
        a = Reservation.objects.get()
        self.assertEqual(a.date_debut, datetime.date(FUTUR, 7, 11))
        self.assertEqual((a.montant_revenu, a.date_paiement, a.notes),
                         (Decimal('420.00'), datetime.date(2026, 1, 5), 'Famille Martin'))

    def test_annulation_explicite(self):
        self.flux[URL_MAISON] = ics(evenement('a@airbnb.com', f'{FUTUR}0710', f'{FUTUR}0715', 'STATUS:CANCELLED'))
        self.synchroniser()
        self.assertEqual(self.statuts(), {'a@airbnb.com': 'annulee'})

    def test_sejour_a_venir_disparu_du_flux_annule_le_passe_conserve(self):
        def res(uid, annee, appartement=None, source='airbnb'):
            return Reservation.objects.create(
                appartement=appartement or self.appt_maison, source=source, uid_externe=uid,
                date_debut=datetime.date(annee, 7, 1), date_fin=datetime.date(annee, 7, 5))

        res('reste', FUTUR)
        res('disparu', FUTUR)
        res('ancien', PASSE)                                 # séjour passé : sorti du flux, normal
        res('', FUTUR, source='direct')                      # saisie manuelle : jamais touchée
        res('autre-appartement', FUTUR, appartement=self.appt_chalet)
        self.flux[URL_MAISON] = ics(evenement('reste', f'{FUTUR}0701', f'{FUTUR}0705'))
        self.synchroniser()
        self.assertEqual(self.statuts(), {'reste': 'confirmee', 'disparu': 'annulee',
                                          'ancien': 'confirmee', '': 'confirmee'})
        self.assertEqual(self.statuts(self.appt_chalet), {'autre-appartement': 'confirmee'})

    def test_un_sejour_reapparu_redevient_confirme(self):
        Reservation.objects.create(appartement=self.appt_maison, source='airbnb', uid_externe='a',
                                   statut='annulee', date_debut=datetime.date(FUTUR, 7, 1),
                                   date_fin=datetime.date(FUTUR, 7, 5))
        self.flux[URL_MAISON] = ics(evenement('a', f'{FUTUR}0701', f'{FUTUR}0705'))
        self.synchroniser()
        self.assertEqual(self.statuts(), {'a': 'confirmee'})

    def test_flux_en_panne_rien_n_est_annule_et_l_erreur_est_notee(self):
        Reservation.objects.create(appartement=self.appt_maison, source='airbnb', uid_externe='a',
                                   date_debut=datetime.date(FUTUR, 7, 1), date_fin=datetime.date(FUTUR, 7, 5))
        pannes = {
            'réseau': requests.ConnectionError('connexion refusée'),
            'page html': b'<html><body>Service unavailable</body></html>',
        }
        for nom, panne in pannes.items():
            with self.subTest(panne=nom):
                self.flux[URL_MAISON] = panne
                sortie, erreurs = self.synchroniser()
                self.assertIn('échec sync', erreurs)
                self.assertEqual(self.statuts(), {'a': 'confirmee'})
                self.appt_maison.refresh_from_db()
                self.assertNotEqual(self.appt_maison.dernier_sync_erreur, '')
        # Erreur HTTP (404 : lien iCal révoqué).
        self.flux[URL_MAISON] = ics()
        self.http.side_effect = lambda url, **kw: mock.Mock(
            status_code=404, headers={}, content=b'', raise_for_status=mock.Mock(side_effect=requests.HTTPError('404 Not Found')))
        _, erreurs = self.synchroniser()
        self.assertIn('404', erreurs)
        self.assertEqual(self.statuts(), {'a': 'confirmee'})

    def test_la_panne_d_un_flux_n_empeche_pas_les_autres(self):
        Appartement.objects.filter(pk=self.appt_chalet.pk).update(airbnb_ical_url=URL_CHALET)
        self.flux[URL_MAISON] = requests.Timeout('trop long')
        self.flux[URL_CHALET] = ics(evenement('c', f'{FUTUR}0701', f'{FUTUR}0705'))
        sortie, erreurs = self.synchroniser()
        self.assertIn('échec sync', erreurs)
        self.assertIn('sync OK', sortie)
        self.assertEqual(self.statuts(self.appt_chalet), {'c': 'confirmee'})
        # Une réussite efface l'erreur précédente.
        self.flux[URL_MAISON] = ics()
        self.synchroniser()
        self.appt_maison.refresh_from_db()
        self.assertEqual(self.appt_maison.dernier_sync_erreur, '')

    def test_le_meme_uid_dans_deux_appartements_reste_distinct(self):
        Appartement.objects.filter(pk=self.appt_chalet.pk).update(airbnb_ical_url=URL_CHALET)
        self.flux[URL_MAISON] = ics(evenement('meme-uid', f'{FUTUR}0701', f'{FUTUR}0705'))
        self.flux[URL_CHALET] = ics(evenement('meme-uid', f'{FUTUR}0801', f'{FUTUR}0805'))
        self.synchroniser()
        self.synchroniser()
        self.assertEqual(Reservation.objects.count(), 2)
        self.assertEqual(Reservation.objects.get(appartement=self.appt_chalet).date_debut,
                         datetime.date(FUTUR, 8, 1))

    def test_sans_url_configuree_aucun_appel(self):
        Appartement.objects.update(airbnb_ical_url='')
        sortie, _ = self.synchroniser()
        self.assertIn('Aucun appartement', sortie)
        self.http.assert_not_called()

    def test_declenchement_par_l_api_reserve_au_gestionnaire(self):
        self.flux[URL_MAISON] = ics(evenement('a', f'{FUTUR}0701', f'{FUTUR}0705'))
        self.assertEqual(self.alice.post('/api/sync/airbnb/').status_code, 403)
        self.http.assert_not_called()
        resp = self.gina.post('/api/sync/airbnb/')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('sync OK', resp.json()['log'])
        self.assertEqual(resp.json()['erreurs'], '')
        self.assertEqual(Reservation.objects.count(), 1)

    def test_l_url_ical_secrete_n_est_pas_modifiable_par_un_proprietaire(self):
        url = f'/api/appartements/{self.appt_maison.pk}/'
        resp = self.alice.patch(url, {'airbnb_ical_url': 'http://storage-backend:8000/api/'}, format='json')
        self.assertEqual(resp.status_code, 403)
        # Les champs d'état de la synchro ne s'écrivent pas par l'API.
        self.gina.patch(url, {'dernier_sync_erreur': 'forgé', 'nom': 'RDC bis'}, format='json')
        self.appt_maison.refresh_from_db()
        self.assertEqual((self.appt_maison.nom, self.appt_maison.dernier_sync_erreur), ('RDC bis', ''))

    def test_un_sejour_termine_la_veille_n_est_pas_annule(self):
        # « Aujourd'hui » est la date de Paris (TIME_ZONE). Avec le fuseau par
        # défaut de Django (Chicago), entre minuit et 7 h c'était encore la
        # veille : un séjour terminé la veille et déjà sorti du flux était pris
        # pour un séjour à venir disparu, marqué annulé, et son revenu sortait
        # du bilan.
        Reservation.objects.create(appartement=self.appt_maison, source='airbnb', uid_externe='hier',
                                   date_debut=datetime.date(2026, 6, 27), date_fin=datetime.date(2026, 7, 1),
                                   montant_revenu=Decimal('600'), date_paiement=datetime.date(2026, 6, 28))
        self.flux[URL_MAISON] = ics()
        # 2 juillet, 3 h à Paris (1 h UTC).
        instant = datetime.datetime(2026, 7, 2, 1, 0, tzinfo=datetime.timezone.utc)
        with mock.patch('django.utils.timezone.now', return_value=instant):
            self.synchroniser()
        self.assertEqual(self.statuts(), {'hier': 'confirmee'})

    def test_un_evenement_date_en_heure_utc_prend_la_date_de_paris(self):
        self.flux[URL_MAISON] = ics(['UID:nuit', f'DTSTART:{FUTUR}0701T223000Z', f'DTEND:{FUTUR}0705T080000Z'])
        self.synchroniser()
        r = Reservation.objects.get(uid_externe='nuit')
        # 22 h 30 UTC le 1er = 0 h 30 le 2 à Paris ; 8 h UTC le 5 = 10 h le 5.
        self.assertEqual((r.date_debut, r.date_fin), (datetime.date(FUTUR, 7, 2), datetime.date(FUTUR, 7, 5)))

    def test_le_dossier_d_une_facture_ne_depend_pas_du_fuseau(self):
        # Seul autre usage d'une date dans l'app : le dossier AAAA/MM d'une
        # facture, pris sur l'instant UTC.
        from api import views
        instant = datetime.datetime(2026, 12, 31, 23, 30, tzinfo=datetime.timezone.utc)
        fichier = mock.Mock(content_type='application/pdf')
        fichier.name = 'f.pdf'
        with mock.patch('django.utils.timezone.now', return_value=instant):
            chemin = views._upload_facture(self.maison.pk, fichier)
        self.assertTrue(chemin.startswith('factures/2026/12/'), chemin)
