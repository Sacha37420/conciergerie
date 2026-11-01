"""Lot 2 — confidentialité entre co-propriétaires (décision du propriétaire,
chantier « failles », 2026-10-31) :
- `airbnb_ical_url` (lien secret) : au gestionnaire seul ;
- `notes` du gestionnaire sur les co-propriétaires : au gestionnaire seul ;
- contacts (nom, email, téléphone) : visibles entre co-propriétaires d'un
  MÊME bien, jamais ceux d'un autre bien.
Monde : « Maison » (Alice, Bob), « Chalet » (Chloé), gestionnaire Gina."""
from decimal import Decimal
from io import StringIO
from unittest import mock

import requests
from django.core.management import call_command

from api import ical_fetch
from api.models import Appartement, Frais, Proprietaire, Tache
from .base import Base

URL = 'https://www.airbnb.fr/calendar/ical/111.ics?s=secret-maison'
NOTE = 'Note privée du gestionnaire'


class ConfidentialiteTests(Base):

    def setUp(self):
        super().setUp()
        self.monde()
        for p, tel in ((self.p_alice, '0611111111'), (self.p_bob, '0622222222'), (self.p_chloe, '0633333333')):
            Proprietaire.objects.filter(pk=p.pk).update(telephone=tel, notes=f'{NOTE} sur {p.nom}')
        Appartement.objects.filter(pk=self.appt_maison.pk).update(airbnb_ical_url=URL)

    # ── outils ──
    def json(self, client, url):
        resp = client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        return resp.json()

    def assertSansSecret(self, donnees):
        texte = str(donnees)
        self.assertNotIn('secret-maison', texte)
        self.assertNotIn('airbnb_ical_url', texte)
        self.assertNotIn(NOTE, texte)
        self.assertNotIn("'notes': 'Note", texte)

    def fiche(self, p):
        return {'id': p.pk, 'nom': p.nom, 'email': p.email, 'telephone': Proprietaire.objects.get(pk=p.pk).telephone}

    # ── URL iCal ──
    def test_co_proprietaire_ne_recoit_pas_l_url_ical(self):
        for url in ('/api/biens/', f'/api/biens/{self.maison.pk}/', '/api/appartements/',
                    f'/api/appartements/{self.appt_maison.pk}/', f'/api/appartements/?bien={self.maison.pk}'):
            with self.subTest(url=url):
                donnees = self.json(self.alice, url)
                self.assertSansSecret(donnees)
        appt = self.json(self.alice, f'/api/appartements/{self.appt_maison.pk}/')
        self.assertIs(appt['airbnb_ical_configure'], True)
        self.assertNotIn('airbnb_ical_url', appt)
        autre = self.json(self.bob, '/api/biens/')[0]['appartements'][0]
        self.assertEqual((autre['airbnb_ical_configure'], 'airbnb_ical_url' in autre), (True, False))

    def test_gestionnaire_recoit_l_url_ical(self):
        appt = self.json(self.gina, f'/api/appartements/{self.appt_maison.pk}/')
        self.assertEqual((appt['airbnb_ical_url'], appt['airbnb_ical_configure']), (URL, True))
        bien = self.json(self.gina, f'/api/biens/{self.maison.pk}/')
        self.assertEqual(bien['appartements'][0]['airbnb_ical_url'], URL)
        chalet = self.json(self.gina, f'/api/appartements/{self.appt_chalet.pk}/')
        self.assertEqual((chalet['airbnb_ical_url'], chalet['airbnb_ical_configure']), ('', False))

    def test_erreur_de_synchro_ancienne_qui_recopie_l_url(self):
        # Erreur enregistrée avant le lot 4, au format de requests.
        Appartement.objects.filter(pk=self.appt_maison.pk).update(
            dernier_sync_erreur="404 Client Error: Not Found for url: " + URL)
        appt = self.json(self.alice, f'/api/appartements/{self.appt_maison.pk}/')
        self.assertEqual(appt['dernier_sync_erreur'], 'Échec de la synchronisation.')
        Appartement.objects.filter(pk=self.appt_maison.pk).update(
            dernier_sync_erreur="HTTPSConnectionPool(host='www.airbnb.fr', port=443): Max retries exceeded "
                                "with url: /calendar/ical/111.ics?s=secret-maison")
        self.assertSansSecret(self.json(self.alice, '/api/biens/'))
        # Le gestionnaire garde le texte tel quel ; une erreur sans l'URL reste lisible.
        self.assertIn('secret-maison', self.json(self.gina, f'/api/appartements/{self.appt_maison.pk}/')
                      ['dernier_sync_erreur'])
        Appartement.objects.filter(pk=self.appt_maison.pk).update(dernier_sync_erreur='Délai dépassé.')
        self.assertEqual(self.json(self.alice, f'/api/appartements/{self.appt_maison.pk}/')
                         ['dernier_sync_erreur'], 'Délai dépassé.')

    def test_la_synchro_n_enregistre_plus_l_url_dans_l_erreur(self):
        self.dns = {}
        self._patch(mock.patch.object(ical_fetch, '_resoudre', lambda h, p: ['34.120.10.20']))
        pannes = [
            requests.ConnectionError(f"HTTPSConnectionPool(host='www.airbnb.fr', port=443): "
                                     f"Max retries exceeded with url: /calendar/ical/111.ics?s=secret-maison"),
            requests.HTTPError(f'404 Client Error: Not Found for url: {URL}'),
            requests.Timeout(f'Read timed out. (url: {URL})'),
            ValueError(f'parse error near {URL}'),
        ]
        for panne in pannes:
            with self.subTest(panne=type(panne).__name__):
                self._patch(mock.patch.object(ical_fetch, '_get', mock.Mock(side_effect=panne)))
                err = StringIO()
                call_command('sync_airbnb_calendars', stdout=StringIO(), stderr=err)
                self.appt_maison.refresh_from_db()
                self.assertNotEqual(self.appt_maison.dernier_sync_erreur, '')
                self.assertNotIn('secret-maison', self.appt_maison.dernier_sync_erreur)
                self.assertNotIn('secret-maison', err.getvalue())
                self.assertIn('échec sync', err.getvalue())

    # ── notes et contacts ──
    def test_co_proprietaire_voit_les_contacts_de_son_bien_sans_les_notes(self):
        parts = self.json(self.alice, f'/api/biens/{self.maison.pk}/')['parts']
        self.assertEqual(sorted((p['proprietaire_detail'] for p in parts), key=lambda d: d['id']),
                         [self.fiche(self.p_alice), self.fiche(self.p_bob)])
        for url in ('/api/biens/', '/api/parts-proprietaire/', f'/api/parts-proprietaire/?bien={self.maison.pk}'):
            with self.subTest(url=url):
                self.assertSansSecret(self.json(self.alice, url))
        part_bob = self.json(self.alice, '/api/parts-proprietaire/')
        self.assertIn(self.fiche(self.p_bob), [p['proprietaire_detail'] for p in part_bob])
        # Chloé (autre bien) ne voit rien d'Alice ni de Bob.
        texte = str(self.json(self.chloe, '/api/biens/')) + str(self.json(self.chloe, '/api/parts-proprietaire/'))
        for fuite in ('alice@', 'bob@', '0611111111', '0622222222', 'Alice', 'Bob'):
            self.assertNotIn(fuite, texte)

    def test_me_ne_rend_pas_les_notes_meme_sur_sa_propre_fiche(self):
        me = self.json(self.alice, '/api/me/')
        self.assertEqual(me['proprietaire'], self.fiche(self.p_alice))
        self.assertSansSecret(me)

    def test_gestionnaire_recoit_notes_et_contacts(self):
        parts = self.json(self.gina, f'/api/biens/{self.maison.pk}/')['parts']
        self.assertEqual({p['proprietaire_detail']['notes'] for p in parts},
                         {f'{NOTE} sur Alice', f'{NOTE} sur Bob'})
        tous = self.json(self.gina, '/api/proprietaires/')
        self.assertEqual(len(tous), 3)
        self.assertTrue(all(p['notes'].startswith(NOTE) for p in tous))
        self.assertEqual(self.alice.get('/api/proprietaires/').status_code, 403)
        self.assertEqual(self.alice.get(f'/api/proprietaires/{self.p_bob.pk}/').status_code, 403)

    def test_responsable_et_payeur_d_un_autre_bien_jamais_rendus(self):
        # Le gestionnaire a mis Chloé (Chalet) responsable d'une tâche et payeuse
        # d'un frais de la Maison : Alice voit la tâche, pas les contacts de Chloé.
        tache = Tache.objects.create(bien=self.maison, titre='Toiture', proprietaire_responsable=self.p_chloe)
        Frais.objects.create(tache=tache, libelle='Tuiles', montant_fixe=Decimal('10'), payeur='proprietaire',
                             proprietaire_payeur=self.p_chloe)
        Frais.objects.create(tache=tache, libelle='Clous', montant_fixe=Decimal('2'), payeur='proprietaire',
                             proprietaire_payeur=self.p_bob)
        for url in ('/api/taches/', f'/api/taches/{tache.pk}/', '/api/frais/', f'/api/taches/?bien={self.maison.pk}'):
            with self.subTest(url=url):
                texte = str(self.json(self.alice, url))
                for fuite in ('chloe@', '0633333333', 'Chloé', NOTE):
                    self.assertNotIn(fuite, texte)
        t = self.json(self.alice, f'/api/taches/{tache.pk}/')
        self.assertIsNone(t['proprietaire_responsable_detail'])
        self.assertEqual(t['proprietaire_responsable'], self.p_chloe.pk)
        payeurs = {f['libelle']: f['proprietaire_payeur_detail'] for f in t['frais']}
        self.assertEqual(payeurs, {'Tuiles': None, 'Clous': self.fiche(self.p_bob)})
        # Le gestionnaire voit tout.
        t = self.json(self.gina, f'/api/taches/{tache.pk}/')
        self.assertEqual(t['proprietaire_responsable_detail']['notes'], f'{NOTE} sur Chloé')
        self.assertEqual({f['libelle']: f['proprietaire_payeur_detail']['email'] for f in t['frais']},
                         {'Tuiles': 'chloe@ssolab.local', 'Clous': 'bob@ssolab.local'})

    def test_co_proprietaire_de_deux_biens_voit_les_deux_groupes(self):
        # Alice entre au Chalet : Chloé devient une co-propriétaire, ses contacts
        # (pas ses notes) deviennent visibles à Alice, partout.
        from api.models import PartProprietaire
        PartProprietaire.objects.create(bien=self.chalet, proprietaire=self.p_alice)
        tache = Tache.objects.create(bien=self.maison, titre='Toiture', proprietaire_responsable=self.p_chloe)
        t = self.json(self.alice, f'/api/taches/{tache.pk}/')
        self.assertEqual(t['proprietaire_responsable_detail'], self.fiche(self.p_chloe))
        # Bob, lui, n'a toujours aucun bien commun avec Chloé.
        self.assertIsNone(self.json(self.bob, f'/api/taches/{tache.pk}/')['proprietaire_responsable_detail'])

    def test_bilan_et_parts_de_sejour_restent_individuels(self):
        bilan = self.json(self.alice, f'/api/biens/{self.maison.pk}/bilan/')
        self.assertEqual([l['proprietaire_id'] for l in bilan['proprietaires']], [self.p_alice.pk])
        self.assertSansSecret(bilan)
