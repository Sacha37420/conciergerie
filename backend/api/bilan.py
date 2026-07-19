"""Calcul du bilan économique d'un bien : répartition des revenus entre
co-propriétaires (quote-part + investissement financier + investissement
temporel valorisé, pondérés par bien) et P&L global.

Fonctions pures (pas de vue ici) — réutilisées par BienViewSet.bilan et par
ReservationSerializer pour l'aperçu « part par séjour ». Voir la section
« Paramétrage financier & bilan économique par bien » du plan pour le détail
de la formule.
"""
from decimal import Decimal

from django.db.models import Sum

from .models import Bien, Proprietaire, Reservation, Frais, ApportInitial, VersementRevenu

ZERO = Decimal('0')


def _dec(value) -> Decimal:
    return value if value is not None else ZERO


def commission_sur(montant_revenu: Decimal, bien: Bien) -> Decimal:
    return (montant_revenu * bien.commission_gestion_pct / Decimal(100)) + bien.commission_gestion_fixe


def investissement_financier(bien: Bien, proprietaire: Proprietaire) -> Decimal:
    apports = ApportInitial.objects.filter(bien=bien, proprietaire=proprietaire).aggregate(t=Sum('montant'))['t']
    frais_non_rembourses = [
        f for f in Frais.objects
        .filter(tache__bien=bien, payeur='proprietaire', proprietaire_payeur=proprietaire)
        .select_related('tache')
        if not f.est_rembourse
    ]
    total_frais = sum((f.montant_total for f in frais_non_rembourses), ZERO)
    return _dec(apports) + total_frais


def investissement_temporel_valorise(bien: Bien, proprietaire: Proprietaire) -> Decimal:
    heures = bien.taches.filter(proprietaire_responsable=proprietaire).aggregate(t=Sum('duree_heures'))['t']
    return _dec(heures) * bien.valorisation_heure_proprietaire


def poids_proprietaire(bien: Bien, proprietaire: Proprietaire, parts, total_financier: Decimal, total_temporel: Decimal) -> Decimal:
    quote_part = next((p.quote_part_pct for p in parts if p.proprietaire_id == proprietaire.id), ZERO)
    fin = investissement_financier(bien, proprietaire)
    temp = investissement_temporel_valorise(bien, proprietaire)
    part_fin_pct = (fin / total_financier * 100) if total_financier else ZERO
    part_temp_pct = (temp / total_temporel * 100) if total_temporel else ZERO
    return (
        quote_part * bien.poids_quote_part_pct
        + part_fin_pct * bien.poids_investissement_financier_pct
        + part_temp_pct * bien.poids_investissement_temporel_pct
    ) / Decimal(100)


def revenu_net_total(bien: Bien) -> Decimal:
    total = ZERO
    reservations = Reservation.objects.filter(
        appartement__bien=bien, statut='confirmee', montant_revenu__isnull=False,
    )
    for res in reservations:
        total += res.montant_revenu - commission_sur(res.montant_revenu, bien)
    return total


def charges_maison_total(bien: Bien) -> Decimal:
    """Frais payés directement par le compte de la maison (tous, sans
    filtre de date — même périmètre que `frais_total`/`cumul_gains_depenses`
    ci-dessous). Ne compte pas les frais avancés par un propriétaire : ceux-là
    restent une dette qui lui est propre (`investissement_financier`), pas
    une charge qui réduit le pot distribué à tout le monde."""
    return sum(
        (f.montant_total for f in Frais.objects.filter(tache__bien=bien, payeur='maison').select_related('tache')),
        ZERO,
    )


def part_reservation(reservation: Reservation, proprietaire: Proprietaire) -> Decimal | None:
    """Ce que touche `proprietaire` sur ce séjour précis, selon la clé de
    répartition du bien — utilisé par l'aperçu « part par séjour ».

    Estimation BRUTE, avant déduction des charges maison (elles ne sont pas
    rattachées à un séjour précis) : peut donc être optimiste par rapport au
    `solde_du` agrégé de `bilan_bien()`, qui lui déduit `charges_maison_total`
    avant de répartir. Voir la note dans le bilan pour l'explication."""
    if reservation.montant_revenu is None:
        return None
    bien = reservation.appartement.bien
    net = reservation.montant_revenu - commission_sur(reservation.montant_revenu, bien)
    parts = list(bien.parts.select_related('proprietaire'))
    proprietaires = [p.proprietaire for p in parts]
    total_financier = sum((investissement_financier(bien, p) for p in proprietaires), ZERO)
    total_temporel = sum((investissement_temporel_valorise(bien, p) for p in proprietaires), ZERO)
    poids = poids_proprietaire(bien, proprietaire, parts, total_financier, total_temporel)
    return net * poids / Decimal(100)


def bilan_bien(bien: Bien) -> dict:
    parts = list(bien.parts.select_related('proprietaire'))
    proprietaires = [p.proprietaire for p in parts]

    total_financier = sum((investissement_financier(bien, p) for p in proprietaires), ZERO)
    total_temporel = sum((investissement_temporel_valorise(bien, p) for p in proprietaires), ZERO)
    revenu_net = revenu_net_total(bien)
    charges_maison = charges_maison_total(bien)
    # Pot réellement distribuable : revenus − commission − ce que la maison a
    # déjà dépensé pour le bien. Peut devenir négatif (année de gros travaux) :
    # c'est correct, ça signifie que les propriétaires ont collectivement
    # « emprunté » à la maison plutôt que l'inverse.
    revenu_distribuable = revenu_net - charges_maison

    lignes = []
    for part in parts:
        p = part.proprietaire
        fin = investissement_financier(bien, p)
        temp = investissement_temporel_valorise(bien, p)
        poids = poids_proprietaire(bien, p, parts, total_financier, total_temporel)
        part_revenus = revenu_distribuable * poids / Decimal(100)
        deja_verse = _dec(
            VersementRevenu.objects.filter(bien=bien, proprietaire=p).aggregate(t=Sum('montant'))['t']
        )
        lignes.append({
            'proprietaire_id': p.id,
            'proprietaire_nom': p.nom,
            'quote_part_pct': part.quote_part_pct,
            'investissement_financier': fin,
            'investissement_temporel_valorise': temp,
            'poids_pct': poids,
            'part_revenus_estimee': part_revenus,
            'deja_verse': deja_verse,
            # Dette directe (frais avancés non remboursés + apports) + part
            # de revenus estimée − ce qui a déjà été reversé. Reste souvent
            # élevé et jamais soldé si le revenu est réinvesti : normal.
            'solde_du': fin + part_revenus - deja_verse,
        })

    revenu_brut_total = _dec(
        Reservation.objects.filter(
            appartement__bien=bien, statut='confirmee', montant_revenu__isnull=False,
        ).aggregate(t=Sum('montant_revenu'))['t']
    )
    frais_total = sum(
        (f.montant_total for f in Frais.objects.filter(tache__bien=bien).select_related('tache')),
        ZERO,
    )

    return {
        'bien_id': bien.id,
        'bien_nom': bien.nom,
        'revenu_brut_total': revenu_brut_total,
        'frais_total': frais_total,
        'cumul_gains_depenses': revenu_brut_total - frais_total,
        # Détail du pot réparti entre propriétaires (revenu_net_total - charges
        # payées par la maison) — exposé pour que le bilan reste vérifiable à
        # l'oeil plutôt qu'une boîte noire.
        'charges_maison': charges_maison,
        'revenu_distribuable': revenu_distribuable,
        'proprietaires': lignes,
    }
