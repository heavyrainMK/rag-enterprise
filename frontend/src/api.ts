// ##############################################################
// # Nom ......... : api.ts
// # Rôle ........ : Couche d'accès à l'API FastAPI côté frontend
// #                 React. Centralise l'authentification (jeton
// #                 JWT), le streaming RAG (SSE), la gestion des
// #                 documents, l'historique et les routes admin.
// # Auteur ...... : Maxim Khomenko
// # Version ..... : V1.1.0 du 26/06/2026
// # Licence ..... : Réalisé dans le cadre d'un projet de fin de
// #                 licence en Informatique (L3)
// # Usage ....... : Importé par les composants React (App, VueAdmin).
// # Dépendances . : API FastAPI (même origine en prod, proxy en dev).
// ##############################################################

// Base d'URL de l'API. Vide volontairement : en développement, le serveur
// Vite agit comme proxy et redirige les appels vers le backend ; en
// production, le frontend est servi par le backend lui-même, donc l'API
// est sur la MÊME origine. Dans les deux cas, une URL relative (ex.
// "/auth/login") suffit, d'où la chaîne vide.
const API_BASE = ""; // proxy en dev, même origine en prod

// --- Stockage du token JWT (en mémoire + localStorage) ---
// Le jeton est gardé à deux endroits complémentaires :
//   - dans la variable « token » (mémoire vive) pour un accès immédiat et
//     synchrone à chaque requête, sans relire le disque ;
//   - dans localStorage (persistant) pour que la session survive à un
//     rafraîchissement de page ou à la fermeture de l'onglet.
// Au chargement du module, on tente de récupérer un jeton déjà présent
// dans localStorage : si l'utilisateur s'était connecté précédemment, il
// reste connecté.
let token: string | null = localStorage.getItem("jwt");

// Lecture du jeton courant (utilisée ailleurs pour savoir si on est connecté).
export function getToken() {
  return token;
}

// Met à jour le jeton de façon cohérente entre la mémoire et localStorage.
// On passe TOUJOURS par cette fonction pour modifier le jeton, afin que les
// deux emplacements ne se désynchronisent jamais.
export function setToken(nouveau: string | null) {
  token = nouveau;
  // Un jeton non nul = connexion : on le persiste.
  if (nouveau) localStorage.setItem("jwt", nouveau);
  // Un jeton nul = déconnexion : on l'efface du disque.
  else localStorage.removeItem("jwt");
}

// Déconnexion : il suffit d'effacer le jeton (setToken(null) supprime aussi
// l'entrée localStorage). La prochaine requête authentifiée échouera donc,
// et l'interface renverra l'utilisateur vers l'écran de connexion.
export function deconnexion() {
  setToken(null);
}

// Construit l'en-tête d'autorisation HTTP à partir du jeton courant.
// Si un jeton existe, on renvoie { Authorization: "Bearer <jeton>" }, format
// standard attendu par le backend pour authentifier la requête. Sinon, on
// renvoie un objet vide : la requête partira sans en-tête d'auth (utile pour
// les routes publiques comme /health ou /auth/login).
function authHeaders(): Record<string, string> {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

// --- Construit une Error à partir d'une réponse en échec ---
// Tente de lire le champ 'detail' renvoyé par FastAPI ; retombe sur
// un message générique si le corps n'est pas du JSON exploitable.
//
// Pourquoi cette fonction : quand le backend FastAPI refuse une requête, il
// renvoie en général un JSON de la forme { "detail": "message explicatif" }.
// On veut afficher CE message précis à l'utilisateur (ex. « Fichier trop
// volumineux ») plutôt qu'un code d'erreur brut. Mais une réponse peut aussi
// ne pas être du JSON (erreur réseau, page d'erreur HTML…) : le try/catch
// garantit alors qu'on retombe proprement sur un message générique au lieu
// de planter.
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
// Forme exacte de la réponse renvoyée par /auth/login. Décrire ce type
// permet à TypeScript de vérifier qu'on lit bien les bons champs ensuite.
export interface InfoConnexion {
  access_token: string; // le jeton JWT à stocker et renvoyer à chaque requête
  token_type: string; // toujours "bearer" ici (convention OAuth2)
  nom_utilisateur: string;
  role: string; // "admin" ou "user" : conditionne l'accès à certaines vues
}

// --- Connexion : POST /auth/login (form-data OAuth2) ---
// Particularité : la route de login attend un encodage « form-urlencoded »
// (et non du JSON), car elle suit la convention OAuth2 « password flow »
// implémentée par FastAPI. C'est pourquoi on construit un URLSearchParams
// avec les champs « username » et « password » plutôt qu'un objet JSON.
export async function login(username: string, password: string): Promise<InfoConnexion> {
  const corps = new URLSearchParams();
  corps.append("username", username);
  corps.append("password", password);

  const reponse = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: corps,
  });

  // Identifiants invalides (ou compte inexistant) : le backend répond avec un
  // statut d'erreur. On lève un message volontairement neutre pour ne pas
  // révéler si c'est le nom ou le mot de passe qui est faux (bonne pratique
  // de sécurité : ne pas aider un attaquant à deviner les comptes existants).
  if (!reponse.ok) {
    throw new Error("Identifiants incorrects.");
  }

  const data = (await reponse.json()) as InfoConnexion;
  // Connexion réussie : on mémorise immédiatement le jeton pour que toutes
  // les requêtes suivantes soient authentifiées sans action supplémentaire.
  setToken(data.access_token);
  return data;
}

