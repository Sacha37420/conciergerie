import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { KeycloakService } from '../../core/keycloak.service';
import { ApiService, Me, Proprietaire, Frais, Remboursement } from '../../core/api.service';

interface Groupe {
  proprietaire: Proprietaire;
  frais: Frais[];
  total: number;
}

@Component({
  selector: 'app-remboursements',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './remboursements.component.html',
  styleUrl: './remboursements.component.scss',
})
export class RemboursementsComponent implements OnInit {
  private api = inject(ApiService);
  protected kc = inject(KeycloakService);

  me = signal<Me | null>(null);
  proprietaires = signal<Proprietaire[]>([]);
  fraisNonRembourses = signal<Frais[]>([]);
  historique = signal<Remboursement[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);

  selection = new Set<number>();

  showModal = signal(false);
  form: Partial<Remboursement> = {};

  get isManager(): boolean { return this.kc.isManager; }

  get groupes(): Groupe[] {
    const parId = new Map<number, Frais[]>();
    for (const f of this.fraisNonRembourses()) {
      const id = f.proprietaire_payeur!;
      if (!parId.has(id)) parId.set(id, []);
      parId.get(id)!.push(f);
    }
    return Array.from(parId.entries()).map(([id, frais]) => ({
      proprietaire: this.proprietaires().find(p => p.id === id) ?? { id, nom: '—' },
      frais,
      total: frais.reduce((s, f) => s + Number(f.montant_total ?? 0), 0),
    }));
  }

  ngOnInit(): void {
    this.api.getMe().subscribe({
      next: m => { this.me.set(m); this.afterMe(); },
      error: () => this.afterMe(),
    });
  }

  private afterMe(): void {
    if (this.isManager) {
      this.api.getProprietaires().subscribe({ next: p => this.proprietaires.set(p) });
    }
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.getFraisList().subscribe({
      next: items => {
        let pending = items.filter(f => f.payeur === 'proprietaire' && !f.est_rembourse);
        if (!this.isManager) {
          const monId = this.me()?.proprietaire?.id;
          pending = pending.filter(f => f.proprietaire_payeur === monId);
        }
        this.fraisNonRembourses.set(pending);
        this.loading.set(false);
      },
      error: () => { this.error.set('Impossible de charger les frais.'); this.loading.set(false); },
    });
    this.api.getRemboursements().subscribe({ next: items => this.historique.set(items) });
  }

  toggle(id: number): void {
    if (this.selection.has(id)) this.selection.delete(id);
    else this.selection.add(id);
  }

  openRembourser(groupe: Groupe): void {
    const choisis = groupe.frais.filter(f => this.selection.has(f.id!));
    const cible = choisis.length > 0 ? choisis : groupe.frais;
    this.form = {
      proprietaire: groupe.proprietaire.id,
      frais: cible.map(f => f.id!),
      montant: cible.reduce((s, f) => s + Number(f.montant_total ?? 0), 0),
      date_versement: new Date().toISOString().slice(0, 10),
      moyen_paiement: 'virement',
      notes: '',
    };
    this.showModal.set(true);
  }

  save(): void {
    this.api.createRemboursement(this.form).subscribe({
      next: () => { this.showModal.set(false); this.selection.clear(); this.load(); },
      error: () => this.error.set("Échec de l'enregistrement du remboursement."),
    });
  }

  deleteRemboursement(r: Remboursement): void {
    if (!confirm('Annuler ce remboursement ? Les frais associés redeviendront « non remboursés ».')) return;
    this.api.deleteRemboursement(r.id!).subscribe({ next: () => this.load() });
  }

  close(): void { this.showModal.set(false); }
}
