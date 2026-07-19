from decimal import Decimal

from rest_framework import serializers
from .models import (
    Proprietaire, Entreprise, Bien, PartProprietaire, Appartement, Reservation,
    Tache, Frais,
)


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
        fields = ['id', 'bien', 'proprietaire', 'proprietaire_detail', 'quote_part_pct']


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
    quote_part_totale = serializers.DecimalField(max_digits=6, decimal_places=2, read_only=True)

    class Meta:
        model = Bien
        fields = [
            'id', 'nom', 'adresse', 'ville', 'code_postal', 'description',
            'commission_gestion_pct', 'commission_gestion_fixe',
            'valorisation_heure_proprietaire',
            'poids_quote_part_pct', 'poids_investissement_financier_pct',
            'poids_investissement_temporel_pct',
            'parts', 'appartements', 'quote_part_totale', 'created_at',
        ]
        read_only_fields = ['created_at']

    def validate(self, attrs):
        poids_keys = [
            'poids_quote_part_pct',
            'poids_investissement_financier_pct',
            'poids_investissement_temporel_pct',
        ]
        values = [
            attrs[k] if k in attrs else getattr(self.instance, k, None)
            for k in poids_keys
        ]
        if all(v is not None for v in values) and abs(sum(values) - 100) > 1:
            raise serializers.ValidationError(
                'La somme des poids (quote-part + investissement financier + '
                'investissement temporel) doit être égale à 100.'
            )
        return attrs


class ReservationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reservation
        fields = [
            'id', 'appartement', 'source', 'uid_externe', 'date_debut', 'date_fin',
            'libelle', 'statut', 'montant_revenu', 'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = ['uid_externe', 'created_at', 'updated_at']


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
            'id', 'bien', 'appartement', 'titre', 'description', 'date_prevue',
            'duree_heures', 'statut', 'proprietaire_responsable', 'entreprise_responsable',
            'proprietaire_responsable_detail', 'entreprise_responsable_detail',
            'frais', 'cout_total', 'created_by', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']

    def get_cout_total(self, obj):
        return sum((f.montant_total for f in obj.frais.all()), Decimal('0'))

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
        return attrs
