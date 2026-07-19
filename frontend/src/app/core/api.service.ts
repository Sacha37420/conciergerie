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
  quote_part_pct: number | string;
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
  valorisation_heure_proprietaire?: number | string;
  poids_quote_part_pct?: number | string;
  poids_investissement_financier_pct?: number | string;
  poids_investissement_temporel_pct?: number | string;
  parts?: PartProprietaire[];
  appartements?: Appartement[];
  quote_part_totale?: number | string;
  created_at?: string;
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
}
