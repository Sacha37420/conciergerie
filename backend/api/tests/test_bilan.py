"""Grand livre de capital (api/bilan.py). Les montants attendus sont calculés
à la main dans les commentaires, pas recopiés d'une exécution."""
import datetime
from decimal import Decimal as D

from api import bilan
from api.models import (
    ApportInitial, Bien, Frais, PartProprietaire, Proprietaire, Remboursement,
    Reservation, Tache, VersementRevenu,
)
from .base import Base


def lu(valeur) -> D:
    """Montant lu dans une réponse JSON (l'API rend des nombres, pas des chaînes)."""
    return D(str(valeur))


def jour(mois, j=1):
    return datetime.date(2026, mois, j)


class GrandLivreBase(Base):

    def setUp(self):
        super().setUp()
        self.monde()

    def revenu(self, montant, date, bien_appt=None, **champs):
        return Reservation.objects.create(
            appartement=bien_appt or self.appt_maison, source='direct', date_debut=date, date_fin=date,
            montant_revenu=D(montant), date_paiement=date, **champs)

    def apport(self, proprietaire, montant, date, bien=None):
        return ApportInitial.objects.create(bien=bien or self.maison, proprietaire=proprietaire,
                                            montant=D(montant), date=date)

    def capitaux(self, bien=None):
        etat = bilan._rejouer(bien or self.maison)
        return etat['capital'], etat['capital_total']


class ScenarioCompletTests(GrandLivreBase):
    """Maison : commission 10 % + 5 € par séjour, temps propriétaire à 6 €/h.

    date   évènement                                  Alice      Bob      total
    01-01  apport Alice 6000                        6000,00     0,00   6000,00
    01-02  apport Bob 4000                          6000,00  4000,00  10000,00
    02-01  séjour 1005 → net 1005−(100,50+5)=899,50
           au prorata 60/40 : +539,70 / +359,80     6539,70  4359,80  10899,50
    03-01  frais maison 100 + 50 €/h × 2 h = 200
           au prorata 60/40 : −120 / −80            6419,70  4279,80  10699,50
    04-01  versement de revenus à Alice 419,70      6000,00  4279,80  10279,80
    05-01  Bob travaille 10 h × 6 = 60 :
           +60 pour Bob                             6000,00  4339,80  10339,80
           −60 au prorata : Alice 6000/10339,80
           = 58,0282 % → −34,82 ; Bob le reste −25,18
                                                    5965,18  4314,62  10279,80
    06-01  remboursement à Alice d'un frais de 80
           qu'elle avait avancé, au prorata :
           Alice 5965,18/10279,80 = 58,0282 % → −46,42 ; Bob −33,58
                                                    5918,76  4281,04  10199,80
    """

    def setUp(self):
        super().setUp()
        self.apport(self.p_alice, '6000', jour(1, 1))
        self.apport(self.p_bob, '4000', jour(1, 2))
        self.sejour = self.revenu('1005', jour(2))
        t1 = Tache.objects.create(bien=self.maison, titre='Plomberie', duree_heures=D('2'))
        Frais.objects.create(tache=t1, libelle='Plombier', montant_fixe=D('100'), taux_horaire=D('50'),
                             payeur='maison', date_paiement=jour(3))
        VersementRevenu.objects.create(bien=self.maison, proprietaire=self.p_alice, montant=D('419.70'),
                                       date_versement=jour(4))
        Tache.objects.create(bien=self.maison, titre='Peinture', duree_heures=D('10'),
                             proprietaire_responsable=self.p_bob, date_paiement=jour(5))
        t3 = Tache.objects.create(bien=self.maison, titre='Courses')
        avance = Frais.objects.create(tache=t3, libelle='Linge', montant_fixe=D('80'),
                                      payeur='proprietaire', proprietaire_payeur=self.p_alice)
        remb = Remboursement.objects.create(proprietaire=self.p_alice, montant=D('80'), date_versement=jour(6))
        remb.frais.add(avance)

    def test_capitaux_finaux(self):
        capital, total = self.capitaux()
        self.assertEqual(capital[self.p_alice.pk], D('5918.76'))
        self.assertEqual(capital[self.p_bob.pk], D('4281.04'))
        self.assertEqual(total, D('10199.80'))
        self.assertEqual(sum(capital.values()), total)

    def test_bilan_rendu_par_l_api(self):
        donnees = self.gina.get(f'/api/biens/{self.maison.pk}/bilan/').json()
        self.assertEqual(lu(donnees['revenu_brut_total']), D('1005'))
        # Tous les frais (200 + 80) plus le travail valorisé (60).
        self.assertEqual(lu(donnees['frais_total']), D('340'))
        self.assertEqual(lu(donnees['cumul_gains_depenses']), D('665'))
        self.assertEqual(lu(donnees['capital_total']), D('10199.80'))
        lignes = {l['proprietaire_nom']: l for l in donnees['proprietaires']}
        self.assertEqual(lu(lignes['Alice']['capital']), D('5918.76'))
        self.assertEqual(lu(lignes['Bob']['capital']), D('4281.04'))
        # 5918,76 / 10199,80 = 58,03 % ; 4281,04 / 10199,80 = 41,97 %.
        self.assertEqual(lu(lignes['Alice']['quote_part_pct']).quantize(D('0.01')), D('58.03'))
        self.assertEqual(lu(lignes['Bob']['quote_part_pct']).quantize(D('0.01')), D('41.97'))

    def test_part_de_chacun_dans_un_sejour(self):
        self.assertEqual(bilan.part_reservation(self.sejour, self.p_alice), D('539.70'))
        self.assertEqual(bilan.part_reservation(self.sejour, self.p_bob), D('359.80'))
        donnees = self.gina.get(f'/api/reservations/{self.sejour.pk}/').json()
        self.assertEqual({p['proprietaire_nom']: lu(p['montant']) for p in donnees['parts_proprietaires']},
                         {'Alice': D('539.70'), 'Bob': D('359.80')})

    def test_l_autre_bien_n_est_pas_touche(self):
        capital, total = self.capitaux(self.chalet)
        self.assertEqual((capital, total), ({self.p_chloe.pk: D('0')}, D('0')))


