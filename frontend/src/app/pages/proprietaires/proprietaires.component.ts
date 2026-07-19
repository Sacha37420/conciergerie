import { Component, OnInit, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ApiService, Proprietaire } from '../../core/api.service';

@Component({
  selector: 'app-proprietaires',
  standalone: true,
  imports: [FormsModule],
  templateUrl: './proprietaires.component.html',
  styleUrl: './proprietaires.component.scss',
})
export class ProprietairesComponent implements OnInit {
  private api = inject(ApiService);

  items = signal<Proprietaire[]>([]);
  loading = signal(true);
  error = signal<string | null>(null);
  showModal = signal(false);
  editing = signal<Proprietaire | null>(null);

  form: Proprietaire = { nom: '' };

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.api.getProprietaires().subscribe({
      next: items => { this.items.set(items); this.loading.set(false); },
      error: () => { this.error.set('Impossible de charger les propriétaires.'); this.loading.set(false); },
    });
  }

  openCreate(): void {
    this.form = { nom: '', email: '', telephone: '', notes: '' };
    this.editing.set(null);
    this.showModal.set(true);
  }

  openEdit(item: Proprietaire): void {
    this.form = { ...item };
    this.editing.set(item);
    this.showModal.set(true);
  }

  save(): void {
    const id = this.editing()?.id;
    const obs = id ? this.api.updateProprietaire(id, this.form) : this.api.createProprietaire(this.form);
    obs.subscribe({
      next: () => { this.showModal.set(false); this.load(); },
      error: () => this.error.set("Échec de l'enregistrement."),
    });
  }

  delete(item: Proprietaire): void {
    if (!confirm(`Supprimer ${item.nom} ? Toutes ses quote-parts sur des biens seront aussi supprimées.`)) return;
    this.api.deleteProprietaire(item.id!).subscribe({ next: () => this.load() });
  }

  close(): void { this.showModal.set(false); }
}
