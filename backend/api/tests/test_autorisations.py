"""Qui lit et écrit quoi : gestionnaire (`admins`) et co-propriétaires
(`proprietaires`, cloisonnés par bien via PartProprietaire et l'email du jeton)."""
import datetime
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
        self.assertEqual(self.alice.get(f'/api/biens/{self.chalet.pk}/bilan/').status_code, 404)

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
        self.assertEqual(self.alice.get(f'/api/biens/{self.maison.pk}/bilan/').status_code, 404)

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
            self.assertEqual(client.get(f'/api/biens/{self.maison.pk}/bilan/').status_code, 404)
        # Le bilan reste une lecture : aucune écriture par cette route.
        self.assertIn(self.alice.post(f'/api/biens/{self.maison.pk}/bilan/', {}).status_code, (403, 405))

    def test_bilan_un_proprietaire_ne_recoit_que_sa_ligne(self):
        # Le co-propriétaire lit le bilan de SON bien, réduit à sa ligne.
        url = f'/api/biens/{self.maison.pk}/bilan/'
        donnees = self.alice.get(url).json()
        self.assertEqual([l['proprietaire_id'] for l in donnees['proprietaires']], [self.p_alice.pk])
        # Le total reste visible : apport de Bob 1000 − deux versements de 7.
        self.assertEqual(Decimal(str(donnees['capital_total'])), Decimal('986'))

    def test_part_par_sejour_un_proprietaire_ne_recoit_que_la_sienne(self):
        # Même règle que le bilan : la part des autres co-propriétaires dans
        # un séjour (donc leur poids dans le capital) n'est rendue qu'au
        # gestionnaire.
        Reservation.objects.filter(pk=self.res_maison.pk).update(montant_revenu=Decimal('200'), date_paiement=J)
        url = f'/api/reservations/{self.res_maison.pk}/'
        self.assertEqual({p['proprietaire_nom'] for p in self.gina.get(url).json()['parts_proprietaires']},
                         {'Alice', 'Bob'})
        for client, fiche in ((self.alice, self.p_alice), (self.bob, self.p_bob)):
            for donnees in (client.get(url).json(), client.get('/api/reservations/').json()[0]):
                self.assertEqual([p['proprietaire_id'] for p in donnees['parts_proprietaires']], [fiche.pk])
                self.assertIsNotNone(donnees['parts_proprietaires'][0]['montant'])

    def test_filtre_d_url_mal_forme_refuse_en_400(self):
        # Les filtres portant un identifiant n'acceptent que des chiffres ;
        # sinon 400, pour le gestionnaire comme pour un propriétaire (avant
        # correctif : ValueError de l'ORM, erreur 500).
        chemins = ['/api/parts-proprietaire/?bien=', '/api/appartements/?bien=', '/api/reservations/?bien=',
                   '/api/reservations/?appartement=', '/api/taches/?bien=', '/api/taches/?appartement=',
                   '/api/frais/?tache=', '/api/remboursements/?proprietaire=', '/api/apports-initiaux/?bien=',
                   '/api/versements-revenu/?bien=']
        for client in (self.gina, self.alice):
            for chemin in chemins:
                for valeur in ('abc', '1 OR 1=1', '-1', '1.5', '²'):
                    with self.subTest(chemin=chemin, valeur=valeur):
                        route, nom = chemin.rstrip('=').split('?')
                        resp = client.get(route, {nom: valeur})
                        self.assertEqual(resp.status_code, 400)
        # Filtre vide = pas de filtre ; identifiant inconnu = liste vide.
        self.assertEqual(len(self.ids(self.gina, '/api/taches/?bien=')), 2)
        self.assertEqual(self.ids(self.gina, '/api/taches/?bien=99999999999999999999'), [])
