from decimal import Decimal
from urllib.parse import quote

from django.conf import settings
from django.urls import reverse
from rest_framework import serializers
from .models import (
    Proprietaire, Entreprise, Bien, PartProprietaire, Appartement, Reservation,
    Tache, Frais, Remboursement, ApportInitial, VersementRevenu,
)
from . import bilan as bilan_module
from . import ical_fetch
from .scoping import is_manager, proprietaire_for


class ProprietaireSerializer(serializers.ModelSerializer):
    class Meta:
        model = Proprietaire
        fields = ['id', 'nom', 'email', 'telephone', 'notes']


class EntrepriseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Entreprise
        fields = ['id', 'nom', 'contact_nom', 'telephone', 'email', 'specialite']


class PartProprietaireSerializer(serializers.ModelSerializer):
    proprietaire_detail = ProprietaireSerializer(source='proprietaire', read_only=True)

    class Meta:
        model = PartProprietaire
        fields = ['id', 'bien', 'proprietaire', 'proprietaire_detail']


class AppartementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Appartement
        fields = [
            'id', 'bien', 'nom', 'capacite', 'description',
            'airbnb_ical_url', 'dernier_sync_at', 'dernier_sync_erreur',
        ]
        read_only_fields = ['dernier_sync_at', 'dernier_sync_erreur']

    def validate_airbnb_ical_url(self, value):
        # Le backend appellera cette URL : elle ne doit désigner aucune
        # destination interne (api/ical_fetch.py). Une URL inchangée n'est pas
        # recontrôlée ici — elle l'est de toute façon à chaque appel.
        if value and value != getattr(self.instance, 'airbnb_ical_url', None):
            try:
                ical_fetch.verifier_url(value)
            except ical_fetch.DestinationInterdite as exc:
                raise serializers.ValidationError(str(exc)) from exc
        return value


class BienSerializer(serializers.ModelSerializer):
    parts = PartProprietaireSerializer(many=True, read_only=True)
    appartements = AppartementSerializer(many=True, read_only=True)

    class Meta:
        model = Bien
        fields = [
            'id', 'nom', 'adresse', 'ville', 'code_postal', 'description',
            'commission_gestion_pct', 'commission_gestion_fixe',
            'valorisation_heure_proprietaire',
            'parts', 'appartements', 'created_at',
        ]
        read_only_fields = ['created_at']


class ReservationSerializer(serializers.ModelSerializer):
    parts_proprietaires = serializers.SerializerMethodField()

    class Meta:
        model = Reservation
        fields = [
            'id', 'appartement', 'source', 'uid_externe', 'date_debut', 'date_fin',
            'libelle', 'statut', 'montant_revenu', 'date_paiement', 'notes',
            'parts_proprietaires', 'created_at', 'updated_at',
        ]
        read_only_fields = ['uid_externe', 'created_at', 'updated_at']

    def validate(self, attrs):
        montant = attrs.get('montant_revenu', getattr(self.instance, 'montant_revenu', None))
        date_paiement = attrs.get('date_paiement', getattr(self.instance, 'date_paiement', None))
        if (montant is None) != (date_paiement is None):
            raise serializers.ValidationError(
                "Le revenu du séjour et sa date d'encaissement doivent être renseignés ensemble "
                '(l\'un détermine quand ce revenu entre dans le grand livre du bilan).'
            )
        return attrs

    def get_parts_proprietaires(self, obj):
        if obj.montant_revenu is None or obj.date_paiement is None:
            return []
        bien = obj.appartement.bien
        parts = bien.parts.select_related('proprietaire')
        # Même règle que le bilan (BienViewSet.bilan) : un co-propriétaire ne
        # reçoit que sa propre part, le gestionnaire les voit toutes.
        request = self.context.get('request')
        if request is not None and not is_manager(request):
            proprietaire = proprietaire_for(request.user)
            parts = parts.filter(proprietaire=proprietaire) if proprietaire else parts.none()
        return [
            {
                'proprietaire_id': part.proprietaire_id,
                'proprietaire_nom': part.proprietaire.nom,
                'montant': bilan_module.part_reservation(obj, part.proprietaire),
            }
            for part in parts
        ]


