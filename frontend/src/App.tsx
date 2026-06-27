// ##############################################################
// # Nom ......... : App.tsx
// # Rôle ........ : Composant racine du frontend React de RAG
// #                 Enterprise. Gère l'authentification, la
// #                 navigation entre les vues (chat, documents,
// #                 historique, admin) et l'état du chat en
// #                 streaming. Les sous-vues sont définies plus bas.
// # Auteur ...... : Maxim Khomenko
// # Version ..... : V2.1.0 du 26/06/2026
// # Licence ..... : Réalisé dans le cadre d'un projet de fin de
// #                 licence en Informatique (L3)
// # Usage ....... : Monté par src/main.tsx.
// # Dépendances . : react, lucide-react, ./api, ./VueAdmin.
// ##############################################################

import { useEffect, useRef, useState, useCallback } from "react";
import {
  Paperclip,
  SendHorizontal,
  MessageSquare,
  BookMarked,
  Clock,
  ShieldCheck,
  Trash2,
  Upload,
  LogOut,
  RefreshCw,
  RotateCcw,
  SquarePen,
  User,
} from "lucide-react";
import {
  login,
  getToken,
  deconnexion,
  monProfil,
  streamerChat,
  listerDocuments,
  televerserDocument,
  supprimerDocument,
  consulterHistorique,
  supprimerConversation,
  viderHistorique,
  etatSante,
  type InfoDocument,
  type Conversation,
  type Profil,
} from "./api";
import VueAdmin from "./VueAdmin";

// ---------------------------------------------------------------------------
// Navigation
// ---------------------------------------------------------------------------
// Type énuméré des vues possibles de l'application. Utiliser un type fermé
// (plutôt qu'une simple chaîne) garantit qu'on ne peut afficher qu'une vue
// réellement prévue : toute faute de frappe est détectée par TypeScript.
type Vue = "chat" | "documents" | "historique" | "admin";

// Liste des entrées de la barre latérale. Centraliser la navigation dans un
// tableau (plutôt que d'écrire chaque bouton à la main) permet de générer le
// menu par une simple boucle et d'ajouter une vue en une seule ligne.
const NAV_ITEMS: { vue: Vue; icon: typeof MessageSquare; label: string }[] = [
  { vue: "chat", icon: MessageSquare, label: "Chat Principal" },
  { vue: "documents", icon: BookMarked, label: "Mes documents" },
  { vue: "historique", icon: Clock, label: "Mon historique" },
  { vue: "admin", icon: ShieldCheck, label: "Tableau de bord" },
];

// Représente un message du fil de discussion. « role » distingue la question
// de l'utilisateur de la réponse de l'assistant. Les deux champs optionnels :
//   - sansContexte : marque une réponse de repli (aucune source trouvée) ;
//   - question     : pour un message assistant, mémorise la question d'origine,
//                    ce qui permet le bouton « Reposer ».
interface Message {
  role: "user" | "assistant";
  contenu: string;
  sansContexte?: boolean;
  question?: string; // pour un message assistant : la question qui l'a produit
}

// Extrait l'extension en badge coloré
// Renvoie l'extension du fichier en majuscules et la classe CSS associée,
// pour afficher une pastille de couleur différente selon le type (TXT, PDF,
// ou autre). Le « ! » après pop() indique à TypeScript que la valeur existe
// (on a déjà vérifié la présence d'un point dans le nom).
function badgeFichier(nom: string) {
  const ext = nom.includes(".") ? nom.split(".").pop()!.toUpperCase() : "DOC";
  const cls =
    ext === "TXT" ? "badge-fichier--txt" : ext === "PDF" ? "badge-fichier--pdf" : "badge-fichier--doc";
  return { ext, cls };
}

