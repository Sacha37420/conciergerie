from decimal import Decimal

from rest_framework import serializers
from .models import (
    Proprietaire, Entreprise, Bien, PartProprietaire, Appartement, Reservation,
    Tache, Frais, Remboursement, ApportInitial, VersementRevenu,
)
from . import bilan as bilan_module


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
        return [
            {
                'proprietaire_id': part.proprietaire_id,
                'proprietaire_nom': part.proprietaire.nom,
                'montant': bilan_module.part_reservation(obj, part.proprietaire),
            }
            for part in bien.parts.select_related('proprietaire')
        ]


class FraisSerializer(serializers.ModelSerializer):
    montant_total = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    proprietaire_payeur_detail = ProprietaireSerializer(source='proprietaire_payeur', read_only=True)

    class Meta:
        model = Frais
        fields = [
            'id', 'tache', 'libelle', 'montant_fixe', 'taux_horaire', 'payeur',
            'proprietaire_payeur', 'proprietaire_payeur_detail', 'facture', 'date_paiement',
            'notes', 'montant_total', 'created_by', 'created_at',
        ]
        read_only_fields = ['created_by', 'created_at']

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
