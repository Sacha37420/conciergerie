"""Qui lit et écrit quoi : gestionnaire (`admins`) et co-propriétaires
(`proprietaires`, cloisonnés par bien via PartProprietaire et l'email du jeton)."""
import datetime
import unittest
from decimal import Decimal

from api.models import (
    ApportInitial, Bien, Entreprise, Frais, PartProprietaire, Proprietaire,
    Remboursement, Reservation, Tache, VersementRevenu,
)
from .base import Base

J = datetime.date(2026, 3, 1)


class CloisonnementTests(Base):

    def setUp(self):
        super().setUp()
        self.monde()
        self.res_maison = Reservation.objects.create(appartement=self.appt_maison, source='direct',
                                                     date_debut=J, date_fin=J)
        self.res_chalet = Reservation.objects.create(appartement=self.appt_chalet, source='direct',
                                                     date_debut=J, date_fin=J)
        self.tache_maison = Tache.objects.create(bien=self.maison, titre='Ménage maison')
        self.tache_chalet = Tache.objects.create(bien=self.chalet, titre='Ménage chalet')
        self.frais_maison = Frais.objects.create(tache=self.tache_maison, libelle='Produits', montant_fixe=10)
        self.frais_chalet = Frais.objects.create(tache=self.tache_chalet, libelle='Bois', montant_fixe=20)
        self.apport_bob = ApportInitial.objects.create(bien=self.maison, proprietaire=self.p_bob,
                                                       montant=1000, date=J)
        self.apport_chloe = ApportInitial.objects.create(bien=self.chalet, proprietaire=self.p_chloe,
                                                         montant=1000, date=J)
        self.remb = {p.nom: Remboursement.objects.create(proprietaire=p, montant=5, date_versement=J)
                     for p in (self.p_alice, self.p_bob)}
        self.vers = {p.nom: VersementRevenu.objects.create(bien=self.maison, proprietaire=p, montant=7,
                                                           date_versement=J)
                     for p in (self.p_alice, self.p_bob)}

    def ids(self, client, chemin):
        resp = client.get(chemin)
        self.assertEqual(resp.status_code, 200, chemin)
        return sorted(o['id'] for o in resp.json())

    def test_le_gestionnaire_voit_tout(self):
        self.assertEqual(self.ids(self.gina, '/api/biens/'), sorted([self.maison.pk, self.chalet.pk]))
        self.assertEqual(len(self.ids(self.gina, '/api/frais/')), 2)
        self.assertEqual(len(self.ids(self.gina, '/api/remboursements/')), 2)
        self.assertEqual(len(self.ids(self.gina, '/api/proprietaires/')), 3)

    def test_un_proprietaire_ne_voit_que_ses_biens(self):
        attendu = {
            '/api/biens/': [self.maison.pk],
            '/api/appartements/': [self.appt_maison.pk],
            '/api/reservations/': [self.res_maison.pk],
            '/api/taches/': [self.tache_maison.pk],
            '/api/frais/': [self.frais_maison.pk],
            '/api/apports-initiaux/': [self.apport_bob.pk],      # transparence entre co-propriétaires
        }
        for chemin, ids in attendu.items():
            with self.subTest(chemin=chemin):
                self.assertEqual(self.ids(self.alice, chemin), ids)
        parts = self.alice.get('/api/parts-proprietaire/').json()
        self.assertEqual({p['bien'] for p in parts}, {self.maison.pk})
        self.assertEqual(len(parts), 2)

    def test_les_filtres_d_url_n_elargissent_pas_la_vue(self):
        for chemin in (f'/api/appartements/?bien={self.chalet.pk}',
                       f'/api/reservations/?bien={self.chalet.pk}',
                       f'/api/reservations/?appartement={self.appt_chalet.pk}',
                       f'/api/taches/?bien={self.chalet.pk}',
                       f'/api/frais/?tache={self.tache_chalet.pk}',
                       f'/api/parts-proprietaire/?bien={self.chalet.pk}',
                       f'/api/apports-initiaux/?bien={self.chalet.pk}',
                       f'/api/remboursements/?proprietaire={self.p_bob.pk}',
                       f'/api/versements-revenu/?bien={self.chalet.pk}'):
            with self.subTest(chemin=chemin):
                self.assertEqual(self.ids(self.alice, chemin), [])
        # … et restent des filtres pour le gestionnaire.
        self.assertEqual(self.ids(self.gina, f'/api/taches/?bien={self.chalet.pk}'), [self.tache_chalet.pk])
        self.assertEqual(self.ids(self.gina, f'/api/remboursements/?proprietaire={self.p_bob.pk}'),
                         [self.remb['Bob'].pk])

    def test_acces_direct_a_l_objet_d_un_autre_bien_introuvable(self):
        for chemin in (f'/api/biens/{self.chalet.pk}/',
                       f'/api/appartements/{self.appt_chalet.pk}/',
                       f'/api/reservations/{self.res_chalet.pk}/',
                       f'/api/taches/{self.tache_chalet.pk}/', f'/api/frais/{self.frais_chalet.pk}/',
                       f'/api/frais/{self.frais_chalet.pk}/facture/',
                       f'/api/apports-initiaux/{self.apport_chloe.pk}/'):
            with self.subTest(chemin=chemin):
                self.assertEqual(self.alice.get(chemin).status_code, 404)
        self.assertEqual(self.alice.get(f'/api/biens/{self.maison.pk}/').status_code, 200)
        self.assertIn(self.alice.get(f'/api/biens/{self.chalet.pk}/bilan/').status_code, (403, 404))

    def test_versements_et_remboursements_strictement_personnels(self):
        # Même entre co-propriétaires du même bien.
        self.assertEqual(self.ids(self.alice, '/api/remboursements/'), [self.remb['Alice'].pk])
        self.assertEqual(self.ids(self.alice, '/api/versements-revenu/'), [self.vers['Alice'].pk])
        self.assertEqual(self.alice.get(f"/api/remboursements/{self.remb['Bob'].pk}/").status_code, 404)
        self.assertEqual(self.alice.get(f"/api/versements-revenu/{self.vers['Bob'].pk}/").status_code, 404)

    def test_le_rattachement_se_fait_par_l_email_sans_casse(self):
        alice = self.client_de('alice', email='Alice@SSOLab.Local')
        self.assertEqual(self.ids(alice, '/api/biens/'), [self.maison.pk])
        self.assertEqual(alice.get('/api/me/').json()['proprietaire']['id'], self.p_alice.pk)
        self.assertEqual(self.ids(alice, '/api/remboursements/'), [self.remb['Alice'].pk])

    def test_membre_du_groupe_sans_fiche_ne_voit_rien(self):
        dan = self.client_de('dan')
        for chemin in ('/api/biens/', '/api/appartements/', '/api/reservations/', '/api/taches/',
                       '/api/frais/', '/api/parts-proprietaire/', '/api/apports-initiaux/',
                       '/api/remboursements/', '/api/versements-revenu/'):
            with self.subTest(chemin=chemin):
                self.assertEqual(self.ids(dan, chemin), [])
        self.assertIsNone(dan.get('/api/me/').json()['proprietaire'])

    def test_fiche_sans_email_ne_se_rattache_a_personne(self):
        sans = Proprietaire.objects.create(nom='Sans compte')
        PartProprietaire.objects.create(bien=self.chalet, proprietaire=sans)
        self.assertEqual(self.ids(self.client_de('dan'), '/api/biens/'), [])

    def test_retirer_la_part_retire_l_acces(self):
        PartProprietaire.objects.filter(bien=self.maison, proprietaire=self.p_alice).delete()
        self.assertEqual(self.ids(self.alice, '/api/biens/'), [])
        self.assertEqual(self.alice.get(f'/api/biens/{self.maison.pk}/').status_code, 404)

    def test_annuaires_reserves_au_gestionnaire(self):
        Entreprise.objects.create(nom='Plombier')
        for chemin in ('/api/proprietaires/', f'/api/proprietaires/{self.p_alice.pk}/', '/api/entreprises/'):
            with self.subTest(chemin=chemin):
                self.assertEqual(self.alice.get(chemin).status_code, 403)
                self.assertEqual(self.gina.get(chemin).status_code, 200)

    def test_un_proprietaire_n_ecrit_rien_hors_ses_frais(self):
        corps = {
            'proprietaires': {'nom': 'Moi', 'email': 'x@ssolab.local'},
            'entreprises': {'nom': 'E'},
            'biens': {'nom': 'Nouveau'},
            'parts-proprietaire': {'bien': self.chalet.pk, 'proprietaire': self.p_alice.pk},
            'appartements': {'bien': self.maison.pk, 'nom': 'Cave'},
            'reservations': {'appartement': self.appt_maison.pk, 'source': 'direct',
                             'date_debut': '2026-05-01', 'date_fin': '2026-05-02'},
            'taches': {'bien': self.maison.pk, 'titre': 'T'},
            'remboursements': {'proprietaire': self.p_alice.pk, 'montant': '999', 'date_versement': '2026-05-01'},
            'apports-initiaux': {'bien': self.maison.pk, 'proprietaire': self.p_alice.pk,
                                 'montant': '999999', 'date': '2026-05-01'},
            'versements-revenu': {'bien': self.maison.pk, 'proprietaire': self.p_alice.pk,
                                  'montant': '999', 'date_versement': '2026-05-01'},
        }
        existants = {
            'proprietaires': self.p_alice.pk, 'biens': self.maison.pk,
            'parts-proprietaire': PartProprietaire.objects.get(bien=self.maison, proprietaire=self.p_bob).pk,
            'appartements': self.appt_maison.pk, 'reservations': self.res_maison.pk,
            'taches': self.tache_maison.pk, 'remboursements': self.remb['Alice'].pk,
            'apports-initiaux': self.apport_bob.pk, 'versements-revenu': self.vers['Alice'].pk,
        }
        avant = {m: m.objects.count() for m in (Proprietaire, Entreprise, Bien, PartProprietaire, Reservation,
                                                Tache, Remboursement, ApportInitial, VersementRevenu)}
        for ressource, donnees in corps.items():
            with self.subTest(creation=ressource):
                self.assertEqual(self.alice.post(f'/api/{ressource}/', donnees, format='json').status_code, 403)
        for ressource, pk in existants.items():
            url = f'/api/{ressource}/{pk}/'
            with self.subTest(modification=ressource):
                self.assertEqual(self.alice.patch(url, {'notes': 'x', 'nom': 'x', 'montant': '1'},
                                                  format='json').status_code, 403)
                self.assertEqual(self.alice.put(url, corps[ressource], format='json').status_code, 403)
                self.assertEqual(self.alice.delete(url).status_code, 403)
        self.assertEqual(self.alice.post('/api/sync/airbnb/').status_code, 403)
        self.assertEqual({m: m.objects.count() for m in avant}, avant)
        self.maison.refresh_from_db()
        self.assertEqual(self.maison.nom, 'Maison')
        self.storage.add_member.assert_not_called()
        self.storage.remove_member.assert_not_called()

    def test_le_gestionnaire_ecrit_et_l_auteur_est_celui_du_jeton(self):
        resp = self.gina.post('/api/apports-initiaux/', {
            'bien': self.maison.pk, 'proprietaire': self.p_alice.pk, 'montant': '500.00',
            'date': '2026-05-01', 'created_by': 'usurpe@ssolab.local'}, format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()['created_by'], 'gina@ssolab.local')
        resp = self.gina.post('/api/taches/', {'bien': self.maison.pk, 'titre': 'T',
                                               'created_by': 'usurpe@ssolab.local'}, format='json')
        self.assertEqual(resp.json()['created_by'], 'gina@ssolab.local')
        self.assertEqual(self.gina.delete(f'/api/reservations/{self.res_maison.pk}/').status_code, 204)

    def test_bilan_complet_pour_le_gestionnaire(self):
        donnees = self.gina.get(f'/api/biens/{self.maison.pk}/bilan/').json()
        self.assertEqual([l['proprietaire_nom'] for l in donnees['proprietaires']], ['Alice', 'Bob'])

    def test_bilan_ferme_a_qui_n_a_pas_de_part(self):
        for client in (self.chloe, self.client_de('dan')):
            self.assertIn(client.get(f'/api/biens/{self.maison.pk}/bilan/').status_code, (403, 404))

    @unittest.expectedFailure
    def test_bilan_un_proprietaire_ne_recoit_que_sa_ligne(self):
        # DÉFAUT (rapport chantier « tests ») : BienViewSet.get_permissions
        # n'ouvre aux propriétaires que `list` et `retrieve` ; l'action
        # `bilan` tombe dans la branche IsManager. Un co-propriétaire reçoit
        # donc 403 sur le bilan de SON bien, alors que la vue est écrite pour
        # lui (docstring, filtrage de sa seule ligne juste en dessous — code
        # mort). Échec fermé : rien ne fuit, mais la fonction est cassée.
        # Correctif : ajouter 'bilan' aux actions ouvertes par IsManagerOrOwner.
        url = f'/api/biens/{self.maison.pk}/bilan/'
        donnees = self.alice.get(url).json()
        self.assertEqual([l['proprietaire_id'] for l in donnees['proprietaires']], [self.p_alice.pk])
        # Le total reste visible : apport de Bob 1000 − deux versements de 7.
        self.assertEqual(Decimal(str(donnees['capital_total'])), Decimal('986'))

    @unittest.expectedFailure
    def test_filtre_d_url_mal_forme_ne_provoque_pas_d_erreur_serveur(self):
        # DÉFAUT (rapport chantier « tests ») : les filtres `?bien=`,
        # `?appartement=`, `?tache=`, `?proprietaire=` sont passés tels quels
        # à l'ORM. Une valeur non numérique lève ValueError : erreur 500 au
        # lieu d'un 400 ou d'une liste vide, sur neuf listes.
        self.gina.raise_request_exception = False
        for chemin in ('/api/taches/?bien=abc', '/api/frais/?tache=abc',
                       '/api/remboursements/?proprietaire=abc'):
            with self.subTest(chemin=chemin):
                self.assertLess(self.gina.get(chemin).status_code, 500)