// ===========================================================================
// Composant racine
// ===========================================================================
export default function App() {
  // État global de l'application :
  //   - connecte : l'utilisateur est-il authentifié ? Initialisé à partir de
  //     la présence d'un jeton (getToken), pour rester connecté au rechargement.
  //   - profil   : les informations de l'utilisateur (nom, rôle…), chargées
  //     après connexion.
  //   - vue      : l'onglet actuellement affiché.
  const [connecte, setConnecte] = useState<boolean>(!!getToken());
  const [profil, setProfil] = useState<Profil | null>(null);
  const [vue, setVue] = useState<Vue>("chat");

  // L'état du chat vit ici (dans App) et non dans VueChat, afin de
  // survivre aux changements d'onglet. App ne se démonte jamais tant
  // que l'utilisateur reste connecté, donc les messages persistent.
  const chat = useChat();

  // À la connexion, on récupère le profil. Si la requête échoue (jeton expiré
  // ou invalide), on déconnecte proprement : le jeton périmé est effacé et
  // l'utilisateur est renvoyé vers l'écran de connexion.
  useEffect(() => {
    if (!connecte) return;
    monProfil()
      .then(setProfil)
      .catch(() => {
        deconnexion();
        setConnecte(false);
      });
  }, [connecte]);

  // Déconnexion : on efface le jeton, on réinitialise le profil et la vue,
  // et on vide le chat pour ne pas laisser de messages au prochain utilisateur.
  function seDeconnecter() {
    deconnexion();
    setProfil(null);
    setConnecte(false);
    setVue("chat");
    chat.reinitialiser();
  }

  // Tant que l'utilisateur n'est pas connecté, on n'affiche que l'écran de
  // connexion : aucune autre vue n'est accessible.
  if (!connecte) {
    return <EcranConnexion onConnecte={() => setConnecte(true)} />;
  }

  // Le rôle « admin » débloque la vue tableau de bord et certaines actions.
  const estAdmin = profil?.role === "admin";

  return (
    <div className="relative flex h-screen w-screen overflow-hidden">
      <div className="aurora-orb aurora-orb--violet" />
      <div className="aurora-orb aurora-orb--cyan" />

      {/* SIDEBAR */}
      <aside className="glass relative z-10 flex w-72 flex-col">
        <div className="app-header flex items-center gap-3 px-6 py-5">
          <span className="text-2xl">📚</span>
          <h1 className="text-lg font-semibold text-white">RAG Enterprise</h1>
        </div>

        <nav className="flex flex-col gap-1 p-3">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            // L'entrée « admin » n'apparaît que pour les administrateurs :
            // un utilisateur standard ne voit même pas le bouton. (La sécurité
            // réelle reste côté backend ; ceci n'est qu'un confort visuel.)
            if (item.vue === "admin" && !estAdmin) return null;
            return (
              <button
                key={item.vue}
                onClick={() => setVue(item.vue)}
                className={`nav-item ${vue === item.vue ? "actif" : ""}`}
              >
                <Icon size={18} />
                {item.label}
                {item.vue === "admin" && (
                  <span className="badge-scope ml-auto" style={{ color: "#c4b5fd" }}>
                    admin
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        <div className="mt-auto flex flex-col gap-3 border-t border-white/10 px-6 py-4">
          <button
            onClick={seDeconnecter}
            className="flex items-center gap-2 text-xs text-slate-500 transition-colors hover:text-white"
          >
            <LogOut size={14} />
            Se déconnecter
          </button>
          <p className="text-xs text-slate-600">Projet L3 · 100% local</p>
        </div>
      </aside>

      {/* MAIN */}
      <main className="relative z-10 flex flex-1 flex-col overflow-hidden">
        <div className="flex items-center justify-between px-8 pt-5">
          <BadgeEtat />
          <div className="flex items-center gap-3">
            {vue === "chat" && chat.messages.length > 0 && (
              <button
                onClick={() => {
                  if (confirm("Effacer la conversation en cours ?")) {
                    chat.reinitialiser();
                  }
                }}
                className="flex items-center gap-2 rounded-xl border border-white/10 px-3 py-2 text-xs text-slate-300 transition-colors hover:border-violet-500/40 hover:text-violet-200"
                title="Démarrer une nouvelle conversation"
              >
                <SquarePen size={15} />
                Nouveau chat
              </button>
            )}
            {profil && (
              <span className="pill-utilisateur">
                <User size={15} />
                {profil.nom_utilisateur}
                {estAdmin && <span className="text-violet-300"> (Admin)</span>}
              </span>
            )}
          </div>
        </div>

        {vue === "chat" && <VueChat chat={chat} />}
        {vue === "documents" && <VueDocuments estAdmin={estAdmin} />}
        {vue === "historique" && <VueHistorique onReposer={() => setVue("chat")} />}
        {vue === "admin" && <VueAdmin />}
      </main>
    </div>
  );
}

// ===========================================================================
// Badge d'état système (appelle /health)
// ===========================================================================
function BadgeEtat() {
  const [ok, setOk] = useState<boolean | null>(null);
  const [modele, setModele] = useState<string>("");

  useEffect(() => {
    // « actif » est un garde anti-fuite : si le composant est démonté avant
    // qu'une requête /health ne réponde, on ne tente pas de mettre à jour un
    // état qui n'existe plus (ce qui provoquerait un avertissement React).
    let actif = true;
    const verifier = () =>
      etatSante()
        .then((etat) => {
          if (!actif) return;
          setOk(true);
          setModele(etat.modele); // nom du modèle réel, renvoyé par le backend
        })
        .catch(() => actif && setOk(false));
    verifier(); // vérification immédiate au montage
    // Puis re-vérification périodique toutes les 30 s, pour refléter en quasi
    // temps réel une éventuelle indisponibilité du service.
    const minuteur = setInterval(verifier, 30000);
    // Nettoyage au démontage : on désarme le garde et on stoppe le minuteur.
    return () => {
      actif = false;
      clearInterval(minuteur);
    };
  }, []);

  // Met une majuscule à la première lettre du modèle (ex: "llama3.2" → "Llama3.2")
  const modeleAffiche = modele ? modele.charAt(0).toUpperCase() + modele.slice(1) : "";

  return (
    <span className="badge-etat">
      <span className={`point-etat ${ok === false ? "point-etat--ko" : ""}`} />
      {ok === false ? "Service indisponible" : "Système opérationnel"}
      {modeleAffiche && <span className="text-slate-500"> · {modeleAffiche} Local</span>}
    </span>
  );
}

// ===========================================================================
// Hook useChat : tout l'état + la logique du chat.
// Appelé dans App (et non dans VueChat) pour que les messages survivent
// aux changements d'onglet.
// ===========================================================================
interface ChatState {
  messages: Message[];
  input: string;
  setInput: (v: string) => void;
  enCours: boolean;
  envoyer: (questionDirecte?: string) => void;
  reinitialiser: () => void;
}

function useChat(): ChatState {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [enCours, setEnCours] = useState(false);
  // Garde anti-double-envoi. On utilise une ref (et non un état) car sa valeur
  // est lue/écrite de façon SYNCHRONE : un état React ne se met à jour qu'au
  // rendu suivant, ce qui laisserait passer deux envois quasi simultanés.
  const enVolRef = useRef(false); // empêche deux flux de streaming simultanés

  // Met à jour le dernier message (celui de l'assistant en cours de
  // génération). Centralise le motif « copier le tableau, remplacer le
  // dernier élément » utilisé pour chaque type d'événement SSE.
  // Note React : on ne modifie jamais le tableau en place ; on en crée une
  // copie, car React ne détecte les changements que sur une nouvelle référence.
  const majDernierMessage = useCallback((champs: Partial<Message>) => {
    setMessages((m) => {
      if (m.length === 0) return m;
      const copie = [...m];
      copie[copie.length - 1] = { ...copie[copie.length - 1], ...champs };
      return copie;
    });
  }, []);

  const envoyer = useCallback(
    async (questionDirecte?: string) => {
      // La question vient soit d'un appel direct (rejeu depuis l'historique),
      // soit du champ de saisie. On ignore l'envoi si la question est vide ou
      // si une génération est déjà en cours (double garde : état + ref).
      const question = (questionDirecte ?? input).trim();
      if (!question || enCours || enVolRef.current) return;
      enVolRef.current = true;

      if (!questionDirecte) setInput(""); // vide le champ si saisie manuelle
      setEnCours(true);
      // On ajoute d'emblée DEUX messages : la question de l'utilisateur, et un
      // message assistant vide qui se remplira au fil du streaming.
      setMessages((m) => [
        ...m,
        { role: "user", contenu: question },
        { role: "assistant", contenu: "", question },
      ]);

      try {
        // Boucle de streaming : on consomme les événements au fur et à mesure
        // qu'ils arrivent du backend (voir streamerChat dans api.ts).
        for await (const ev of streamerChat(question)) {
          if (ev.type === "token") {
            // Cas particulier : on concatène à l'existant, donc on lit
            // l'ancien contenu dans le updater plutôt que de le figer.
            // (Lire « dernier.contenu » à l'extérieur risquerait d'utiliser
            // une valeur périmée ; on le lit donc dans le setMessages.)
            setMessages((m) => {
              if (m.length === 0) return m;
              const copie = [...m];
              const dernier = copie[copie.length - 1];
              copie[copie.length - 1] = { ...dernier, contenu: dernier.contenu + ev.content };
              return copie;
            });
          } else if (ev.type === "no_context") {
            // Réponse de repli : aucune source pertinente trouvée.
            majDernierMessage({ contenu: ev.content, sansContexte: true });
          } else if (ev.type === "error") {
            majDernierMessage({ contenu: `⚠️ ${ev.content}` });
          }
        }
      } catch (e) {
        // Erreur réseau ou serveur : on l'affiche dans la bulle assistant.
        majDernierMessage({ contenu: `⚠️ ${(e as Error).message}` });
      } finally {
        // Quoi qu'il arrive (succès ou erreur), on relâche les gardes pour
        // autoriser un nouvel envoi.
        setEnCours(false);
        enVolRef.current = false;
      }
    },
    [input, enCours, majDernierMessage],
  );

  // Remet le chat à zéro (déconnexion, ou bouton « Nouveau chat »).
  const reinitialiser = useCallback(() => {
    setMessages([]);
    setInput("");
    setEnCours(false);
    enVolRef.current = false;
  }, []);

  return { messages, input, setInput, enCours, envoyer, reinitialiser };
}

// ===========================================================================
// Vue : Chat principal (piloté par l'état remonté dans App)
// ===========================================================================
function VueChat({ chat }: { chat: ChatState }) {
  const { messages, input, setInput, enCours, envoyer } = chat;
  const scrollRef = useRef<HTMLDivElement>(null);
  const fichierRef = useRef<HTMLInputElement>(null);
  const rejeuFaitRef = useRef(false); // le rejeu depuis l'historique n'a lieu qu'une fois
  const [infoUpload, setInfoUpload] = useState<string>("");

  // Auto-scroll : à chaque nouveau message (ou nouveau token), on fait défiler
  // la zone de discussion jusqu'en bas, pour suivre la réponse qui s'écrit.
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  // Au montage de la vue : rejoue une question demandée depuis l'historique.
  // Le mécanisme : la vue Historique dépose la question dans sessionStorage
  // puis bascule vers le chat ; ici, on la récupère, on l'efface (pour ne pas
  // la rejouer en boucle) et on l'envoie. Le rejeuFaitRef garantit que cela
  // n'arrive qu'une seule fois par montage. Le setTimeout(…, 0) diffère
  // l'envoi juste après le rendu initial.
  useEffect(() => {
    if (rejeuFaitRef.current) return;
    rejeuFaitRef.current = true;
    const q = sessionStorage.getItem("question_a_reposer");
    if (q) {
      sessionStorage.removeItem("question_a_reposer");
      setTimeout(() => envoyer(q), 0);
    }
    // envoyer est stable (useCallback) ; volontairement hors deps.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Entrée = envoyer ; Maj+Entrée = saut de ligne (comportement attendu d'un
  // champ de discussion multiligne).
  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      envoyer();
    }
  }

  // Téléversement rapide depuis le chat : le fichier part toujours en privé.
  // Le bloc finally réinitialise le champ fichier (pour pouvoir re-sélectionner
  // le même fichier) et efface le message d'info après quelques secondes.
  async function surFichierChoisi(e: React.ChangeEvent<HTMLInputElement>) {
    const fichier = e.target.files?.[0];
    if (!fichier) return;
    setInfoUpload(`Envoi de « ${fichier.name} »…`);
    try {
      const res = await televerserDocument(fichier, "private");
      setInfoUpload(res.message);
    } catch (err) {
      setInfoUpload(`⚠️ ${(err as Error).message}`);
    } finally {
      if (fichierRef.current) fichierRef.current.value = "";
      setTimeout(() => setInfoUpload(""), 6000);
    }
  }

  return (
    <>
      <div ref={scrollRef} className="scroll-zone flex-1 space-y-6 overflow-y-auto px-8 py-6">
        {messages.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <p className="text-slate-500">
              Posez une question sur vos documents pour commencer.
            </p>
          </div>
        )}
        {messages.map((msg, i) => (
          <BulleMessage
            key={i}
            message={msg}
            onReposer={(q) => envoyer(q)}
            peutReposer={!enCours}
          />
        ))}
      </div>

      <div className="px-8 py-5">
        {infoUpload && <p className="mb-2 text-xs text-cyan-300">{infoUpload}</p>}
        <div className="composer flex items-end gap-3 p-3">
          <input
            ref={fichierRef}
            type="file"
            accept=".pdf,.txt"
            className="hidden"
            onChange={surFichierChoisi}
          />
          <button
            onClick={() => fichierRef.current?.click()}
            title="Joindre un document (PDF ou TXT)"
            className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-slate-400 transition-colors hover:bg-white/5 hover:text-white"
          >
            <Paperclip size={20} />
          </button>
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Posez votre question..."
            rows={1}
            className="max-h-40 flex-1 resize-none bg-transparent py-2 text-sm text-slate-100 outline-none placeholder:text-slate-500"
          />
          <button
            onClick={() => envoyer()}
            disabled={!input.trim() || enCours}
            className="bouton-envoi flex h-10 w-10 shrink-0 items-center justify-center rounded-xl"
          >
            <SendHorizontal size={18} />
          </button>
        </div>
      </div>
    </>
  );
}

// --- Bulle de message + sources + boutons d'action ---
// Affiche un message. Les messages utilisateur sont alignés à droite avec un
// style simple ; les messages assistant sont à gauche, avec un avatar, et,
// si la réponse est terminée, un bouton « Reposer » qui relance la question.
function BulleMessage({
  message,
  onReposer,
  peutReposer,
}: {
  message: Message;
  onReposer: (q: string) => void;
  peutReposer: boolean;
}) {
  const estUser = message.role === "user";

  if (estUser) {
    return (
      <div className="flex justify-end">
        <div className="bulle-user-glow max-w-[75%] text-sm leading-relaxed text-slate-100">
          {message.contenu}
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col items-start gap-3">
      <div className="flex items-start gap-3">
        <div className="avatar-ia">
          <MessageSquare size={20} className="text-white" />
        </div>
        <div className="bulle-assistant-glow max-w-[70ch] text-sm leading-relaxed text-slate-100">
          {/* Tant que le contenu est vide (génération qui démarre), on affiche
              des points clignotants en guise d'indicateur de chargement. */}
          {message.contenu || (
            <span className="inline-block animate-pulse text-slate-500">…</span>
          )}
        </div>
      </div>

      {/* Le bouton « Reposer » n'apparaît que si la réponse est complète
          (contenu non vide), qu'aucune génération n'est en cours (peutReposer)
          et qu'on connaît la question d'origine. */}
      {message.contenu && peutReposer && message.question && (
        <div className="ml-[52px]">
          <button
            className="bouton-action"
            onClick={() => onReposer(message.question!)}
          >
            <RotateCcw size={15} />
            Reposer
          </button>
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// Vue : Mes documents
// ===========================================================================
function VueDocuments({ estAdmin }: { estAdmin: boolean }) {
  const [docs, setDocs] = useState<InfoDocument[]>([]);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState("");
  const [info, setInfo] = useState("");
  // Destination du prochain téléversement. Réservé aux admins : eux seuls
  // peuvent déposer dans l'espace partagé ; un utilisateur reste en « private ».
  const [scopeUpload, setScopeUpload] = useState<"private" | "shared">("private");
  const fichierRef = useRef<HTMLInputElement>(null);

  // Charge (ou recharge) la liste des documents. useCallback la rend stable
  // pour pouvoir l'utiliser comme dépendance du useEffect ci-dessous.
  const recharger = useCallback(async () => {
    setChargement(true);
    setErreur("");
    try {
      const data = await listerDocuments();
      setDocs(data.documents);
    } catch (e) {
      setErreur((e as Error).message);
    } finally {
      setChargement(false);
    }
  }, []);

  useEffect(() => {
    recharger();
  }, [recharger]);

  async function surFichierChoisi(e: React.ChangeEvent<HTMLInputElement>) {
    const fichier = e.target.files?.[0];
    if (!fichier) return;
    setInfo(`Envoi de « ${fichier.name} »…`);
    try {
      const res = await televerserDocument(fichier, scopeUpload);
      setInfo(res.message);
      setTimeout(recharger, 1500);
    } catch (err) {
      setInfo(`⚠️ ${(err as Error).message}`);
    } finally {
      if (fichierRef.current) fichierRef.current.value = "";
      setTimeout(() => setInfo(""), 6000);
    }
  }

  // Supprime un document après confirmation. En cas de succès, on retire le
  // document de la liste localement (mise à jour « optimiste ») plutôt que de
  // tout recharger depuis le serveur : l'interface réagit instantanément.
  async function supprimer(doc: InfoDocument) {
    if (!confirm(`Supprimer « ${doc.nom_fichier} » ?`)) return;
    try {
      await supprimerDocument(doc.scope, doc.nom_fichier);
      setDocs((d) => d.filter((x) => x !== doc));
    } catch (e) {
      setErreur((e as Error).message);
    }
  }

  return (
    <div className="scroll-zone flex-1 overflow-y-auto px-8 py-6">
      <EnteteVue titre="Mes documents" onRafraichir={recharger}>
        <div className="flex items-center gap-2">
          {estAdmin && (
            <select
              value={scopeUpload}
              onChange={(e) => setScopeUpload(e.target.value as "private" | "shared")}
              className="champ text-xs"
              title="Destination du prochain téléversement"
            >
              <option value="private">Destination : Privé</option>
              <option value="shared">Destination : Partagé</option>
            </select>
          )}
          <input
            ref={fichierRef}
            type="file"
            accept=".pdf,.txt"
            className="hidden"
            onChange={surFichierChoisi}
          />
          <button
            onClick={() => fichierRef.current?.click()}
            className="bouton-envoi flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium"
          >
            <Upload size={16} />
            Téléverser
          </button>
        </div>
      </EnteteVue>

      {info && <p className="mb-4 text-xs text-cyan-300">{info}</p>}
      {erreur && <p className="mb-4 text-xs text-red-400">{erreur}</p>}

      {chargement ? (
        <p className="text-sm text-slate-500">Chargement…</p>
      ) : docs.length === 0 ? (
        <p className="text-sm text-slate-500">Aucun document. Téléversez-en un pour commencer.</p>
      ) : (
        <div className="space-y-2">
          {docs.map((doc, i) => {
            const { ext, cls } = badgeFichier(doc.nom_fichier);
            return (
              <div key={i} className="glass flex items-center justify-between rounded-xl p-4">
                <div className="flex items-center gap-3">
                  <span className={`badge-fichier ${cls}`}>{ext}</span>
                  <div>
                    <p className="font-mono text-sm text-slate-200">{doc.nom_fichier}</p>
                    <p className="text-xs text-slate-500">{doc.taille_ko.toFixed(0)} Ko</p>
                  </div>
                  <span
                    className={`badge-scope ${
                      doc.scope === "shared" ? "badge-scope--shared" : "badge-scope--private"
                    }`}
                  >
                    {doc.scope === "shared" ? "Partagé" : "Privé"}
                  </span>
                </div>
                {/* On ne peut supprimer un document partagé que si l'on est
                    admin ; un document privé est toujours supprimable par son
                    propriétaire. */}
                {(doc.scope === "private" || estAdmin) && (
                  <button
                    onClick={() => supprimer(doc)}
                    title="Supprimer"
                    className="flex h-9 w-9 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-red-500/10 hover:text-red-400"
                  >
                    <Trash2 size={16} />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// Vue : Mon historique
// ===========================================================================
function VueHistorique({ onReposer }: { onReposer: () => void }) {
  const [convs, setConvs] = useState<Conversation[]>([]);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState("");
  const [recherche, setRecherche] = useState("");

  const recharger = useCallback(async (q?: string) => {
    setChargement(true);
    setErreur("");
    try {
      const data = await consulterHistorique(50, 0, q);
      setConvs(data.conversations);
    } catch (e) {
      setErreur((e as Error).message);
    } finally {
      setChargement(false);
    }
  }, []);

  useEffect(() => {
    recharger();
  }, [recharger]);

  async function supprimer(id: number) {
    try {
      await supprimerConversation(id);
      setConvs((c) => c.filter((x) => x.id !== id));
    } catch (e) {
      setErreur((e as Error).message);
    }
  }

  async function viderTout() {
    if (!confirm("Vider tout votre historique ?")) return;
    try {
      await viderHistorique();
      setConvs([]);
    } catch (e) {
      setErreur((e as Error).message);
    }
  }

  // « Reposer » une question depuis l'historique : on la stocke dans
  // sessionStorage, puis on bascule vers le chat. C'est VueChat qui, à son
  // montage, lira cette valeur et enverra la question (voir son useEffect).
  // Ce passage par sessionStorage permet de transmettre la question entre deux
  // vues sans la faire remonter par des props jusqu'à App.
  function reposer(question: string) {
    sessionStorage.setItem("question_a_reposer", question);
    onReposer();
  }

  return (
    <div className="scroll-zone flex-1 overflow-y-auto px-8 py-6">
      <EnteteVue titre="Mon historique" onRafraichir={() => recharger(recherche)}>
        <button
          onClick={viderTout}
          className="flex items-center gap-2 rounded-xl border border-white/10 px-4 py-2 text-sm text-slate-300 transition-colors hover:border-red-500/40 hover:text-red-400"
        >
          <Trash2 size={16} />
          Tout vider
        </button>
      </EnteteVue>

      <div className="composer mb-4 flex items-center gap-2 p-2">
        <input
          value={recherche}
          onChange={(e) => setRecherche(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && recharger(recherche)}
          placeholder="Rechercher dans vos questions…"
          className="flex-1 bg-transparent px-2 py-1 text-sm text-slate-100 outline-none placeholder:text-slate-500"
        />
        <button
          onClick={() => recharger(recherche)}
          className="bouton-envoi rounded-lg px-3 py-1.5 text-xs"
        >
          Chercher
        </button>
      </div>

      {erreur && <p className="mb-4 text-xs text-red-400">{erreur}</p>}

      {chargement ? (
        <p className="text-sm text-slate-500">Chargement…</p>
      ) : convs.length === 0 ? (
        <p className="text-sm text-slate-500">Aucune conversation enregistrée.</p>
      ) : (
        <div className="space-y-3">
          {convs.map((c) => (
            <div key={c.id} className="glass rounded-xl p-4">
              <div className="mb-2 flex items-start justify-between gap-3">
                <p className="text-sm font-medium text-slate-100">{c.question}</p>
                <div className="flex shrink-0 items-center gap-1">
                  <button
                    onClick={() => reposer(c.question)}
                    title="Reposer cette question"
                    className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-violet-500/10 hover:text-violet-300"
                  >
                    <RotateCcw size={14} />
                  </button>
                  <button
                    onClick={() => supprimer(c.id)}
                    title="Supprimer"
                    className="flex h-7 w-7 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-red-500/10 hover:text-red-400"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
              <p className="mb-2 text-sm text-slate-400">{c.reponse}</p>
              <div className="flex flex-wrap items-center gap-2">
                {c.sources.map((s, i) => (
                  <span key={i} className="badge-scope">
                    {s}
                  </span>
                ))}
                <span className="ml-auto text-xs text-slate-600">
                  {new Date(c.cree_le).toLocaleString("fr-FR")}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// En-tête commun aux vues
// ===========================================================================
// Petit composant réutilisable : un titre, un bouton « rafraîchir » optionnel,
// et un emplacement « children » pour les actions propres à chaque vue
// (téléverser, vider…). Factoriser cet en-tête évite de le réécrire dans
// chaque vue.
function EnteteVue({
  titre,
  onRafraichir,
  children,
}: {
  titre: string;
  onRafraichir?: () => void;
  children?: React.ReactNode;
}) {
  return (
    <div className="mb-6 flex items-center justify-between">
      <div className="flex items-center gap-3">
        <h2 className="text-xl font-semibold text-white">{titre}</h2>
        {onRafraichir && (
          <button
            onClick={onRafraichir}
            title="Rafraîchir"
            className="flex h-8 w-8 items-center justify-center rounded-lg text-slate-500 transition-colors hover:bg-white/5 hover:text-white"
          >
            <RefreshCw size={15} />
          </button>
        )}
      </div>
      {children}
    </div>
  );
}

// ===========================================================================
// Écran de connexion
// ===========================================================================
function EcranConnexion({ onConnecte }: { onConnecte: () => void }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [erreur, setErreur] = useState("");

  // Tente la connexion. En cas de succès, on prévient le parent (onConnecte)
  // qui bascule l'application vers les vues authentifiées. En cas d'échec, on
  // affiche le message d'erreur renvoyé par login().
  async function soumettre() {
    try {
      setErreur("");
      await login(u, p);
      onConnecte();
    } catch (e) {
      setErreur((e as Error).message);
    }
  }

  return (
    <div className="relative flex h-screen w-screen items-center justify-center">
      <div className="aurora-orb aurora-orb--violet" />
      <div className="aurora-orb aurora-orb--cyan" />
      <div className="modale relative z-10 w-80 p-6">
        <div className="mb-5 flex items-center gap-3">
          <span className="text-2xl">📚</span>
          <h1 className="text-lg font-semibold text-white">RAG Enterprise</h1>
        </div>
        <input
          value={u}
          onChange={(e) => setU(e.target.value)}
          placeholder="Nom d'utilisateur"
          className="champ mb-3 w-full"
        />
        <input
          type="password"
          value={p}
          onChange={(e) => setP(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && soumettre()}
          placeholder="Mot de passe"
          className="champ mb-4 w-full"
        />
        {erreur && <p className="mb-3 text-xs text-red-400">{erreur}</p>}
        <button
          onClick={soumettre}
          className="bouton-envoi w-full rounded-xl py-2 text-sm font-medium"
        >
          Se connecter
        </button>
      </div>
    </div>
  );
}