class FraisSerializer(serializers.ModelSerializer):
    montant_total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    proprietaire_payeur_detail = ProprietaireSerializer(source='proprietaire_payeur', read_only=True)
    # La facture n'est plus un FileField local (stockage délégué à l'app
    # storage, cf. api/storage_client.py) : écriture = champ multipart séparé,
    # lu dans FraisViewSet.perform_create/update, pas persisté tel quel par ce
    # serializer (facture_path est déduit après upload). Lecture : voir
    # get_facture ci-dessous, deux chemins possibles.
    facture = serializers.SerializerMethodField()
    facture_file = serializers.FileField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Frais
        fields = [
            'id', 'tache', 'libelle', 'montant_fixe', 'taux_horaire', 'payeur',
            'proprietaire_payeur', 'proprietaire_payeur_detail', 'facture', 'facture_file',
            'date_paiement', 'notes', 'montant_total', 'created_by', 'created_at',
        ]
        read_only_fields = ['created_by', 'created_at']

    def get_facture(self, obj):
        """URL de lecture de la facture — deux chemins distincts, tous deux
        déjà correctement cloisonnés :

        - Co-propriétaire (le cas courant du portail) : URL storage
          **directe** (même origine, cf. dev/CLAUDE.md « Verrou 2
          généralisé »). Sûr uniquement parce que chaque PartProprietaire a
          désormais un vrai ShareMember correspondant sur le partage du bien
          (cf. api/signals.py) — c'est storage qui devient le seul rempart
          sur ce chemin, avec le propre token de l'utilisateur.
        - Gestionnaire : reste proxié par FraisViewSet.facture. Un
          gestionnaire voit TOUS les biens (pas de filtre biens_du_proprietaire,
          cf. FraisViewSet.get_queryset), sans notion de co-propriété — le
          traduire en ShareMember individuels par gestionnaire supposerait de
          connaître la liste des comptes du groupe Keycloak `admins`, que ce
          backend n'a aujourd'hui aucun moyen d'interroger (pas d'accès à
          l'API Admin Keycloak, cf. .keycloak-service-account-roles vide).
          Plutôt que de deviner un mécanisme de synchronisation supplémentaire
          (risque de dérive du type documenté pour e2e_member), on garde le
          proxy pour ce rôle : IsManagerOrOwner/is_manager revérifie déjà
          correctement l'accès à chaque requête, sans rien à synchroniser.
        """
        if not obj.facture_path:
            return None
        request = self.context.get('request')
        if request is not None and not is_manager(request):
            namespace = f'{settings.STORAGE_NAMESPACE_PREFIX}{obj.tache.bien_id}'
            quoted_path = quote(obj.facture_path, safe='/')
            location = f'/storage-api/api/files/{namespace}/content/{quoted_path}'
            return request.build_absolute_uri(location)
        url = reverse('frais-facture', args=[obj.pk])
        return request.build_absolute_uri(url) if request else url

    def validate(self, attrs):
        payeur = attrs.get('payeur', getattr(self.instance, 'payeur', 'maison'))
        proprietaire_payeur = attrs.get(
            'proprietaire_payeur', getattr(self.instance, 'proprietaire_payeur', None),
        )
        if payeur == 'proprietaire' and not proprietaire_payeur:
            raise serializers.ValidationError(
                'Un propriétaire payeur est requis quand le frais est payé par un propriétaire.'
            )
        if payeur == 'maison' and proprietaire_payeur:
            raise serializers.ValidationError(
                "Un frais payé par la maison ne doit pas avoir de propriétaire payeur."
            )
        return attrs


class TacheSerializer(serializers.ModelSerializer):
    frais = FraisSerializer(many=True, read_only=True)
    proprietaire_responsable_detail = ProprietaireSerializer(source='proprietaire_responsable', read_only=True)
    entreprise_responsable_detail = EntrepriseSerializer(source='entreprise_responsable', read_only=True)
    cout_total = serializers.SerializerMethodField()

    class Meta:
        model = Tache
        fields = [
            'id', 'bien', 'appartement', 'titre', 'description', 'date_prevue', 'date_paiement',
            'duree_heures', 'statut', 'proprietaire_responsable', 'entreprise_responsable',
            'proprietaire_responsable_detail', 'entreprise_responsable_detail',
            'frais', 'cout_total', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def get_cout_total(self, obj):
        total = sum((f.montant_total for f in obj.frais.all()), Decimal('0'))
        if obj.proprietaire_responsable_id and obj.duree_heures:
            total += obj.duree_heures * obj.bien.valorisation_heure_proprietaire
        return total

    def validate(self, attrs):
        proprietaire_resp = attrs.get(
            'proprietaire_responsable', getattr(self.instance, 'proprietaire_responsable', None),
        )
        entreprise_resp = attrs.get(
            'entreprise_responsable', getattr(self.instance, 'entreprise_responsable', None),
        )
        if proprietaire_resp and entreprise_resp:
            raise serializers.ValidationError(
                "Une tâche ne peut être rattachée qu'à un propriétaire OU une entreprise, pas les deux."
            )
        bien = attrs.get('bien', getattr(self.instance, 'bien', None))
        appartement = attrs.get('appartement', getattr(self.instance, 'appartement', None))
        if appartement and bien and appartement.bien_id != bien.id:
            raise serializers.ValidationError("L'appartement doit appartenir au bien sélectionné.")

        duree = attrs.get('duree_heures', getattr(self.instance, 'duree_heures', None))
        date_paiement = attrs.get('date_paiement', getattr(self.instance, 'date_paiement', None))
        if proprietaire_resp and duree and not date_paiement:
            raise serializers.ValidationError(
                "La date de réalisation est requise pour valoriser le temps du propriétaire "
                'dans le bilan (elle place l\'évènement dans le grand livre).'
            )
        return attrs


class RemboursementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Remboursement
        fields = [
            'id', 'proprietaire', 'frais', 'montant', 'date_versement',
            'moyen_paiement', 'notes', 'created_by', 'created_at',
        ]
        read_only_fields = ['created_by', 'created_at']

    def validate(self, attrs):
        proprietaire = attrs.get('proprietaire', getattr(self.instance, 'proprietaire', None))
        frais_list = attrs.get('frais')
        if frais_list:
            for f in frais_list:
                if f.payeur != 'proprietaire' or f.proprietaire_payeur_id != getattr(proprietaire, 'id', None):
                    raise serializers.ValidationError(
                        f"Le frais « {f.libelle} » n'a pas été avancé par ce propriétaire."
                    )
        return attrs


class ApportInitialSerializer(serializers.ModelSerializer):
    class Meta:
        model = ApportInitial
        fields = ['id', 'bien', 'proprietaire', 'montant', 'date', 'notes', 'created_by', 'created_at']
        read_only_fields = ['created_by', 'created_at']


class VersementRevenuSerializer(serializers.ModelSerializer):
    class Meta:
        model = VersementRevenu
        fields = [
            'id', 'bien', 'proprietaire', 'montant', 'date_versement',
            'moyen_paiement', 'notes', 'created_by', 'created_at',
        ]
        read_only_fields = ['created_by', 'created_at']
