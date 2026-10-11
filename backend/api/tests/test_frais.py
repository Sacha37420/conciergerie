"""Frais : ce qu'un co-propriétaire peut déclarer, et le trajet des factures
vers `storage` (compte de service — tout appel est simulé)."""
import datetime
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile

from api import storage_client
from api.models import Frais, Remboursement, Tache
from .base import Base


def pdf(nom='facture.pdf'):
    return SimpleUploadedFile(nom, b'%PDF-1.4 contenu', content_type='application/pdf')


class FraisBase(Base):

    def setUp(self):
        super().setUp()
        self.monde()
        self.tache_maison = Tache.objects.create(bien=self.maison, titre='Ménage maison')
        self.tache_chalet = Tache.objects.create(bien=self.chalet, titre='Ménage chalet')

    def corps(self, **champs):
        return {'tache': self.tache_maison.pk, 'libelle': 'Produits', 'montant_fixe': '12.50',
                'payeur': 'proprietaire', 'proprietaire_payeur': self.p_alice.pk, **champs}

    def avance(self, proprietaire, tache=None, **champs):
        return Frais.objects.create(tache=tache or self.tache_maison, libelle='Avance', montant_fixe=10,
                                    payeur='proprietaire', proprietaire_payeur=proprietaire, **champs)


