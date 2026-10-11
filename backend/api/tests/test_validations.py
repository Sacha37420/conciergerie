"""Validations portées par les sérialiseurs (appelées par le gestionnaire)."""
from decimal import Decimal

from django.test import override_settings

from api.models import Bien, Frais, PartProprietaire, Reservation, Tache
from .base import Base


class ValidationTests(Base):

    def setUp(self):
        super().setUp()
        self.monde()

    def refuse(self, resp, fragment):
        self.assertEqual(resp.status_code, 400, resp.content)
        self.assertIn(fragment, str(resp.json()))

    # ── Réservation : revenu et date d'encaissement vont ensemble ────────────

    def _reservation(self, **champs):
        return self.gina.post('/api/reservations/', {
            'appartement': self.appt_maison.pk, 'source': 'direct',
            'date_debut': '2026-05-01', 'date_fin': '2026-05-04', **champs}, format='json')

    def test_reservation_revenu_et_date_d_encaissement_ensemble(self):
        self.refuse(self._reservation(montant_revenu='300.00'), 'renseignés ensemble')
        self.refuse(self._reservation(date_paiement='2026-05-05'), 'renseignés ensemble')
        self.assertEqual(self._reservation().status_code, 201)
        resp = self._reservation(montant_revenu='300.00', date_paiement='2026-05-05')
        self.assertEqual(resp.status_code, 201)
        url = f"/api/reservations/{resp.json()['id']}/"
        # Modification partielle : on ne peut pas retirer l'un sans l'autre.
        self.refuse(self.gina.patch(url, {'date_paiement': None}, format='json'), 'renseignés ensemble')
        self.refuse(self.gina.patch(url, {'montant_revenu': None}, format='json'), 'renseignés ensemble')
        self.assertEqual(self.gina.patch(url, {'montant_revenu': None, 'date_paiement': None},
                                         format='json').status_code, 200)

    def test_reservation_l_uid_externe_ne_s_ecrit_pas_par_l_api(self):
        # Clé d'upsert de la synchronisation iCal : l'écrire à la main ferait
        # écraser ou annuler la réservation par le prochain passage.
        resp = self._reservation(uid_externe='abc@airbnb.com')
        self.assertEqual(Reservation.objects.get(pk=resp.json()['id']).uid_externe, '')

    # ── Tâche ───────────────────────────────────────────────────────────────

    def _tache(self, **champs):
        return self.gina.post('/api/taches/', {'bien': self.maison.pk, 'titre': 'T', **champs}, format='json')

    def test_tache_un_seul_responsable(self):
        ent = self.gina.post('/api/entreprises/', {'nom': 'Plombier'}, format='json').json()['id']
        self.refuse(self._tache(proprietaire_responsable=self.p_alice.pk, entreprise_responsable=ent),
                    'pas les deux')
        resp = self._tache(entreprise_responsable=ent)
        self.assertEqual(resp.status_code, 201)
        self.refuse(self.gina.patch(f"/api/taches/{resp.json()['id']}/",
                                    {'proprietaire_responsable': self.p_alice.pk}, format='json'), 'pas les deux')

    def test_tache_l_appartement_doit_etre_dans_le_bien(self):
        self.refuse(self._tache(appartement=self.appt_chalet.pk), 'appartenir au bien')
        self.assertEqual(self._tache(appartement=self.appt_maison.pk).status_code, 201)

    def test_tache_le_temps_d_un_proprietaire_exige_une_date_de_realisation(self):
        self.refuse(self._tache(proprietaire_responsable=self.p_alice.pk, duree_heures='3'),
                    'date de réalisation')
        self.assertEqual(self._tache(proprietaire_responsable=self.p_alice.pk, duree_heures='3',
                                     date_paiement='2026-05-01').status_code, 201)
        # Sans durée, ou pour une entreprise, rien à valoriser : pas de date exigée.
        self.assertEqual(self._tache(proprietaire_responsable=self.p_alice.pk).status_code, 201)
        self.assertEqual(self._tache(duree_heures='3').status_code, 201)

    def test_tache_cout_total_frais_plus_temps_valorise(self):
        tache = Tache.objects.create(bien=self.maison, titre='Peinture', duree_heures=Decimal('4'),
                                     proprietaire_responsable=self.p_alice, date_paiement='2026-05-01')
        Frais.objects.create(tache=tache, libelle='Pots', montant_fixe=Decimal('30'))
        Frais.objects.create(tache=tache, libelle='Location', taux_horaire=Decimal('2.50'))
        donnees = self.gina.get(f'/api/taches/{tache.pk}/').json()
        # frais : 30 + 2,50 × 4 = 40 ; temps : 4 h × 6 €/h = 24.
        self.assertEqual(sorted(Decimal(f['montant_total']) for f in donnees['frais']),
                         [Decimal('10'), Decimal('30')])
        self.assertEqual(Decimal(str(donnees['cout_total'])), Decimal('64'))
        # Une entreprise n'est pas valorisée au tarif propriétaire.
        tache.proprietaire_responsable = None
        tache.save()
        self.assertEqual(Decimal(str(self.gina.get(f'/api/taches/{tache.pk}/').json()['cout_total'])),
                         Decimal('40'))

    # ── Frais ───────────────────────────────────────────────────────────────

    def test_frais_coherence_payeur(self):
        tache = Tache.objects.create(bien=self.maison, titre='T')
        base = {'tache': tache.pk, 'libelle': 'L', 'montant_fixe': '10'}
        self.refuse(self.gina.post('/api/frais/', {**base, 'payeur': 'proprietaire'}, format='json'),
                    'propriétaire payeur est requis')
        self.refuse(self.gina.post('/api/frais/', {**base, 'payeur': 'maison',
                                                   'proprietaire_payeur': self.p_alice.pk}, format='json'),
                    'payé par la maison')
        # payeur omis ⇒ maison : même règle.
        self.refuse(self.gina.post('/api/frais/', {**base, 'proprietaire_payeur': self.p_alice.pk},
                                   format='json'), 'payé par la maison')
        self.assertEqual(Frais.objects.count(), 0)
        self.storage.upload.assert_not_called()

    # ── Remboursement ───────────────────────────────────────────────────────

    def test_remboursement_seulement_des_frais_avances_par_ce_proprietaire(self):
        tache = Tache.objects.create(bien=self.maison, titre='T')
        d_alice = Frais.objects.create(tache=tache, libelle='Avance Alice', montant_fixe=10,
                                       payeur='proprietaire', proprietaire_payeur=self.p_alice)
        d_bob = Frais.objects.create(tache=tache, libelle='Avance Bob', montant_fixe=10,
                                     payeur='proprietaire', proprietaire_payeur=self.p_bob)
        maison = Frais.objects.create(tache=tache, libelle='Payé maison', montant_fixe=10)

        def rembourser(*frais):
            return self.gina.post('/api/remboursements/', {
                'proprietaire': self.p_alice.pk, 'frais': [f.pk for f in frais], 'montant': '10.00',
                'date_versement': '2026-05-01'}, format='json')

        self.refuse(rembourser(d_bob), 'Avance Bob')
        self.refuse(rembourser(maison), 'Payé maison')
        self.refuse(rembourser(d_alice, d_bob), 'Avance Bob')
        self.assertFalse(d_alice.est_rembourse)
        resp = rembourser(d_alice)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['created_by'], 'gina@ssolab.local')
        self.assertTrue(d_alice.est_rembourse)
        self.assertFalse(d_bob.est_rembourse)

    # ── Parts et biens ──────────────────────────────────────────────────────

    def test_une_seule_part_par_proprietaire_et_par_bien(self):
        resp = self.gina.post('/api/parts-proprietaire/',
                              {'bien': self.maison.pk, 'proprietaire': self.p_alice.pk}, format='json')
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(PartProprietaire.objects.filter(bien=self.maison).count(), 2)
        self.storage.add_member.assert_not_called()

    def test_deux_proprietaires_ne_partagent_pas_un_email(self):
        # L'email EST l'identité du portail : un doublon donnerait à un
        # compte les biens d'un autre.
        resp = self.gina.post('/api/proprietaires/', {'nom': 'Faux', 'email': 'alice@ssolab.local'},
                              format='json')
        self.assertEqual(resp.status_code, 400)
        # Plusieurs fiches sans email restent possibles (NULL, pas '').
        for nom in ('A', 'B'):
            self.assertEqual(self.gina.post('/api/proprietaires/', {'nom': nom}, format='json').status_code, 201)

    def test_le_tarif_horaire_d_un_bien_est_fige_a_sa_creation(self):
        # SMIC/2 du moment ; une revalorisation ne réécrit pas les biens existants.
        self.assertEqual(self.maison.valorisation_heure_proprietaire, Decimal('6.00'))
        with override_settings(VALORISATION_HEURE_PROPRIETAIRE=Decimal('7.25')):
            resp = self.gina.post('/api/biens/', {'nom': 'Studio'}, format='json')
            self.assertEqual(Decimal(resp.json()['valorisation_heure_proprietaire']), Decimal('7.25'))
            self.maison.refresh_from_db()
            self.assertEqual(self.maison.valorisation_heure_proprietaire, Decimal('6.00'))
        self.assertEqual(Bien.objects.get(nom='Studio').valorisation_heure_proprietaire, Decimal('7.25'))
