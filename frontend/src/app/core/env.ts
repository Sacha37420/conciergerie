/**
 * Configuration injectée au démarrage du conteneur (`assets/env.js`, écrit par
 * `nginx-entrypoint.sh`).
 */
export interface LabEnv {
  keycloakUrl?: string;
  keycloakRealm?: string;
  keycloakClientId?: string;
  appUrl?: string;
  apiUrl?: string;
}

export function labEnv(): LabEnv {
  return (window as unknown as { __env?: LabEnv }).__env ?? {};
}

/** Base de l'API de conciergerie, sans barre finale. */
export function apiUrl(): string {
  return (labEnv().apiUrl ?? 'http://localhost:8084').replace(/\/+$/, '');
}

/**
 * Les SEULES adresses auxquelles le jeton de l'utilisateur est joint
 * (`auth.interceptor.ts`, `token-scope.ts`) :
 *  - l'API de conciergerie ;
 *  - l'API de fichiers de `storage`, que le navigateur d'un co-propriétaire
 *    appelle en direct pour lire une facture (URL rendue par le backend,
 *    `FraisSerializer.get_facture` : `<origine de l'API>/storage-api/api/files/…`).
 */
export function tokenBases(): string[] {
  const api = apiUrl();
  const bases = [api];
  try {
    bases.push(`${new URL(api, window.location.origin).origin}/storage-api/api/files`);
  } catch {
    /* apiUrl illisible : seulement l'API (qui ne recevra pas non plus de jeton) */
  }
  return bases;
}
