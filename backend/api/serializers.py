from rest_framework import serializers
from .models import Proprietaire, Entreprise, Bien, PartProprietaire, Appartement


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
