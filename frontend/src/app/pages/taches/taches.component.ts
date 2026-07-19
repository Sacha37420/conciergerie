import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { KeycloakService } from '../../core/keycloak.service';
import {
  ApiService, Me, Bien, Appartement, Proprietaire, Entreprise, Tache, Frais,
} from '../../core/api.service';

type Responsable = 'aucun' | 'proprietaire' | 'entreprise';

@Component({
  selector: 'app-taches',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './taches.component.html',
  styleUrl: './taches.component.scss',
})
export class TachesComponent implements OnInit {
  private api = inject(ApiService);
  protected kc = inject(KeycloakService);

  me = signal<Me | null>(null);
  biens = signal<Bien[]>([]);
  proprietaires = signal<Proprietaire[]>([]);
  entreprises = signal<Entreprise[]>([]);
  taches = signal<Tache[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  expandedId = signal<number | null>(null);

  filterBienId: number | '' = '';
  filterStatut = '';

  showTacheModal = signal(false);
  editingTache = signal<Tache | null>(null);
  tacheForm: Partial<Tache> = {};
  responsableType: Responsable = 'aucun';

  showFraisModal = signal(false);
  editingFrais = signal<Frais | null>(null);
  fraisForm: Partial<Frais> = {};
  fraisTacheId: number | null = null;
  fraisFile: File | null = null;

  get isManager(): boolean { return this.kc.isManager; }

  get appartementsDuBien(): Appartement[] {
    const bien = this.biens().find(b => b.id === this.tacheForm.bien);
    return bien?.appartements ?? [];
  }

  ngOnInit(): void {
    this.api.getMe().subscribe({ next: m => this.me.set(m) });
    this.api.getBiens().subscribe({ next: b => this.biens.set(b) });
    if (this.isManager) {
      this.api.getProprietaires().subscribe({ next: p => this.proprietaires.set(p) });
      this.api.getEntreprises().subscribe({ next: e => this.entreprises.set(e) });
    }
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.getTaches({
      bien: this.filterBienId || undefined,
      statut: this.filterStatut || undefined,
    }).subscribe({
      next: items => { this.taches.set(items); this.loading.set(false); },
      error: () => { this.error.set('Impossible de charger les tâches.'); this.loading.set(false); },
    });
  }

  toggleExpand(t: Tache): void {
    this.expandedId.set(this.expandedId() === t.id ? null : t.id!);
  }

  nomBien(id: number): string {
    return this.biens().find(b => b.id === id)?.nom ?? '—';
  }

  // ── Tâche ───────────────────────────────────────────────────────────────
  openCreateTache(): void {
    this.tacheForm = {
      bien: this.biens()[0]?.id, appartement: null, titre: '', description: '',
      date_prevue: '', duree_heures: null, statut: 'a_faire',
      proprietaire_responsable: null, entreprise_responsable: null,
    };
    this.responsableType = 'aucun';
    this.editingTache.set(null);
    this.showTacheModal.set(true);
  }

  openEditTache(t: Tache): void {
    this.tacheForm = { ...t };
    this.responsableType = t.proprietaire_responsable ? 'proprietaire' : t.entreprise_responsable ? 'entreprise' : 'aucun';
    this.editingTache.set(t);
    this.showTacheModal.set(true);
  }

  onResponsableTypeChange(): void {
    if (this.responsableType !== 'proprietaire') this.tacheForm.proprietaire_responsable = null;
    if (this.responsableType !== 'entreprise') this.tacheForm.entreprise_responsable = null;
  }

  saveTache(): void {
    const id = this.editingTache()?.id;
    const obs = id ? this.api.updateTache(id, this.tacheForm) : this.api.createTache(this.tacheForm);
    obs.subscribe({
      next: () => { this.showTacheModal.set(false); this.load(); },
      error: err => this.error.set(err?.error?.non_field_errors?.[0] ?? "Échec de l'enregistrement de la tâche."),
    });
  }

  deleteTache(t: Tache): void {
    if (!confirm(`Supprimer la tâche « ${t.titre} » et tous ses frais ?`)) return;
    this.api.deleteTache(t.id!).subscribe({ next: () => this.load() });
  }

  closeTacheModal(): void { this.showTacheModal.set(false); }

  // ── Frais ───────────────────────────────────────────────────────────────
  openCreateFrais(t: Tache): void {
    this.fraisTacheId = t.id!;
    this.fraisFile = null;
    if (this.isManager) {
      this.fraisForm = { tache: t.id, libelle: '', montant_fixe: 0, taux_horaire: 0, payeur: 'maison', proprietaire_payeur: null };
    } else {
      const monId = this.me()?.proprietaire?.id ?? null;
      this.fraisForm = { tache: t.id, libelle: '', montant_fixe: 0, taux_horaire: 0, payeur: 'proprietaire', proprietaire_payeur: monId };
    }
    this.editingFrais.set(null);
    this.showFraisModal.set(true);
  }

  openEditFrais(f: Frais): void {
    this.fraisTacheId = f.tache;
    this.fraisFile = null;
    this.fraisForm = { ...f };
    this.editingFrais.set(f);
    this.showFraisModal.set(true);
  }

  onFraisFileChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.fraisFile = input.files?.[0] ?? null;
  }

  saveFrais(): void {
    const id = this.editingFrais()?.id;
    const obs = id
      ? this.api.updateFrais(id, this.fraisForm, this.fraisFile)
      : this.api.createFrais(this.fraisForm, this.fraisFile);
    obs.subscribe({
      next: () => { this.showFraisModal.set(false); this.load(); },
      error: err => this.error.set(err?.error?.non_field_errors?.[0] ?? "Échec de l'enregistrement du frais."),
    });
  }

  deleteFrais(f: Frais): void {
    if (!confirm('Supprimer ce frais ?')) return;
    this.api.deleteFrais(f.id!).subscribe({ next: () => this.load() });
  }

  closeFraisModal(): void { this.showFraisModal.set(false); }

  peutModifierFrais(f: Frais): boolean {
    if (this.isManager) return true;
    const monId = this.me()?.proprietaire?.id;
    return !!monId && f.proprietaire_payeur === monId && !f.est_rembourse;
  }
}