class DeclarationParUnProprietaireTests(FraisBase):

    def test_declare_un_frais_qu_il_a_paye_sur_son_bien(self):
        resp = self.alice.post('/api/frais/', self.corps(created_by='usurpe@ssolab.local'), format='json')
        self.assertEqual(resp.status_code, 201, resp.content)
        frais = Frais.objects.get(pk=resp.json()['id'])
        self.assertEqual((frais.created_by, frais.payeur, frais.proprietaire_payeur),
                         ('alice@ssolab.local', 'proprietaire', self.p_alice))

    def test_ne_declare_pas_un_frais_paye_par_la_maison(self):
        # Un frais « maison » daté entre directement dans le grand livre.
        resp = self.alice.post('/api/frais/', self.corps(payeur='maison', proprietaire_payeur=None,
                                                         date_paiement='2026-05-01'), format='json')
        self.assertEqual(resp.status_code, 403)

    def test_ne_declare_pas_pour_un_autre_proprietaire(self):
        resp = self.alice.post('/api/frais/', self.corps(proprietaire_payeur=self.p_bob.pk), format='json')
        self.assertEqual(resp.status_code, 403)

    def test_ne_declare_pas_sur_un_bien_qu_il_ne_possede_pas(self):
        resp = self.alice.post('/api/frais/', self.corps(tache=self.tache_chalet.pk), format='json')
        self.assertEqual(resp.status_code, 403)

    def test_membre_sans_fiche_ne_declare_rien(self):
        resp = self.client_de('dan').post('/api/frais/', self.corps(), format='json')
        self.assertEqual(resp.status_code, 403)

    def test_un_refus_ne_depose_aucun_fichier(self):
        for champs in ({'payeur': 'maison', 'proprietaire_payeur': ''},
                       {'proprietaire_payeur': self.p_bob.pk}, {'tache': self.tache_chalet.pk}):
            with self.subTest(champs=champs):
                resp = self.alice.post('/api/frais/', {**self.corps(**champs), 'facture_file': pdf()},
                                       format='multipart')
                self.assertEqual(resp.status_code, 403)
        self.assertEqual(Frais.objects.count(), 0)
        self.storage.upload.assert_not_called()

    def test_modifie_et_supprime_son_frais_non_rembourse(self):
        frais = self.avance(self.p_alice)
        url = f'/api/frais/{frais.pk}/'
        self.assertEqual(self.alice.patch(url, {'montant_fixe': '15.00'}, format='json').status_code, 200)
        self.assertEqual(self.alice.delete(url).status_code, 204)
        self.assertFalse(Frais.objects.filter(pk=frais.pk).exists())

    def test_ne_touche_pas_au_frais_d_un_autre_ni_a_ceux_de_la_maison(self):
        de_bob = self.avance(self.p_bob, facture_path='factures/bob.pdf')
        de_la_maison = Frais.objects.create(tache=self.tache_maison, libelle='Maison', montant_fixe=10)
        for frais in (de_bob, de_la_maison):
            url = f'/api/frais/{frais.pk}/'
            with self.subTest(frais=frais.libelle):
                self.assertEqual(self.alice.get(url).status_code, 200)      # lecture : transparence
                self.assertEqual(self.alice.patch(url, {'montant_fixe': '999'}, format='json').status_code, 403)
                self.assertEqual(self.alice.patch(url, {'facture_file': pdf()},
                                                  format='multipart').status_code, 403)
                self.assertEqual(self.alice.delete(url).status_code, 403)
                frais.refresh_from_db()
                self.assertEqual(frais.montant_fixe, 10)
        self.storage.upload.assert_not_called()
        self.storage.delete.assert_not_called()

    def test_un_frais_rembourse_est_fige_pour_son_auteur(self):
        frais = self.avance(self.p_alice, facture_path='factures/a.pdf')
        remb = Remboursement.objects.create(proprietaire=self.p_alice, montant=10,
                                            date_versement=datetime.date(2026, 5, 1))
        remb.frais.add(frais)
        url = f'/api/frais/{frais.pk}/'
        self.assertEqual(self.alice.patch(url, {'montant_fixe': '500'}, format='json').status_code, 403)
        self.assertEqual(self.alice.delete(url).status_code, 403)
        self.storage.delete.assert_not_called()
        # Le gestionnaire, lui, garde la main.
        self.assertEqual(self.gina.patch(url, {'montant_fixe': '11'}, format='json').status_code, 200)

    # perform_update applique aux valeurs DEMANDÉES les règles de la création :
    # un co-propriétaire ne change ni l'auteur, ni le bien, ni la nature de
    # son frais. (Avant correctif, ces trois PATCH passaient.)

    def _refuse_et_inchange(self, corps):
        frais = self.avance(self.p_alice)
        for methode, donnees in ((self.alice.patch, corps), (self.alice.put, {**self.corps(), **corps})):
            resp = methode(f'/api/frais/{frais.pk}/', donnees, format='json')
            self.assertEqual(resp.status_code, 403, resp.content)
        frais.refresh_from_db()
        self.assertEqual((frais.payeur, frais.proprietaire_payeur, frais.tache, frais.montant_fixe,
                          frais.date_paiement), ('proprietaire', self.p_alice, self.tache_maison, 10, None))
        # Le gestionnaire garde ses droits sur le même changement.
        self.assertEqual(self.gina.patch(f'/api/frais/{frais.pk}/', corps, format='json').status_code, 200)

    def test_modification_ne_met_pas_le_frais_au_nom_d_un_autre(self):
        self._refuse_et_inchange({'proprietaire_payeur': self.p_bob.pk})

    def test_modification_ne_deplace_pas_le_frais_sur_le_bien_d_autrui(self):
        self._refuse_et_inchange({'tache': self.tache_chalet.pk})

    def test_modification_ne_deplace_pas_le_frais_meme_vers_un_autre_de_ses_biens(self):
        # La facture vit dans le partage storage du bien d'origine.
        from api.models import PartProprietaire
        PartProprietaire.objects.create(bien=self.chalet, proprietaire=self.p_alice)
        self._refuse_et_inchange({'tache': self.tache_chalet.pk})

    def test_modification_ne_convertit_pas_en_frais_de_la_maison(self):
        self._refuse_et_inchange({'payeur': 'maison', 'proprietaire_payeur': None, 'montant_fixe': '5000',
                                  'date_paiement': '2026-05-01'})

    def test_modification_permise_montant_libelle_date_et_autre_tache_du_meme_bien(self):
        frais = self.avance(self.p_alice)
        autre = Tache.objects.create(bien=self.maison, titre='Autre tâche de la maison')
        resp = self.alice.put(f'/api/frais/{frais.pk}/', self.corps(
            tache=autre.pk, libelle='Corrigé', montant_fixe='18.00', date_paiement='2026-05-02'), format='json')
        self.assertEqual(resp.status_code, 200, resp.content)
        frais.refresh_from_db()
        self.assertEqual((frais.tache, frais.libelle, frais.montant_fixe), (autre, 'Corrigé', 18))