class ReglesDuGrandLivreTests(GrandLivreBase):

    def setUp(self):
        super().setUp()
        self.maison.commission_gestion_pct = D('0')
        self.maison.commission_gestion_fixe = D('0')
        self.maison.save()

    def test_commission_pourcentage_plus_fixe(self):
        bien = Bien(commission_gestion_pct=D('12.50'), commission_gestion_fixe=D('20'))
        self.assertEqual(bilan.commission_sur(D('800'), bien), D('120'))      # 100 + 20

    def test_avant_tout_apport_le_revenu_se_partage_a_parts_egales(self):
        self.revenu('100', jour(1))
        capital, total = self.capitaux()
        self.assertEqual(capital, {self.p_alice.pk: D('50'), self.p_bob.pk: D('50')})
        self.assertEqual(total, D('100'))

    def test_aucun_centime_perdu_a_l_arrondi(self):
        # Trois propriétaires, 100 € : 33,33 + 33,33 + le reste 33,34.
        dan = Proprietaire.objects.create(nom='Dan', email='dan@ssolab.local')
        PartProprietaire.objects.create(bien=self.maison, proprietaire=dan)
        self.revenu('100', jour(1))
        capital, total = self.capitaux()
        self.assertEqual(sorted(capital.values()), [D('33.33'), D('33.33'), D('33.34')])
        self.assertEqual(sum(capital.values()), total)
        # 0,01 € à trois : 0,00 + 0,00 + 0,01, jamais 0,01 distribué trois fois.
        self.revenu('0.01', jour(2))
        capital, total = self.capitaux()
        self.assertEqual(sum(capital.values()), D('100.01'))
        self.assertEqual(total, D('100.01'))

    def test_le_meme_jour_l_apport_passe_avant_le_revenu(self):
        # Alice apporte 1000 le jour où 100 sont encaissés : le revenu se
        # répartit sur un capital qui compte déjà l'apport (100 % Alice), pas
        # à parts égales comme s'il n'y avait encore rien.
        self.revenu('100', jour(3))
        self.apport(self.p_alice, '1000', jour(3))
        capital, _ = self.capitaux()
        self.assertEqual(capital, {self.p_alice.pk: D('1100'), self.p_bob.pk: D('0')})

    def test_l_ordre_est_celui_des_dates_pas_de_la_saisie(self):
        # Saisis à l'envers : revenu de mars, puis apports de janvier.
        self.revenu('100', jour(3))
        self.apport(self.p_alice, '750', jour(1))
        self.apport(self.p_bob, '250', jour(1))
        capital, _ = self.capitaux()
        self.assertEqual(capital, {self.p_alice.pk: D('825'), self.p_bob.pk: D('275')})

    def test_ce_qui_n_entre_pas_dans_le_grand_livre(self):
        self.apport(self.p_alice, '1000', jour(1))
        self.revenu('500', jour(2), statut='annulee')                       # séjour annulé
        Reservation.objects.create(appartement=self.appt_maison, source='airbnb',
                                   date_debut=jour(2), date_fin=jour(2))    # pas de montant
        self.revenu('900', jour(2), bien_appt=self.appt_chalet)             # autre bien
        t = Tache.objects.create(bien=self.maison, titre='T', duree_heures=D('3'),
                                 proprietaire_responsable=self.p_bob)       # travail non daté
        Frais.objects.create(tache=t, libelle='Maison non payé', montant_fixe=D('40'))   # non daté
        Frais.objects.create(tache=t, libelle='Avance non remboursée', montant_fixe=D('70'),
                             payeur='proprietaire', proprietaire_payeur=self.p_bob, date_paiement=jour(2))
        capital, total = self.capitaux()
        self.assertEqual(capital, {self.p_alice.pk: D('1000'), self.p_bob.pk: D('0')})
        self.assertEqual(total, D('1000'))
        # Le compte de résultat, lui, compte déjà frais et travail (40 + 70 + 3 h × 6).
        donnees = bilan.bilan_bien(self.maison)
        self.assertEqual(donnees['frais_total'], D('128'))
        self.assertEqual(donnees['revenu_brut_total'], D('0'))

    def test_un_versement_ne_reduit_que_le_capital_de_son_beneficiaire(self):
        self.apport(self.p_alice, '500', jour(1))
        self.apport(self.p_bob, '500', jour(1))
        VersementRevenu.objects.create(bien=self.maison, proprietaire=self.p_bob, montant=D('200'),
                                       date_versement=jour(2))
        capital, total = self.capitaux()
        self.assertEqual(capital, {self.p_alice.pk: D('500'), self.p_bob.pk: D('300')})
        self.assertEqual(total, D('800'))
        lignes = {l['proprietaire_id']: l['quote_part_pct'] for l in bilan.bilan_bien(self.maison)['proprietaires']}
        self.assertEqual(lignes, {self.p_alice.pk: D('62.5'), self.p_bob.pk: D('37.5')})

    def test_le_travail_valorise_deplace_de_la_valeur_sans_en_creer(self):
        self.apport(self.p_alice, '500', jour(1))
        self.apport(self.p_bob, '500', jour(1))
        Tache.objects.create(bien=self.maison, titre='Jardin', duree_heures=D('5'),
                             proprietaire_responsable=self.p_bob, date_paiement=jour(2))
        # +30 pour Bob (530 sur 1030), puis −30 au prorata : Alice 500/1030
        # → −14,56 ; Bob −15,44. Total inchangé.
        capital, total = self.capitaux()
        self.assertEqual(capital, {self.p_alice.pk: D('485.44'), self.p_bob.pk: D('514.56')})
        self.assertEqual(total, D('1000'))

    def test_un_remboursement_ne_compte_que_les_frais_de_ce_bien(self):
        self.apport(self.p_alice, '1000', jour(1))
        self.apport(self.p_chloe, '1000', jour(1), bien=self.chalet)
        PartProprietaire.objects.create(bien=self.chalet, proprietaire=self.p_alice)
        t_maison = Tache.objects.create(bien=self.maison, titre='M')
        t_chalet = Tache.objects.create(bien=self.chalet, titre='C')
        f1 = Frais.objects.create(tache=t_maison, libelle='M', montant_fixe=D('30'),
                                  payeur='proprietaire', proprietaire_payeur=self.p_alice)
        f2 = Frais.objects.create(tache=t_chalet, libelle='C', montant_fixe=D('70'),
                                  payeur='proprietaire', proprietaire_payeur=self.p_alice)
        remb = Remboursement.objects.create(proprietaire=self.p_alice, montant=D('100'), date_versement=jour(2))
        remb.frais.add(f1, f2)
        self.assertEqual(self.capitaux(self.maison)[1], D('970'))
        self.assertEqual(self.capitaux(self.chalet)[1], D('930'))

    def test_quote_part_indefinie_sans_capital(self):
        lignes = bilan.bilan_bien(self.maison)['proprietaires']
        self.assertEqual([l['quote_part_pct'] for l in lignes], [None, None])
        # Capital total négatif (dépense avant tout apport) : pas de pourcentage absurde.
        t = Tache.objects.create(bien=self.maison, titre='T')
        Frais.objects.create(tache=t, libelle='F', montant_fixe=D('100'), date_paiement=jour(1))
        donnees = bilan.bilan_bien(self.maison)
        self.assertEqual(donnees['capital_total'], D('-100'))
        self.assertEqual([l['quote_part_pct'] for l in donnees['proprietaires']], [None, None])

    def test_bien_sans_proprietaire(self):
        vide = Bien.objects.create(nom='Vide')
        self.apport(self.p_alice, '10', jour(1), bien=vide)     # apport d'un ancien co-propriétaire
        donnees = bilan.bilan_bien(vide)
        self.assertEqual(donnees['proprietaires'], [])
        self.assertEqual(donnees['capital_total'], D('10'))

    def test_part_d_un_sejour_sans_encaissement(self):
        res = Reservation.objects.create(appartement=self.appt_maison, source='airbnb',
                                         date_debut=jour(2), date_fin=jour(2))
        self.assertIsNone(bilan.part_reservation(res, self.p_alice))
        self.assertEqual(self.gina.get(f'/api/reservations/{res.pk}/').json()['parts_proprietaires'], [])
