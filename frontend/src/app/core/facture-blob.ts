/**
 * Ouvre une facture déjà téléchargée (blob authentifié, voir
 * `ApiService.downloadFacture`).
 *
 * Une facture est déposée par un co-propriétaire : son type vient de lui. Une
 * URL `blob:` hérite de l'origine de l'app — un HTML ou un SVG ouvert dans un
 * onglet y exécuterait ses scripts. Seuls les PDF et les images matricielles
 * s'ouvrent donc dans un onglet ; tout le reste est téléchargé sous un type
 * neutre.
 */
const TYPES_AFFICHABLES = ['application/pdf', 'image/png', 'image/jpeg', 'image/gif', 'image/webp'];

export function ouvrirFacture(blob: Blob, url: string): void {
  const affichable = TYPES_AFFICHABLES.includes(blob.type.split(';')[0].trim().toLowerCase());
  const objectUrl = URL.createObjectURL(
    affichable ? blob : new Blob([blob], { type: 'application/octet-stream' }),
  );
  const a = document.createElement('a');
  a.href = objectUrl;
  if (affichable) {
    a.target = '_blank';
    a.rel = 'noopener';
  } else {
    a.download = nomDeFichier(url);
  }
  a.click();
  URL.revokeObjectURL(objectUrl);
}

function nomDeFichier(url: string): string {
  try {
    return decodeURIComponent(new URL(url, window.location.origin).pathname.split('/').pop() || '') || 'facture';
  } catch {
    return 'facture';
  }
}
