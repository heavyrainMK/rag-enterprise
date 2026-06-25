// ##############################################################
// # Nom ......... : api.ts
// # Rôle ........ : Couche d'accès à l'API FastAPI côté frontend
// #                 React. Centralise l'authentification (jeton
// #                 JWT), le streaming RAG (SSE), la gestion des
// #                 documents, l'historique et les routes admin.
// # Auteur ...... : Maxim Khomenko
// # Version ..... : V1.1.0 du 23/06/2026
// # Licence ..... : Réalisé dans le cadre d'un projet de fin de
// #                 licence en Informatique (L3)
// # Usage ....... : Importé par les composants React (App, VueAdmin).
// # Dépendances . : API FastAPI (même origine en prod, proxy en dev).
// ##############################################################

const API_BASE = ""; // proxy en dev, même origine en prod

// --- Stockage du token JWT (en mémoire + localStorage) ---
let token: string | null = localStorage.getItem("jwt");

export function getToken() {
  return token;
}

export function setToken(nouveau: string | null) {
  token = nouveau;
  if (nouveau) localStorage.setItem("jwt", nouveau);
  else localStorage.removeItem("jwt");
}

export function deconnexion() {
  setToken(null);
}

function authHeaders(): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// --- Construit une Error à partir d'une réponse en échec ---
// Tente de lire le champ 'detail' renvoyé par FastAPI ; retombe sur
// un message générique si le corps n'est pas du JSON exploitable.
async function erreurDepuisReponse(reponse: Response): Promise<Error> {
  let detail = `Erreur (${reponse.status}).`;
  try {
    const data = await reponse.json();
    if (data.detail) detail = data.detail;
  } catch {
    // réponse non-JSON : on garde le message générique
  }
  return new Error(detail);
}

// ---------------------------------------------------------------------------
// Authentification
// ---------------------------------------------------------------------------
export interface InfoConnexion {
  access_token: string;
  token_type: string;
  nom_utilisateur: string;
  role: string;
}

// --- Connexion : POST /auth/login (form-data OAuth2) ---
export async function login(username: string, password: string): Promise<InfoConnexion> {
  const corps = new URLSearchParams();
  corps.append("username", username);
  corps.append("password", password);

  const reponse = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: corps,
  });

  if (!reponse.ok) {
    throw new Error("Identifiants incorrects.");
  }

  const data = (await reponse.json()) as InfoConnexion;
  setToken(data.access_token);
  return data;
}

// --- Profil de l'utilisateur connecté : GET /auth/me ---
export interface Profil {
  id: number;
  nom_utilisateur: string;
  role: string;
  actif: boolean;
  cree_le: string;
}

export async function monProfil(): Promise<Profil> {
  const reponse = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Session expirée.");
  return (await reponse.json()) as Profil;
}

// ---------------------------------------------------------------------------
// Streaming RAG
// ---------------------------------------------------------------------------
export type EvenementSSE =
  | { type: "token"; content: string }
  | { type: "no_context"; content: string }
  | { type: "error"; content: string }
  | {
      type: "done";
      sources: string[];
      private_sources: string[];
      shared_sources: string[];
    };