class FacturesTests(FraisBase):

    def test_la_facture_part_dans_le_partage_du_bien(self):
        resp = self.gina.post('/api/frais/', {**self.corps(), 'facture_file': pdf('recu-ete.pdf')},
                              format='multipart')
        self.assertEqual(resp.status_code, 201, resp.content)
        (bien_id, chemin, fichier, nom, type_mime), _ = self.storage.upload.call_args
        self.assertEqual(bien_id, self.maison.pk)
        self.assertRegex(chemin, r'^factures/\d{4}/\d{2}/[0-9a-f]{32}-recu-ete\.pdf$')
        self.assertEqual((nom, type_mime), ('recu-ete.pdf', 'application/pdf'))
        frais = Frais.objects.get(pk=resp.json()['id'])
        self.assertEqual(frais.facture_path, chemin)
        self.assertNotIn('facture_file', resp.json())
        self.assertNotIn('facture_path', resp.json())

    def test_un_nom_de_fichier_ne_sort_pas_du_dossier_des_factures(self):
        self.gina.post('/api/frais/', {**self.corps(), 'facture_file': pdf('../../../autre/x.pdf')},
                       format='multipart')
        chemin = self.storage.upload.call_args.args[1]
        self.assertRegex(chemin, r'^factures/\d{4}/\d{2}/[0-9a-f]{32}-x\.pdf$')

    def test_storage_en_panne_aucun_frais_cree(self):
        self.storage.upload.side_effect = storage_client.StorageClientError('storage injoignable')
        self.gina.raise_request_exception = False
        resp = self.gina.post('/api/frais/', {**self.corps(), 'facture_file': pdf()}, format='multipart')
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(Frais.objects.count(), 0)

    def test_sans_fichier_aucun_appel_a_storage(self):
        resp = self.gina.post('/api/frais/', self.corps(), format='json')
        self.assertEqual(resp.status_code, 201)
        self.assertIsNone(resp.json()['facture'])
        self.storage.upload.assert_not_called()

    def test_remplacer_la_facture_depose_la_nouvelle_puis_purge_l_ancienne(self):
        frais = self.avance(self.p_alice, facture_path='factures/2026/01/ancienne.pdf')
        resp = self.alice.patch(f'/api/frais/{frais.pk}/', {'facture_file': pdf('nouvelle.pdf')},
                                format='multipart')
        self.assertEqual(resp.status_code, 200, resp.content)
        frais.refresh_from_db()
        self.assertTrue(frais.facture_path.endswith('-nouvelle.pdf'))
        self.assertEqual([c[0] for c in self.storage.method_calls], ['upload', 'delete'])
        self.storage.delete.assert_called_once_with(self.maison.pk, 'factures/2026/01/ancienne.pdf')

    def test_remplacement_rate_l_ancienne_facture_reste(self):
        frais = self.avance(self.p_alice, facture_path='factures/2026/01/ancienne.pdf')
        self.storage.upload.side_effect = storage_client.StorageClientError('panne')
        self.alice.raise_request_exception = False
        resp = self.alice.patch(f'/api/frais/{frais.pk}/', {'facture_file': pdf()}, format='multipart')
        self.assertEqual(resp.status_code, 500)
        frais.refresh_from_db()
        self.assertEqual(frais.facture_path, 'factures/2026/01/ancienne.pdf')
        self.storage.delete.assert_not_called()

    def test_modifier_sans_fichier_garde_la_facture(self):
        frais = self.avance(self.p_alice, facture_path='factures/a.pdf')
        self.alice.patch(f'/api/frais/{frais.pk}/', {'libelle': 'Renommé'}, format='json')
        frais.refresh_from_db()
        self.assertEqual((frais.libelle, frais.facture_path), ('Renommé', 'factures/a.pdf'))
        self.assertEqual(self.storage.method_calls, [])

    def test_supprimer_le_frais_purge_sa_facture(self):
        frais = self.avance(self.p_alice, facture_path='factures/a.pdf')
        self.assertEqual(self.gina.delete(f'/api/frais/{frais.pk}/').status_code, 204)
        self.storage.delete.assert_called_once_with(self.maison.pk, 'factures/a.pdf')

    def test_purge_impossible_le_frais_n_est_pas_supprime(self):
        frais = self.avance(self.p_alice, facture_path='factures/a.pdf')
        self.storage.delete.side_effect = storage_client.StorageClientError('panne')
        self.gina.raise_request_exception = False
        self.assertEqual(self.gina.delete(f'/api/frais/{frais.pk}/').status_code, 500)
        self.assertTrue(Frais.objects.filter(pk=frais.pk).exists())

    # ── Lecture de la facture ───────────────────────────────────────────────

    def test_lien_de_facture_proxy_pour_le_gestionnaire_storage_direct_pour_le_proprietaire(self):
        frais = self.avance(self.p_bob, facture_path='factures/2026/05/abc-Reçu été.pdf')
        url = f'/api/frais/{frais.pk}/'
        self.assertEqual(self.gina.get(url).json()['facture'],
                         f'http://testserver/conciergerie-api/api/frais/{frais.pk}/facture/')
        # Co-propriétaire : URL directe de storage, dans le partage DU BIEN —
        # c'est son ShareMember (test_partages) qui lui en ouvre la lecture.
        self.assertEqual(
            self.alice.get(url).json()['facture'],
            f'http://testserver/storage-api/api/files/conciergerie-bien-{self.maison.pk}'
            '/content/factures/2026/05/abc-Re%C3%A7u%20%C3%A9t%C3%A9.pdf')

    def test_telechargement_par_le_proxy(self):
        frais = self.avance(self.p_bob, facture_path='factures/2026/05/abc-recu.pdf')
        amont = mock.Mock(headers={'Content-Type': 'application/pdf'})
        amont.iter_content.return_value = iter([b'%PDF', b'-1.4'])
        self.storage.download.return_value = amont
        for client in (self.gina, self.alice):       # gestionnaire, co-propriétaire du bien
            amont.iter_content.return_value = iter([b'%PDF', b'-1.4'])
            resp = client.get(f'/api/frais/{frais.pk}/facture/')
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(b''.join(resp.streaming_content), b'%PDF-1.4')
            self.assertEqual(resp['Content-Type'], 'application/pdf')
            self.assertIn('abc-recu.pdf', resp['Content-Disposition'])
        self.storage.download.assert_called_with(self.maison.pk, 'factures/2026/05/abc-recu.pdf')

    def test_telechargement_refuse_hors_du_bien_sans_appeler_storage(self):
        frais = self.avance(self.p_bob, facture_path='factures/x.pdf')
        self.assertEqual(self.chloe.get(f'/api/frais/{frais.pk}/facture/').status_code, 404)
        self.assertEqual(self.client_de('dan').get(f'/api/frais/{frais.pk}/facture/').status_code, 404)
        self.storage.download.assert_not_called()

    def test_frais_sans_facture(self):
        frais = self.avance(self.p_bob)
        self.assertEqual(self.gina.get(f'/api/frais/{frais.pk}/facture/').status_code, 404)
        self.storage.download.assert_not_called()
