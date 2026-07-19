from django.db import models


class Proprietaire(models.Model):
    """Un co-propriétaire familial. `email` relie ce contact à son compte
    Keycloak (groupe `proprietaires`) pour scoper son accès au portail."""

    nom = models.CharField(max_length=150)
    email = models.EmailField(max_length=254, unique=True, blank=True, default='')
    telephone = models.CharField(max_length=30, blank=True, default='')
    notes = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'proprietaire'
        ordering = ['nom']

    def __str__(self) -> str:
        return self.nom


class Entreprise(models.Model):
    """Entreprise extérieure (ménage, plomberie…) pouvant être rattachée à
    une tâche. Jamais de portail — pas de lien vers un compte Keycloak."""

    nom = models.CharField(max_length=150)
    contact_nom = models.CharField(max_length=150, blank=True, default='')
    telephone = models.CharField(max_length=30, blank=True, default='')
    email = models.EmailField(max_length=254, blank=True, default='')
    specialite = models.CharField(max_length=150, blank=True, default='')

    class Meta:
        db_table = 'entreprise'
        ordering = ['nom']

    def __str__(self) -> str:
        return self.nom


class Bien(models.Model):
    """Un bien immobilier, pouvant regrouper plusieurs appartements loués.
    Porte aussi le paramétrage financier utilisé par le bilan économique
    (commission de gestion, valorisation du temps propriétaire, pondération
    de la clé de répartition — voir api/bilan.py)."""

    nom = models.CharField(max_length=150)
    adresse = models.CharField(max_length=255, blank=True, default='')
    ville = models.CharField(max_length=100, blank=True, default='')
    code_postal = models.CharField(max_length=10, blank=True, default='')
    description = models.TextField(blank=True, default='')

    # ── Paramétrage financier (bilan économique) ────────────────────────────
    commission_gestion_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    commission_gestion_fixe = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    valorisation_heure_proprietaire = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    # Pondération des 3 facteurs de répartition (quote-part, investissement
    # financier non remboursé, investissement temporel valorisé). Doit
    # sommer à 100 — vérifié côté serializer, pas en contrainte DB (édition
    # progressive des propriétaires possible sans blocage transitoire).
    poids_quote_part_pct = models.DecimalField(max_digits=5, decimal_places=2, default=100)
    poids_investissement_financier_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    poids_investissement_temporel_pct = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'bien'
        ordering = ['nom']

    def __str__(self) -> str:
        return self.nom

    @property
    def quote_part_totale(self):
        total = self.parts.aggregate(total=models.Sum('quote_part_pct'))['total']
        return total or 0


class PartProprietaire(models.Model):
    """Quote-part (%) d'un propriétaire dans un bien — remplace un simple
    M2M pour permettre une co-propriété non égalitaire (ex: 60/40)."""

    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, related_name='parts')
    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.CASCADE, related_name='parts')
    quote_part_pct = models.DecimalField(max_digits=5, decimal_places=2)

    class Meta:
        db_table = 'part_proprietaire'
        ordering = ['bien', '-quote_part_pct']
        constraints = [
            models.UniqueConstraint(fields=['bien', 'proprietaire'], name='unique_part_par_bien_proprietaire'),
        ]

    def __str__(self) -> str:
        return f'{self.proprietaire} — {self.bien} ({self.quote_part_pct}%)'


class Appartement(models.Model):
    """Un appartement/lot loué au sein d'un bien. Porte l'URL iCal secrète
    Airbnb propre à cette annonce — chaque annonce a la sienne."""

    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, related_name='appartements')
    nom = models.CharField(max_length=100)
    capacite = models.PositiveSmallIntegerField(null=True, blank=True)
    description = models.TextField(blank=True, default='')
    airbnb_ical_url = models.URLField(max_length=500, blank=True, default='')
    dernier_sync_at = models.DateTimeField(null=True, blank=True)
    dernier_sync_erreur = models.TextField(blank=True, default='')

    class Meta:
        db_table = 'appartement'
        ordering = ['bien__nom', 'nom']

    def __str__(self) -> str:
        return f'{self.bien.nom} — {self.nom}'


class Reservation(models.Model):
    """Un séjour occupant un appartement. Synchronisé en lecture seule depuis
    le flux iCal Airbnb (dates uniquement — jamais voyageur ni montant), ou
    saisi manuellement (source `direct`/`autre`, `montant_revenu` renseignable
    à la main dans les deux cas)."""

    SOURCE_CHOICES = [('airbnb', 'Airbnb'), ('direct', 'Direct'), ('autre', 'Autre')]
    STATUT_CHOICES = [('confirmee', 'Confirmée'), ('annulee', 'Annulée')]

    appartement = models.ForeignKey(Appartement, on_delete=models.CASCADE, related_name='reservations')
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='airbnb')
    # UID de l'événement VEVENT iCal — vide pour une réservation saisie à la
    # main. Sert de clé d'upsert idempotent au sync (voir management command).
    uid_externe = models.CharField(max_length=255, blank=True, default='')
    date_debut = models.DateField()
    date_fin = models.DateField()
    libelle = models.CharField(max_length=255, blank=True, default='')
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default='confirmee')
    montant_revenu = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'reservation'
        ordering = ['-date_debut']
        constraints = [
            models.UniqueConstraint(
                fields=['appartement', 'uid_externe'],
                condition=~models.Q(uid_externe=''),
                name='unique_reservation_par_uid_externe',
            ),
        ]

    def __str__(self) -> str:
        return f'{self.appartement} — {self.date_debut} → {self.date_fin}'
