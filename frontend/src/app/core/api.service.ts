import { Injectable, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

interface EnvWindow {
  __env?: { apiUrl?: string };
}

export interface Me {
  email: string;
  username: string;
  groups: string[];
  is_manager: boolean;
  proprietaire: Proprietaire | null;
}

export interface Proprietaire {
  id?: number;
  nom: string;
  email?: string;
  telephone?: string;
  notes?: string;
}

export interface Entreprise {
  id?: number;
  nom: string;
  contact_nom?: string;
  telephone?: string;
  email?: string;
  specialite?: string;
}

export interface PartProprietaire {
  id?: number;
  bien: number;
  proprietaire: number;
  proprietaire_detail?: Proprietaire;
}

export interface Appartement {
  id?: number;
  bien: number;
  nom: string;
  capacite?: number | null;
  description?: string;
  airbnb_ical_url?: string;
  dernier_sync_at?: string | null;
  dernier_sync_erreur?: string;
}

export interface Bien {
  id?: number;
  nom: string;
  adresse?: string;
  ville?: string;
  code_postal?: string;
  description?: string;
  commission_gestion_pct?: number | string;
  commission_gestion_fixe?: number | string;
  /** €/h pour valoriser le temps propriétaire dans le bilan. Vide à la création = SMIC/2 courant (calculé côté serveur). */
  valorisation_heure_proprietaire?: number | string;
  parts?: PartProprietaire[];
  appartements?: Appartement[];
  created_at?: string;
}

export interface PartReservation {
  proprietaire_id: number;
  proprietaire_nom: string;
  montant: number | string | null;
}

export interface Reservation {
  id?: number;
  appartement: number;
  source: 'airbnb' | 'direct' | 'autre';
  uid_externe?: string;
  date_debut: string;
  date_fin: string;
  libelle?: string;
  statut: 'confirmee' | 'annulee';
  montant_revenu?: number | string | null;
  /** Date d'encaissement — requise avec montant_revenu (place l'évènement dans le grand livre du bilan). */
  date_paiement?: string | null;
  notes?: string;
  parts_proprietaires?: PartReservation[];
  created_at?: string;
  updated_at?: string;
}

export interface Remboursement {
  id?: number;
  proprietaire: number;
  frais: number[];
  montant: number | string;
  date_versement: string;
  moyen_paiement: 'virement' | 'especes' | 'cheque' | 'autre';
  notes?: string;
  created_by?: string;
  created_at?: string;
}

export interface ApportInitial {
  id?: number;
  bien: number;
  proprietaire: number;
  montant: number | string;
  date: string;
  notes?: string;
  created_by?: string;
  created_at?: string;
}

export interface VersementRevenu {
  id?: number;
  bien: number;
  proprietaire: number;
  montant: number | string;
  date_versement: string;
  moyen_paiement: 'virement' | 'especes' | 'cheque' | 'autre';
  notes?: string;
  created_by?: string;
  created_at?: string;
}

export interface BilanLigne {
  proprietaire_id: number;
  proprietaire_nom: string;
  /** Capital possédé par cette personne dans le grand livre — la « quote-part » n'est plus stockée, c'est capital / capital_total. */
  capital: number | string;
  quote_part_pct: number | string | null;
}

export interface Bilan {
  bien_id: number;
  bien_nom: string;
  revenu_brut_total: number | string;
  frais_total: number | string;
  cumul_gains_depenses: number | string;
  capital_total: number | string;
  proprietaires: BilanLigne[];
}

export interface Frais {
  id?: number;
  tache: number;
  libelle: string;
  montant_fixe: number | string;
  taux_horaire: number | string;
  payeur: 'maison' | 'proprietaire';
  proprietaire_payeur?: number | null;
  proprietaire_payeur_detail?: Proprietaire;
  facture?: string | null;
  date_paiement?: string | null;
  notes?: string;
  montant_total?: number | string;
  est_rembourse?: boolean;
  created_by?: string;
  created_at?: string;
}

export interface Tache {
  id?: number;
  bien: number;
  appartement?: number | null;
  titre: string;
  description?: string;
  date_prevue?: string | null;
  duree_heures?: number | string | null;
  /** Date de réalisation — requise si proprietaire_responsable + duree_heures sont renseignés (valorisation dans le grand livre). */
  date_paiement?: string | null;
  statut: 'a_faire' | 'en_cours' | 'terminee' | 'annulee';
  proprietaire_responsable?: number | null;
  entreprise_responsable?: number | null;
  proprietaire_responsable_detail?: Proprietaire;
  entreprise_responsable_detail?: Entreprise;
  frais?: Frais[];
  cout_total?: number | string;
  created_by?: string;
  created_at?: string;
  updated_at?: string;
}

@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  private get base(): string {
    return (window as unknown as EnvWindow).__env?.apiUrl
      ?? 'http://localhost:8084';
  }

  private url(path: string): string {
    return `${this.base}/api/${path}`;
  }

  getMe(): Observable<Me> {
    return this.http.get<Me>(this.url('me/'));
  }

  // Propriétaires
  getProprietaires(): Observable<Proprietaire[]> {
    return this.http.get<Proprietaire[]>(this.url('proprietaires/'));
  }
  createProprietaire(data: Proprietaire): Observable<Proprietaire> {
    return this.http.post<Proprietaire>(this.url('proprietaires/'), data);
  }
  updateProprietaire(id: number, data: Proprietaire): Observable<Proprietaire> {
    return this.http.put<Proprietaire>(this.url(`proprietaires/${id}/`), data);
  }
  deleteProprietaire(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`proprietaires/${id}/`));
  }

  // Entreprises
  getEntreprises(): Observable<Entreprise[]> {
    return this.http.get<Entreprise[]>(this.url('entreprises/'));
  }
  createEntreprise(data: Entreprise): Observable<Entreprise> {
    return this.http.post<Entreprise>(this.url('entreprises/'), data);
  }
  updateEntreprise(id: number, data: Entreprise): Observable<Entreprise> {
    return this.http.put<Entreprise>(this.url(`entreprises/${id}/`), data);
  }
  deleteEntreprise(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`entreprises/${id}/`));
  }

  // Biens
  getBiens(): Observable<Bien[]> {
    return this.http.get<Bien[]>(this.url('biens/'));
  }
  getBien(id: number): Observable<Bien> {
    return this.http.get<Bien>(this.url(`biens/${id}/`));
  }
  createBien(data: Bien): Observable<Bien> {
    return this.http.post<Bien>(this.url('biens/'), data);
  }
  updateBien(id: number, data: Bien): Observable<Bien> {
    return this.http.patch<Bien>(this.url(`biens/${id}/`), data);
  }
  deleteBien(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`biens/${id}/`));
  }

  // Quote-parts (co-propriété)
  createPartProprietaire(data: PartProprietaire): Observable<PartProprietaire> {
    return this.http.post<PartProprietaire>(this.url('parts-proprietaire/'), data);
  }
  updatePartProprietaire(id: number, data: Partial<PartProprietaire>): Observable<PartProprietaire> {
    return this.http.patch<PartProprietaire>(this.url(`parts-proprietaire/${id}/`), data);
  }
  deletePartProprietaire(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`parts-proprietaire/${id}/`));
  }

  // Appartements
  getAppartements(bienId?: number): Observable<Appartement[]> {
    const suffix = bienId ? `appartements/?bien=${bienId}` : 'appartements/';
    return this.http.get<Appartement[]>(this.url(suffix));
  }
  createAppartement(data: Appartement): Observable<Appartement> {
    return this.http.post<Appartement>(this.url('appartements/'), data);
  }
  updateAppartement(id: number, data: Partial<Appartement>): Observable<Appartement> {
    return this.http.patch<Appartement>(this.url(`appartements/${id}/`), data);
  }
  deleteAppartement(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`appartements/${id}/`));
  }

  // Réservations
  getReservations(bienId?: number): Observable<Reservation[]> {
    const suffix = bienId ? `reservations/?bien=${bienId}` : 'reservations/';
    return this.http.get<Reservation[]>(this.url(suffix));
  }
  createReservation(data: Reservation): Observable<Reservation> {
    return this.http.post<Reservation>(this.url('reservations/'), data);
  }
  updateReservation(id: number, data: Partial<Reservation>): Observable<Reservation> {
    return this.http.patch<Reservation>(this.url(`reservations/${id}/`), data);
  }
  deleteReservation(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`reservations/${id}/`));
  }
  syncAirbnb(): Observable<{ log: string; erreurs: string }> {
    return this.http.post<{ log: string; erreurs: string }>(this.url('sync/airbnb/'), {});
  }

  // Tâches
  getTaches(filters?: { bien?: number; appartement?: number; statut?: string }): Observable<Tache[]> {
    const params = new URLSearchParams();
    if (filters?.bien) params.set('bien', String(filters.bien));
    if (filters?.appartement) params.set('appartement', String(filters.appartement));
    if (filters?.statut) params.set('statut', filters.statut);
    const qs = params.toString();
    return this.http.get<Tache[]>(this.url(`taches/${qs ? '?' + qs : ''}`));
  }
  createTache(data: Partial<Tache>): Observable<Tache> {
    return this.http.post<Tache>(this.url('taches/'), data);
  }
  updateTache(id: number, data: Partial<Tache>): Observable<Tache> {
    return this.http.patch<Tache>(this.url(`taches/${id}/`), data);
  }
  deleteTache(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`taches/${id}/`));
  }

  // Frais (upload de facture en multipart si un fichier est fourni)
  getFraisList(filters?: { tache?: number }): Observable<Frais[]> {
    const suffix = filters?.tache ? `frais/?tache=${filters.tache}` : 'frais/';
    return this.http.get<Frais[]>(this.url(suffix));
  }
  createFrais(data: Partial<Frais>, facture?: File | null): Observable<Frais> {
    return this.http.post<Frais>(this.url('frais/'), this.toFraisBody(data, facture));
  }
  updateFrais(id: number, data: Partial<Frais>, facture?: File | null): Observable<Frais> {
    return this.http.patch<Frais>(this.url(`frais/${id}/`), this.toFraisBody(data, facture));
  }
  deleteFrais(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`frais/${id}/`));
  }

  private toFraisBody(data: Partial<Frais>, facture?: File | null): FormData | Partial<Frais> {
    if (!facture) return data;
    const form = new FormData();
    Object.entries(data).forEach(([key, value]) => {
      if (value !== undefined && value !== null) form.append(key, String(value));
    });
    form.append('facture', facture);
    return form;
  }

  // Remboursements (frais avancés par un propriétaire)
  getRemboursements(proprietaireId?: number): Observable<Remboursement[]> {
    const suffix = proprietaireId ? `remboursements/?proprietaire=${proprietaireId}` : 'remboursements/';
    return this.http.get<Remboursement[]>(this.url(suffix));
  }
  createRemboursement(data: Partial<Remboursement>): Observable<Remboursement> {
    return this.http.post<Remboursement>(this.url('remboursements/'), data);
  }
  deleteRemboursement(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`remboursements/${id}/`));
  }

  // Apports initiaux
  getApportsInitiaux(bienId?: number): Observable<ApportInitial[]> {
    const suffix = bienId ? `apports-initiaux/?bien=${bienId}` : 'apports-initiaux/';
    return this.http.get<ApportInitial[]>(this.url(suffix));
  }
  createApportInitial(data: Partial<ApportInitial>): Observable<ApportInitial> {
    return this.http.post<ApportInitial>(this.url('apports-initiaux/'), data);
  }
  deleteApportInitial(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`apports-initiaux/${id}/`));
  }

  // Versements de revenus
  getVersementsRevenu(bienId?: number): Observable<VersementRevenu[]> {
    const suffix = bienId ? `versements-revenu/?bien=${bienId}` : 'versements-revenu/';
    return this.http.get<VersementRevenu[]>(this.url(suffix));
  }
  createVersementRevenu(data: Partial<VersementRevenu>): Observable<VersementRevenu> {
    return this.http.post<VersementRevenu>(this.url('versements-revenu/'), data);
  }
  deleteVersementRevenu(id: number): Observable<void> {
    return this.http.delete<void>(this.url(`versements-revenu/${id}/`));
  }

  // Bilan économique
  getBilan(bienId: number): Observable<Bilan> {
    return this.http.get<Bilan>(this.url(`biens/${bienId}/bilan/`));
  }
}
