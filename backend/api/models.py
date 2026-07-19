from django.db import models


class Proprietaire(models.Model):
    """Un co-propriétaire familial. `email` relie ce contact à son compte
    Keycloak (groupe `proprietaires`) pour scoper son accès au portail."""

    nom = models.CharField(max_length=150)
    # null (pas '') pour les valeurs absentes : sous unique=True, Postgres
    # autorise plusieurs NULL mais rejette plusieurs '' — un propriétaire
    # sans accès au portail (pas encore de compte Keycloak) n'a pas d'email.
    email = models.EmailField(max_length=254, unique=True, null=True, blank=True, default=None)
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


class Tache(models.Model):
    """Une tâche à titre libre (ménage, travaux…), à l'échelle du bien ou
    d'un appartement précis, rattachée à un propriétaire OU une entreprise
    extérieure (jamais les deux). `duree_heures` sert au calcul proportionnel
    des `Frais` et à l'investissement temporel du bilan économique."""

    STATUT_CHOICES = [
        ('a_faire', 'À faire'), ('en_cours', 'En cours'),
        ('terminee', 'Terminée'), ('annulee', 'Annulée'),
    ]

    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, related_name='taches')
    # Null ⇒ tâche à l'échelle du bien entier ; sinon doit appartenir à `bien`
    # (vérifié côté serializer, cf. TacheSerializer.validate).
    appartement = models.ForeignKey(
        Appartement, on_delete=models.CASCADE, related_name='taches', null=True, blank=True,
    )
    titre = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    date_prevue = models.DateField(null=True, blank=True)
    duree_heures = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    statut = models.CharField(max_length=10, choices=STATUT_CHOICES, default='a_faire')
    proprietaire_responsable = models.ForeignKey(
        Proprietaire, on_delete=models.SET_NULL, null=True, blank=True, related_name='taches',
    )
    entreprise_responsable = models.ForeignKey(
        Entreprise, on_delete=models.SET_NULL, null=True, blank=True, related_name='taches',
    )
    created_by = models.EmailField(max_length=254, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tache'
        ordering = ['-date_prevue', '-created_at']

    def __str__(self) -> str:
        return self.titre


class Frais(models.Model):
    """Un coût rattaché à une tâche, avec sa propre facture. Une tâche peut
    porter plusieurs `Frais`. Coût = fixe + (taux horaire × durée de la
    tâche). Payé par le compte de la maison ou avancé par un propriétaire —
    dans ce second cas, `facture` porte le justificatif de sa demande de
    remboursement (voir Remboursement)."""

    PAYEUR_CHOICES = [('maison', 'Maison'), ('proprietaire', 'Propriétaire')]

    tache = models.ForeignKey(Tache, on_delete=models.CASCADE, related_name='frais')
    libelle = models.CharField(max_length=200)
    montant_fixe = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    taux_horaire = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    payeur = models.CharField(max_length=15, choices=PAYEUR_CHOICES, default='maison')
    proprietaire_payeur = models.ForeignKey(
        Proprietaire, on_delete=models.SET_NULL, null=True, blank=True, related_name='frais_avances',
    )
    facture = models.FileField(upload_to='factures/%Y/%m/', null=True, blank=True)
    date_paiement = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default='')
    created_by = models.EmailField(max_length=254, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'frais'
        ordering = ['-created_at']

    @property
    def montant_total(self):
        duree = self.tache.duree_heures or 0
        return self.montant_fixe + self.taux_horaire * duree

    @property
    def est_rembourse(self) -> bool:
        # `remboursements` (related_name du M2M Remboursement.frais) n'existe
        # que si payeur='proprietaire' a effectivement été remboursé.
        manager = getattr(self, 'remboursements', None)
        return manager.exists() if manager is not None else False

    def __str__(self) -> str:
        return f'{self.libelle} ({self.montant_total} €)'


MOYEN_PAIEMENT_CHOICES = [
    ('virement', 'Virement'), ('especes', 'Espèces'),
    ('cheque', 'Chèque'), ('autre', 'Autre'),
]


class Remboursement(models.Model):
    """Versement effectué depuis le compte de la maison à un propriétaire,
    en règlement d'un ou plusieurs `Frais` qu'il a avancés. Un versement peut
    solder plusieurs frais d'un coup."""

    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.CASCADE, related_name='remboursements')
    frais = models.ManyToManyField(Frais, related_name='remboursements', blank=True)
    montant = models.DecimalField(max_digits=10, decimal_places=2)
    date_versement = models.DateField()
    moyen_paiement = models.CharField(max_length=15, choices=MOYEN_PAIEMENT_CHOICES, default='virement')
    notes = models.TextField(blank=True, default='')
    created_by = models.EmailField(max_length=254, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'remboursement'
        ordering = ['-date_versement']

    def __str__(self) -> str:
        return f'{self.proprietaire} — {self.montant} € ({self.date_versement})'


class ApportInitial(models.Model):
    """Capital investi par un propriétaire hors du circuit Tache/Frais (ex :
    apport à l'achat, gros travaux financés directement) — alimente
    l'investissement financier utilisé par le bilan économique (api/bilan.py)."""

    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, related_name='apports')
    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.CASCADE, related_name='apports')
    montant = models.DecimalField(max_digits=10, decimal_places=2)
    date = models.DateField()
    notes = models.TextField(blank=True, default='')
    created_by = models.EmailField(max_length=254, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'apport_initial'
        ordering = ['-date']

    def __str__(self) -> str:
        return f'{self.proprietaire} — {self.montant} € ({self.bien})'


class VersementRevenu(models.Model):
    """Part de revenus locatifs effectivement reversée à un propriétaire.
    Distinct de `Remboursement` (qui solde des `Frais` précis) : en pratique
    souvent absent, le revenu étant le plus souvent entièrement réinvesti."""

    bien = models.ForeignKey(Bien, on_delete=models.CASCADE, related_name='versements_revenu')
    proprietaire = models.ForeignKey(Proprietaire, on_delete=models.CASCADE, related_name='versements_revenu')
    montant = models.DecimalField(max_digits=10, decimal_places=2)
    date_versement = models.DateField()
    moyen_paiement = models.CharField(max_length=15, choices=MOYEN_PAIEMENT_CHOICES, default='virement')
    notes = models.TextField(blank=True, default='')
    created_by = models.EmailField(max_length=254, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'versement_revenu'
        ordering = ['-date_versement']

    def __str__(self) -> str:
        return f'{self.proprietaire} — {self.montant} € ({self.bien})'