// --- Streaming : POST /chat/stream, lecture token par token ---
export async function* streamerChat(
  question: string,
  sessionId?: string,
): AsyncGenerator<EvenementSSE> {
  const reponse = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
    },
    body: JSON.stringify({ question, session_id: sessionId ?? null }),
  });

  if (!reponse.ok || !reponse.body) {
    throw new Error(`Erreur serveur (${reponse.status}).`);
  }

  const lecteur = reponse.body.getReader();
  const decodeur = new TextDecoder();
  let tampon = "";

  while (true) {
    const { done, value } = await lecteur.read();
    if (done) break;

    tampon += decodeur.decode(value, { stream: true });

    const blocs = tampon.split("\n\n");
    tampon = blocs.pop() ?? "";

    for (const bloc of blocs) {
      const ligne = bloc.split("\n").find((l) => l.startsWith("data: "));
      if (!ligne) continue;
      try {
        yield JSON.parse(ligne.slice(6)) as EvenementSSE;
      } catch {
        // bloc partiel ou non-JSON, on ignore
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Documents : GET /documents, POST /documents/upload, DELETE /documents/...
// ---------------------------------------------------------------------------
export interface InfoDocument {
  nom_fichier: string;
  taille_ko: number;
  extension: string;
  scope: "shared" | "private";
}

export interface ListeDocuments {
  documents: InfoDocument[];
  total: number;
  nb_partages: number;
  nb_prives: number;
}

export async function listerDocuments(): Promise<ListeDocuments> {
  const reponse = await fetch(`${API_BASE}/documents/`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Impossible de charger les documents.");
  return (await reponse.json()) as ListeDocuments;
}

export interface ReponseTeleversement {
  nom_fichier: string;
  scope: string;
  message: string;
  ingestion_lancee: boolean;
}

// scope = "shared" (admin) ou "private"
export async function televerserDocument(
  fichier: File,
  scope: "shared" | "private" = "private",
): Promise<ReponseTeleversement> {
  const corps = new FormData();
  corps.append("file", fichier);

  const reponse = await fetch(
    `${API_BASE}/documents/upload?scope=${scope}`,
    {
      method: "POST",
      headers: authHeaders(), // NE PAS fixer Content-Type : le boundary FormData est auto
      body: corps,
    },
  );

  if (!reponse.ok) {
    throw await erreurDepuisReponse(reponse);
  }

  return (await reponse.json()) as ReponseTeleversement;
}

export async function supprimerDocument(
  scope: "shared" | "private",
  nomFichier: string,
): Promise<void> {
  const reponse = await fetch(
    `${API_BASE}/documents/${scope}/${encodeURIComponent(nomFichier)}`,
    { method: "DELETE", headers: authHeaders() },
  );
  if (!reponse.ok) {
    throw await erreurDepuisReponse(reponse);
  }
}

// ---------------------------------------------------------------------------
// Historique : GET /history, DELETE /history/{id}, DELETE /history
// ---------------------------------------------------------------------------
export interface Conversation {
  id: number;
  question: string;
  reponse: string;
  sources: string[];
  utilisateur: string | null;
  session_id: string | null;
  cree_le: string;
}

export interface ReponseHistorique {
  conversations: Conversation[];
  total: number;
  limite: number;
  decalage: number;
}

export async function consulterHistorique(
  limite = 20,
  decalage = 0,
  recherche?: string,
): Promise<ReponseHistorique> {
  const params = new URLSearchParams({
    limite: String(limite),
    decalage: String(decalage),
  });
  if (recherche) params.append("recherche", recherche);

  const reponse = await fetch(`${API_BASE}/history/?${params}`, {
    headers: authHeaders(),
  });
  if (!reponse.ok) throw new Error("Impossible de charger l'historique.");
  return (await reponse.json()) as ReponseHistorique;
}

export async function supprimerConversation(id: number): Promise<void> {
  const reponse = await fetch(`${API_BASE}/history/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!reponse.ok) throw new Error("Suppression impossible.");
}

export async function viderHistorique(): Promise<void> {
  const reponse = await fetch(`${API_BASE}/history/`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!reponse.ok) throw new Error("Impossible de vider l'historique.");
}

// ---------------------------------------------------------------------------
// Administration : GET /admin/stats, /admin/users, /admin/activity
// (toutes réservées aux admins)
// ---------------------------------------------------------------------------
export interface StatsSysteme {
  nb_utilisateurs: number;
  nb_utilisateurs_actifs: number;
  nb_admins: number;
  nb_conversations: number;
  conversations_aujourdhui: number;
  conversations_semaine: number;
  nb_documents: number;
  nb_morceaux: number;
}

export interface StatsUtilisateur {
  id: number;
  nom_utilisateur: string;
  role: string;
  actif: boolean;
  cree_le: string;
  nb_conversations: number;
}

export interface ActiviteRecente {
  id: number;
  nom_utilisateur: string;
  question: string;
  sources: string[];
  cree_le: string;
}

export async function statsSysteme(): Promise<StatsSysteme> {
  const reponse = await fetch(`${API_BASE}/admin/stats`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Impossible de charger les statistiques.");
  return (await reponse.json()) as StatsSysteme;
}

export async function statsUtilisateurs(): Promise<StatsUtilisateur[]> {
  const reponse = await fetch(`${API_BASE}/admin/users`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Impossible de charger les utilisateurs.");
  return (await reponse.json()) as StatsUtilisateur[];
}

export async function activiteRecente(): Promise<ActiviteRecente[]> {
  const reponse = await fetch(`${API_BASE}/admin/activity`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Impossible de charger l'activité.");
  return (await reponse.json()) as ActiviteRecente[];
}

// ---------------------------------------------------------------------------
// Santé du système : GET /health (route publique, pas de JWT requis)
// ---------------------------------------------------------------------------
export interface EtatSante {
  statut: string;
  nb_documents: number;
}

export async function etatSante(): Promise<EtatSante> {
  const reponse = await fetch(`${API_BASE}/health`);
  if (!reponse.ok) throw new Error("Service indisponible.");
  return (await reponse.json()) as EtatSante;
}