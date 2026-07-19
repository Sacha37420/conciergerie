import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, Entreprise } from '../../core/api.service';

@Component({
  selector: 'app-entreprises',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './entreprises.component.html',
  styleUrl: './entreprises.component.scss',
})
export class EntreprisesComponent implements OnInit {
  private api = inject(ApiService);

  items = signal<Entreprise[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  showModal = signal(false);
  editing = signal<Entreprise | null>(null);

  form: Entreprise = { nom: '' };

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.getEntreprises().subscribe({
      next: items => { this.items.set(items); this.loading.set(false); },
      error: () => { this.error.set('Impossible de charger les entreprises.'); this.loading.set(false); },
    });
  }

  openCreate(): void {
    this.form = { nom: '', contact_nom: '', telephone: '', email: '', specialite: '' };
    this.editing.set(null);
    this.showModal.set(true);
  }

  openEdit(item: Entreprise): void {
    this.form = { ...item };
    this.editing.set(item);
    this.showModal.set(true);
  }

  save(): void {
    const id = this.editing()?.id;
    const obs = id ? this.api.updateEntreprise(id, this.form) : this.api.createEntreprise(this.form);
    obs.subscribe({
      next: () => { this.showModal.set(false); this.load(); },
      error: () => this.error.set("Échec de l'enregistrement."),
    });
  }

  delete(item: Entreprise): void {
    if (!confirm(`Supprimer ${item.nom} ?`)) return;
    this.api.deleteEntreprise(item.id!).subscribe({ next: () => this.load() });
  }

  close(): void { this.showModal.set(false); }
}
