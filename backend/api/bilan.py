"""Bilan économique d'un bien : grand livre de capital.

Modèle « compte courant d'associés » plutôt qu'une formule de répartition
figée : chaque évènement financier du bien pousse le capital total et/ou le
capital de tel ou tel propriétaire, et la répartition entre co-propriétaires
est l'état courant de ce grand livre (`capital_propriétaire / capital_total`),
pas un pourcentage stocké. Rejoué chronologiquement (date_paiement fait foi
partout), en partant de capital_total = 0 avant le premier évènement.

Deux familles d'évènements :
- **Spécifiques** à un propriétaire (touchent SON capital, pas celui des
  autres) : apport initial, versement de revenus (retrait), part
  « investie » du travail valorisé d'une tâche.
- **Proportionnels** (touchent le capital de TOUT LE MONDE au prorata de
  leur part actuelle, car la dépense/le revenu est collectif) : revenu net
  d'un séjour, dépense payée par la maison, remboursement d'un propriétaire
  (la dépense sous-jacente était collective — lui ne fait que récupérer sa
  mise), part « payée par le capital » du travail valorisé d'une tâche.

Le travail valorisé d'une tâche (proprietaire_responsable + duree_heures)
génère donc DEUX évènements simultanés (même date_paiement) qui s'annulent
sur le total mais déplacent de la valeur vers la personne qui a travaillé —
exactement comme si la maison la payait pour ce travail, financée par tous
au prorata.
"""
from decimal import Decimal, ROUND_HALF_UP

from .models import Bien, Proprietaire, Reservation, Tache, Frais, Remboursement, ApportInitial, VersementRevenu

ZERO = Decimal('0')
CENT = Decimal('0.01')


def _dec(value) -> Decimal:
    return value if value is not None else ZERO


def commission_sur(montant_revenu: Decimal, bien: Bien) -> Decimal:
    return (montant_revenu * bien.commission_gestion_pct / Decimal(100)) + bien.commission_gestion_fixe


def _construire_evenements(bien: Bien) -> list[dict]:
    """Liste brute des évènements du bien, non triée."""
    evenements = []

    for apport in ApportInitial.objects.filter(bien=bien):
        evenements.append({
            'date': apport.date, 'proprietaire_id': apport.proprietaire_id, 'montant': apport.montant,
        })

    for res in Reservation.objects.filter(
        appartement__bien=bien, statut='confirmee',
        montant_revenu__isnull=False, date_paiement__isnull=False,
    ):
        net = res.montant_revenu - commission_sur(res.montant_revenu, bien)
        evenements.append({
            'date': res.date_paiement, 'proprietaire_id': None, 'montant': net, 'reservation_id': res.id,
        })

    for f in Frais.objects.filter(
        tache__bien=bien, payeur='maison', date_paiement__isnull=False,
    ).select_related('tache'):
        evenements.append({'date': f.date_paiement, 'proprietaire_id': None, 'montant': -f.montant_total})

    for r in Remboursement.objects.filter(frais__tache__bien=bien).distinct().prefetch_related('frais__tache'):
        montant_bien = sum(
            (f.montant_total for f in r.frais.all() if f.tache.bien_id == bien.id), ZERO,
        )
        if montant_bien:
            evenements.append({'date': r.date_versement, 'proprietaire_id': None, 'montant': -montant_bien})

    for t in Tache.objects.filter(
        bien=bien, proprietaire_responsable__isnull=False,
        duree_heures__isnull=False, date_paiement__isnull=False,
    ):
        valorisation = t.duree_heures * bien.valorisation_heure_proprietaire
        # Investi par la personne (capital spécifique) ET payé par le
        # capital collectif (proportionnel) — s'annule sur le total, déplace
        # de la valeur vers elle. Même date : le spécifique est traité en
        # premier (voir _rejouer) pour que le collectif se répartisse sur un
        # capital déjà à jour.
        evenements.append({'date': t.date_paiement, 'proprietaire_id': t.proprietaire_responsable_id, 'montant': valorisation})
        evenements.append({'date': t.date_paiement, 'proprietaire_id': None, 'montant': -valorisation})

    for v in VersementRevenu.objects.filter(bien=bien):
        evenements.append({'date': v.date_versement, 'proprietaire_id': v.proprietaire_id, 'montant': -v.montant})

    return evenements