// --- Profil de l'utilisateur connecté : GET /auth/me ---
// Forme des données de profil renvoyées par le backend.
export interface Profil {
  id: number;
  nom_utilisateur: string;
  role: string;
  actif: boolean;
  cree_le: string;
}

// Récupère le profil de l'utilisateur courant. Sert notamment, au démarrage
// de l'app, à vérifier qu'un jeton stocké est encore valide : si la requête
// échoue (jeton expiré ou invalide), on considère la session comme terminée.
export async function monProfil(): Promise<Profil> {
  const reponse = await fetch(`${API_BASE}/auth/me`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Session expirée.");
  return (await reponse.json()) as Profil;
}

// ---------------------------------------------------------------------------
// Streaming RAG
// ---------------------------------------------------------------------------
// Type des événements reçus pendant le streaming d'une réponse. Le backend
// envoie une SUITE d'événements de natures différentes ; ce type « union »
// les énumère tous, ce qui permet à TypeScript de vérifier qu'on traite
// chaque cas correctement (token, absence de contexte, erreur, fin) :
//   - "token"      : un fragment de texte de la réponse, à afficher aussitôt ;
//   - "no_context" : aucune source pertinente trouvée (réponse de repli) ;
//   - "error"      : une erreur est survenue côté serveur ;
//   - "done"       : fin de génération, accompagnée des sources citées.
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
// Cette fonction est un « générateur asynchrone » (async function*) : au lieu
// de renvoyer une réponse complète d'un coup, elle PRODUIT (yield) les
// événements au fur et à mesure qu'ils arrivent. Le composant appelant peut
// ainsi afficher la réponse mot à mot, comme un assistant conversationnel.
export async function* streamerChat(
  question: string,
  sessionId?: string,
): AsyncGenerator<EvenementSSE> {
  const reponse = await fetch(`${API_BASE}/chat/stream`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(), // requête authentifiée : on ajoute le jeton JWT
    },
    // session_id permet de regrouper les échanges d'une même conversation ;
    // « ?? null » envoie explicitement null si aucune session n'est fournie.
    body: JSON.stringify({ question, session_id: sessionId ?? null }),
  });

  // On vérifie à la fois le statut HTTP ET la présence d'un corps « lisible »
  // en flux (reponse.body). Sans corps, impossible de streamer : on échoue.
  if (!reponse.ok || !reponse.body) {
    throw new Error(`Erreur serveur (${reponse.status}).`);
  }

  // Lecture du flux : le corps arrive par morceaux d'octets (« chunks »).
  //   - lecteur  : permet de lire ces morceaux un par un, à mesure qu'ils
  //                arrivent du réseau ;
  //   - decodeur : transforme les octets bruts en texte ;
  //   - tampon   : accumule le texte reçu, car un événement complet peut être
  //                réparti sur plusieurs morceaux réseau.
  const lecteur = reponse.body.getReader();
  const decodeur = new TextDecoder();
  let tampon = "";

  while (true) {
    const { done, value } = await lecteur.read();
    // « done » devient vrai quand le serveur a fini d'envoyer : on sort.
    if (done) break;

    // On ajoute le nouveau morceau (décodé en texte) à ce qu'on avait déjà.
    // L'option { stream: true } gère correctement les caractères multi-octets
    // qui seraient coupés à la frontière entre deux morceaux.
    tampon += decodeur.decode(value, { stream: true });

    // Format SSE : les événements sont séparés par une ligne vide ("\n\n").
    // On découpe donc le tampon sur ce séparateur pour isoler les événements.
    const blocs = tampon.split("\n\n");
    // Le DERNIER élément peut être un événement incomplet (le séparateur final
    // n'est pas encore arrivé) : on le retire de la liste à traiter et on le
    // remet dans le tampon, pour le compléter au prochain tour de boucle.
    tampon = blocs.pop() ?? "";

    for (const bloc of blocs) {
      // Dans un bloc SSE, la donnée utile est la ligne commençant par "data: ".
      const ligne = bloc.split("\n").find((l) => l.startsWith("data: "));
      if (!ligne) continue;
      try {
        // On retire le préfixe "data: " (6 caractères) et on parse le JSON,
        // qu'on produit ensuite au consommateur via yield.
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
// Métadonnées d'un document, telles que renvoyées par l'API.
// « scope » distingue les documents partagés (visibles de tous) des documents
// privés (visibles du seul propriétaire) : c'est le cœur de l'isolation.
export interface InfoDocument {
  nom_fichier: string;
  taille_ko: number;
  extension: string;
  scope: "shared" | "private";
}

// Réponse du listing : la liste des documents plus quelques compteurs prêts
// à afficher (total, nombre de partagés, nombre de privés).
export interface ListeDocuments {
  documents: InfoDocument[];
  total: number;
  nb_partages: number;
  nb_prives: number;
}

// Récupère la liste des documents accessibles à l'utilisateur courant
// (ses documents privés + les documents partagés).
export async function listerDocuments(): Promise<ListeDocuments> {
  const reponse = await fetch(`${API_BASE}/documents/`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Impossible de charger les documents.");
  return (await reponse.json()) as ListeDocuments;
}

// Réponse renvoyée après un téléversement réussi.
// « ingestion_lancee » indique si le document a déclenché une réindexation
// (calcul des vecteurs) afin de devenir immédiatement interrogeable.
export interface ReponseTeleversement {
  nom_fichier: string;
  scope: string;
  message: string;
  ingestion_lancee: boolean;
}

// scope = "shared" (admin) ou "private"
// Téléverse un fichier. Le scope par défaut est « private » : un utilisateur
// dépose par défaut dans son espace personnel ; seul un admin peut déposer en
// « shared ».
export async function televerserDocument(
  fichier: File,
  scope: "shared" | "private" = "private",
): Promise<ReponseTeleversement> {
  // Un envoi de fichier se fait via FormData (encodage multipart), et non en
  // JSON : c'est le format adapté au transfert de fichiers binaires.
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

  // En cas d'échec, on remonte le message « detail » précis du backend
  // (ex. extension non autorisée, droits insuffisants) plutôt qu'un message
  // générique : l'utilisateur sait exactement pourquoi son envoi a échoué.
  if (!reponse.ok) {
    throw await erreurDepuisReponse(reponse);
  }

  return (await reponse.json()) as ReponseTeleversement;
}

// Supprime un document identifié par son scope et son nom de fichier.
// encodeURIComponent protège le nom de fichier : s'il contient des caractères
// spéciaux (espaces, accents, « / »…), ils sont encodés pour ne pas casser
// l'URL ni provoquer de comportement inattendu côté serveur.
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
// Forme d'une conversation enregistrée : la question, la réponse, les sources
// citées, et des métadonnées (utilisateur, session, date de création).
export interface Conversation {
  id: number;
  question: string;
  reponse: string;
  sources: string[];
  utilisateur: string | null;
  session_id: string | null;
  cree_le: string;
}

// Réponse paginée de l'historique. « limite » et « decalage » servent à la
// pagination (voir consulterHistorique ci-dessous), « total » au comptage.
export interface ReponseHistorique {
  conversations: Conversation[];
  total: number;
  limite: number;
  decalage: number;
}

// Récupère l'historique de l'utilisateur, avec pagination et recherche.
//   - limite   : nombre maximum de conversations renvoyées (taille de page) ;
//   - decalage : nombre de conversations à sauter (pour aller « page suivante ») ;
//   - recherche: filtre optionnel sur le texte des questions.
// On construit la chaîne de requête avec URLSearchParams, qui encode
// proprement chaque paramètre, et on n'ajoute « recherche » que s'il est
// effectivement fourni.
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

// Supprime une conversation précise, identifiée par son id.
export async function supprimerConversation(id: number): Promise<void> {
  const reponse = await fetch(`${API_BASE}/history/${id}`, {
    method: "DELETE",
    headers: authHeaders(),
  });
  if (!reponse.ok) throw new Error("Suppression impossible.");
}

// Vide entièrement l'historique de l'utilisateur courant (DELETE sur la
// collection complète, sans id).
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
// Ces routes alimentent le tableau de bord administrateur. Elles sont
// protégées côté backend : un utilisateur non-admin reçoit une erreur. Les
// interfaces ci-dessous décrivent la forme exacte des données affichées.

// Statistiques globales du système (cartes chiffrées du tableau de bord).
export interface StatsSysteme {
  nb_utilisateurs: number;
  nb_utilisateurs_actifs: number;
  nb_admins: number;
  nb_conversations: number;
  conversations_aujourdhui: number;
  conversations_semaine: number;
  nb_documents: number;
  nb_morceaux: number; // nombre de fragments vectorisés dans ChromaDB
}

// Statistiques par utilisateur (tableau des utilisateurs du dashboard).
export interface StatsUtilisateur {
  id: number;
  nom_utilisateur: string;
  role: string;
  actif: boolean;
  cree_le: string;
  nb_conversations: number;
}

// Entrée d'activité récente (qui a posé quelle question, quand, avec quelles
// sources citées) : alimente la liste d'activité du tableau de bord.
export interface ActiviteRecente {
  id: number;
  nom_utilisateur: string;
  question: string;
  sources: string[];
  cree_le: string;
}

// Récupère les statistiques globales du système.
export async function statsSysteme(): Promise<StatsSysteme> {
  const reponse = await fetch(`${API_BASE}/admin/stats`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Impossible de charger les statistiques.");
  return (await reponse.json()) as StatsSysteme;
}

// Récupère la liste des utilisateurs avec leurs statistiques individuelles.
export async function statsUtilisateurs(): Promise<StatsUtilisateur[]> {
  const reponse = await fetch(`${API_BASE}/admin/users`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Impossible de charger les utilisateurs.");
  return (await reponse.json()) as StatsUtilisateur[];
}

// Récupère le flux d'activité récente (dernières questions posées).
export async function activiteRecente(): Promise<ActiviteRecente[]> {
  const reponse = await fetch(`${API_BASE}/admin/activity`, { headers: authHeaders() });
  if (!reponse.ok) throw new Error("Impossible de charger l'activité.");
  return (await reponse.json()) as ActiviteRecente[];
}

// ---------------------------------------------------------------------------
// Santé du système : GET /health (route publique, pas de JWT requis)
// ---------------------------------------------------------------------------
// Forme de la réponse de /health. « modele » a été ajouté pour que l'interface
// affiche dynamiquement le modèle de langage réellement actif (ex. "llama3.2")
// au lieu d'un nom codé en dur.
export interface EtatSante {
  statut: string;
  nb_documents: number;
  modele: string;
}

// Interroge la route publique /health. Pas d'en-tête d'authentification ici :
// la route est volontairement accessible sans jeton, car elle sert à vérifier
// que le service répond (badge « Système opérationnel ») même avant connexion.
export async function etatSante(): Promise<EtatSante> {
  const reponse = await fetch(`${API_BASE}/health`);
  if (!reponse.ok) throw new Error("Service indisponible.");
  return (await reponse.json()) as EtatSante;
}