def _rejouer(bien: Bien) -> dict:
    """Rejoue le grand livre du bien et retourne l'état final + le détail
    par réservation (pour l'aperçu « part par séjour »)."""
    proprietaires = [part.proprietaire for part in bien.parts.select_related('proprietaire')]
    capital = {p.id: ZERO for p in proprietaires}
    capital_total = ZERO
    detail_par_reservation: dict[int, dict[int, Decimal]] = {}

    # Tri stable : à date égale, les évènements spécifiques (apport/versement/
    # travail investi) passent avant les proportionnels, pour que la part
    # collective d'un travail valorisé se répartisse sur un capital qui
    # inclut déjà la part investie de la personne concernée.
    evenements = sorted(
        _construire_evenements(bien),
        key=lambda e: (e['date'], e['proprietaire_id'] is None),
    )

    for e in evenements:
        montant = e['montant']
        if e['proprietaire_id'] is not None:
            capital[e['proprietaire_id']] = capital.get(e['proprietaire_id'], ZERO) + montant
            capital_total += montant
            continue

        if not proprietaires:
            continue

        if capital_total != 0:
            fractions = {p.id: capital[p.id] / capital_total for p in proprietaires}
        else:
            # Amorçage (capital_total = 0, ex: revenu encaissé avant tout
            # apport initial) : répartition égale faute de mieux.
            fractions = {p.id: Decimal(1) / len(proprietaires) for p in proprietaires}

        reste = montant
        for i, p in enumerate(proprietaires):
            if i == len(proprietaires) - 1:
                part = reste  # le dernier récupère l'arrondi résiduel, jamais de centime perdu
            else:
                part = (montant * fractions[p.id]).quantize(CENT, rounding=ROUND_HALF_UP)
                reste -= part
            capital[p.id] += part
            if 'reservation_id' in e:
                detail_par_reservation.setdefault(e['reservation_id'], {})[p.id] = part
        capital_total += montant

    return {'capital': capital, 'capital_total': capital_total, 'detail_par_reservation': detail_par_reservation}


def part_reservation(reservation: Reservation, proprietaire: Proprietaire) -> Decimal | None:
    """Ce que ce séjour a effectivement rapporté à `proprietaire` dans le
    grand livre — reflète la répartition réelle au moment de l'évènement,
    pas une estimation indépendante recalculée à part."""
    if reservation.montant_revenu is None or reservation.date_paiement is None:
        return None
    bien = reservation.appartement.bien
    etat = _rejouer(bien)
    return etat['detail_par_reservation'].get(reservation.id, {}).get(proprietaire.id)


def bilan_bien(bien: Bien) -> dict:
    parts = list(bien.parts.select_related('proprietaire'))
    etat = _rejouer(bien)
    capital_total = etat['capital_total']

    lignes = []
    for part in parts:
        p = part.proprietaire
        cap = etat['capital'].get(p.id, ZERO)
        quote_part_pct = (cap / capital_total * 100) if capital_total > 0 else None
        lignes.append({
            'proprietaire_id': p.id,
            'proprietaire_nom': p.nom,
            'capital': cap,
            'quote_part_pct': quote_part_pct,
        })

    revenu_brut_total = ZERO
    for res in Reservation.objects.filter(
        appartement__bien=bien, statut='confirmee', montant_revenu__isnull=False,
    ):
        revenu_brut_total += res.montant_revenu

    frais_total = sum(
        (f.montant_total for f in Frais.objects.filter(tache__bien=bien).select_related('tache')), ZERO,
    )
    travail_valorise_total = sum(
        (t.duree_heures * bien.valorisation_heure_proprietaire
         for t in Tache.objects.filter(bien=bien, proprietaire_responsable__isnull=False, duree_heures__isnull=False)),
        ZERO,
    )
    frais_total += travail_valorise_total

    return {
        'bien_id': bien.id,
        'bien_nom': bien.nom,
        'revenu_brut_total': revenu_brut_total,
        'frais_total': frais_total,
        'cumul_gains_depenses': revenu_brut_total - frais_total,
        'capital_total': capital_total,
        'proprietaires': lignes,
    }
