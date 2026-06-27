// ##############################################################
// # Nom ......... : VueAdmin.tsx
// # Rôle ........ : Tableau de bord administrateur du frontend
// #                 React de RAG Enterprise. Affiche les
// #                 statistiques système, la répartition des
// #                 utilisateurs et l'activité récente sous forme
// #                 de cartes et de graphiques (recharts), avec
// #                 rafraîchissement automatique toutes les 30 s.
// # Auteur ...... : Maxim Khomenko
// # Version ..... : V1.1.0 du 26/06/2026
// # Licence ..... : Réalisé dans le cadre d'un projet de fin de
// #                 licence en Informatique (L3)
// # Usage ....... : Monté par App.tsx pour la vue "admin".
// # Dépendances . : react, recharts, lucide-react, ./api.
// ##############################################################

import { useEffect, useState, useCallback } from "react";
import {
  BarChart,
  Bar,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { RefreshCw, Users, MessageSquare, FileText, Database } from "lucide-react";
import {
  statsSysteme,
  statsUtilisateurs,
  activiteRecente,
  type StatsSysteme,
  type StatsUtilisateur,
  type ActiviteRecente,
} from "./api";

// Couleurs de la charte graphique, définies une fois et réutilisées dans tous
// les graphiques et badges. Les centraliser évite de répéter les codes
// hexadécimaux partout et garantit une cohérence visuelle.
const VIOLET = "#8b5cf6";
const CYAN = "#06b6d4";

// ===========================================================================
// Vue Admin
// ===========================================================================
export default function VueAdmin() {
  // Les trois jeux de données du tableau de bord, plus les états d'interface :
  //   - stats     : statistiques globales (cartes chiffrées) ;
  //   - users     : liste des utilisateurs avec leurs compteurs ;
  //   - activite  : dernières questions posées ;
  //   - erreur    : message d'erreur éventuel ;
  //   - chargement: vrai pendant le chargement initial.
  const [stats, setStats] = useState<StatsSysteme | null>(null);
  const [users, setUsers] = useState<StatsUtilisateur[]>([]);
  const [activite, setActivite] = useState<ActiviteRecente[]>([]);
  const [erreur, setErreur] = useState("");
  const [chargement, setChargement] = useState(true);

  // Charge les trois sources de données EN PARALLÈLE grâce à Promise.all :
  // les trois requêtes partent en même temps et on attend qu'elles soient
  // toutes terminées. C'est nettement plus rapide que de les enchaîner une
  // par une. Si l'une échoue, le catch affiche l'erreur ; le finally coupe
  // l'indicateur de chargement dans tous les cas.
  const recharger = useCallback(async () => {
    setChargement(true);
    setErreur("");
    try {
      const [s, u, a] = await Promise.all([
        statsSysteme(),
        statsUtilisateurs(),
        activiteRecente(),
      ]);
      setStats(s);
      setUsers(u);
      setActivite(a);
    } catch (e) {
      setErreur((e as Error).message);
    } finally {
      setChargement(false);
    }
  }, []);

  // Chargement initial + rafraîchissement automatique toutes les 30 s
  // Le tableau de bord se met ainsi à jour seul, sans action de l'admin.
  // Le retour de useEffect arrête le minuteur au démontage (évite les fuites).
  useEffect(() => {
    recharger();
    const minuteur = setInterval(recharger, 30000);
    return () => clearInterval(minuteur);
  }, [recharger]);

  // Données dérivées pour les graphiques
  // On transforme la liste d'utilisateurs en un format simple attendu par le
  // graphique en barres : un objet { nom, conversations } par utilisateur.
  const donneesConv = users.map((u) => ({
    nom: u.nom_utilisateur,
    conversations: u.nb_conversations,
  }));

  // Répartition des rôles : on s'appuie sur les compteurs autoritatifs du
  // backend (/admin/stats) plutôt que de recompter la liste /admin/users,
  // qui pourrait être partielle. Repli sur le calcul local tant que stats
  // n'est pas encore chargé.
  const nbAdmins = stats ? stats.nb_admins : users.filter((u) => u.role === "admin").length;
  const nbUsers = stats
    ? stats.nb_utilisateurs - stats.nb_admins
    : users.filter((u) => u.role === "user").length;
  const donneesRoles = [
    { name: "Admins", value: nbAdmins },
    { name: "Employés", value: nbUsers },
  ];

  // Comptage des sources les plus citées (à partir de l'activité récente)
  // On parcourt chaque activité et chacune de ses sources pour construire un
  // dictionnaire { nom_de_source : nombre_de_citations }.
  const compteSources: Record<string, number> = {};
  activite.forEach((a) =>
    a.sources.forEach((s) => {
      compteSources[s] = (compteSources[s] || 0) + 1;
    }),
  );
  // Puis on transforme ce dictionnaire en données de graphique :
  //   - sort   : trie par nombre de citations décroissant ;
  //   - slice  : ne garde que les 5 sources les plus citées ;
  //   - map    : tronque les noms trop longs (> 20 caractères) pour l'affichage.
  const donneesSources = Object.entries(compteSources)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)
    .map(([nom, n]) => ({
      nom: nom.length > 20 ? nom.slice(0, 17) + "…" : nom,
      utilisations: n,
    }));

  return (
    <div className="scroll-zone flex-1 overflow-y-auto px-8 py-6">
      <div className="mb-6 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-semibold text-white">🛡️ Tableau de bord</h2>
          <span className="badge-scope" style={{ color: "#34d399" }}>
            ● En direct
          </span>
        </div>
        <button
          onClick={recharger}
          className="bouton-envoi flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-medium"
        >
          <RefreshCw size={16} />
          Rafraîchir
        </button>
      </div>

      {erreur && <p className="mb-4 text-xs text-red-400">{erreur}</p>}

      {/* On n'affiche l'indicateur « Chargement… » que lors du TOUT premier
          chargement (chargement && !stats). Lors des rafraîchissements
          automatiques suivants, on garde les données déjà affichées pour
          éviter un clignotement de l'interface toutes les 30 s. */}
      {chargement && !stats ? (
        <p className="text-sm text-slate-500">Chargement…</p>
      ) : (
        <>
          {/* Cartes de stats */}
          {stats && (
            <div className="mb-7 grid grid-cols-2 gap-4 md:grid-cols-4">
              <CarteStat
                icon={Users}
                valeur={stats.nb_utilisateurs}
                label="Utilisateurs"
                sous={`${stats.nb_utilisateurs_actifs} actifs · ${stats.nb_admins} admins`}
              />
              <CarteStat
                icon={MessageSquare}
                valeur={stats.nb_conversations}
                label="Conversations"
                sous={`${stats.conversations_aujourdhui} auj. · ${stats.conversations_semaine} semaine`}
              />
              <CarteStat
                icon={FileText}
                valeur={stats.nb_documents}
                label="Documents"
                sous="partagés + privés"
              />
              <CarteStat
                icon={Database}
                valeur={stats.nb_morceaux}
                label="Morceaux ChromaDB"
                sous="vecteurs indexés"
              />
            </div>
          )}

          {/* Graphiques */}
          <div className="mb-7 grid grid-cols-1 gap-5 lg:grid-cols-3">
            <div className="glass rounded-2xl p-5">
              <h3 className="mb-4 text-sm font-semibold text-slate-100">
                📊 Conversations / utilisateur
              </h3>
              {/* Graphique en barres verticales : une barre par utilisateur,
                  hauteur = nombre de conversations. Affiché seulement s'il y a
                  des données (sinon, message « Aucune donnée »). */}
              <div className="h-52">
                {donneesConv.length === 0 ? (
                  <PasDeDonnees />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={donneesConv}>
                      <XAxis dataKey="nom" tick={{ fill: "#94a3b8", fontSize: 11 }} />
                      <YAxis allowDecimals={false} tick={{ fill: "#94a3b8", fontSize: 11 }} />
                      <Tooltip
                        contentStyle={{
                          background: "rgba(24,24,27,0.95)",
                          border: "1px solid rgba(255,255,255,0.1)",
                          borderRadius: 12,
                          color: "#e2e8f0",
                        }}
                        cursor={{ fill: "rgba(255,255,255,0.04)" }}
                      />
                      <Bar dataKey="conversations" fill={VIOLET} radius={[6, 6, 0, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>

            <div className="glass rounded-2xl p-5">
              <h3 className="mb-4 text-sm font-semibold text-slate-100">
                👥 Répartition des utilisateurs
              </h3>
              {/* Camembert (PieChart) admins / employés. Deux parts seulement,
                  d'où les deux <Cell> aux couleurs de la charte. */}
              <div className="h-52">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={donneesRoles}
                      dataKey="value"
                      nameKey="name"
                      innerRadius={55}
                      outerRadius={80}
                      paddingAngle={3}
                    >
                      <Cell fill={VIOLET} />
                      <Cell fill={CYAN} />
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        background: "rgba(24,24,27,0.95)",
                        border: "1px solid rgba(255,255,255,0.1)",
                        borderRadius: 12,
                        color: "#e2e8f0",
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <div className="mt-2 flex justify-center gap-4 text-xs">
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: VIOLET }} />
                  Admins ({nbAdmins})
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: CYAN }} />
                  Employés ({nbUsers})
                </span>
              </div>
            </div>

            <div className="glass rounded-2xl p-5">
              <h3 className="mb-4 text-sm font-semibold text-slate-100">
                📂 Sources les plus citées (activité récente)
              </h3>
              {/* Graphique en barres HORIZONTALES (layout="vertical" chez
                  recharts), bien adapté à l'affichage de noms de fichiers. Si
                  aucune source n'a encore été citée, on affiche un message. */}
              <div className="h-52">
                {donneesSources.length === 0 ? (
                  <PasDeDonnees texte="Aucune source citée pour le moment" />
                ) : (
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={donneesSources} layout="vertical">
                      <XAxis type="number" allowDecimals={false} tick={{ fill: "#94a3b8", fontSize: 11 }} />
                      <YAxis
                        type="category"
                        dataKey="nom"
                        width={90}
                        tick={{ fill: "#94a3b8", fontSize: 10 }}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "rgba(24,24,27,0.95)",
                          border: "1px solid rgba(255,255,255,0.1)",
                          borderRadius: 12,
                          color: "#e2e8f0",
                        }}
                        cursor={{ fill: "rgba(255,255,255,0.04)" }}
                      />
                      <Bar dataKey="utilisations" fill={CYAN} radius={[0, 6, 6, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                )}
              </div>
            </div>
          </div>

          {/* Tableaux */}
          <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
            {/* Utilisateurs */}
            <div className="glass rounded-2xl p-5">
              <h3 className="mb-4 text-sm font-semibold text-slate-100">👥 Utilisateurs</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-white/10 text-left text-xs uppercase tracking-wide text-slate-400">
                      <th className="px-3 py-2">Nom</th>
                      <th className="px-3 py-2">Rôle</th>
                      <th className="px-3 py-2">Statut</th>
                      <th className="px-3 py-2">Conv.</th>
                      <th className="px-3 py-2">Créé</th>
                    </tr>
                  </thead>
                  <tbody>
                    {users.map((u) => (
                      <tr key={u.id} className="border-b border-white/5 hover:bg-white/[0.03]">
                        <td className="px-3 py-2 font-medium text-slate-200">{u.nom_utilisateur}</td>
                        <td className="px-3 py-2">
                          <span
                            className="badge-scope"
                            style={{ color: u.role === "admin" ? "#c4b5fd" : "#67e8f9" }}
                          >
                            {u.role}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          <span
                            className="badge-scope"
                            style={{ color: u.actif ? "#6ee7b7" : "#fca5a5" }}
                          >
                            {u.actif ? "✓ actif" : "✗ inactif"}
                          </span>
                        </td>
                        <td className="px-3 py-2 font-semibold text-violet-300">
                          {u.nb_conversations}
                        </td>
                        <td className="px-3 py-2 text-xs text-slate-500">
                          {u.cree_le.slice(0, 10)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Activité récente */}
            <div className="glass rounded-2xl p-5">
              <h3 className="mb-4 text-sm font-semibold text-slate-100">🕐 Activité récente</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-white/10 text-left text-xs uppercase tracking-wide text-slate-400">
                      <th className="px-3 py-2">Utilisateur</th>
                      <th className="px-3 py-2">Question</th>
                      <th className="px-3 py-2">Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {activite.map((a) => (
                      <tr key={a.id} className="border-b border-white/5 hover:bg-white/[0.03]">
                        <td className="px-3 py-2 font-medium text-slate-200">
                          {a.nom_utilisateur}
                        </td>
                        <td
                          className="max-w-[200px] truncate px-3 py-2 text-slate-300"
                          title={a.question}
                        >
                          {a.question}
                        </td>
                        <td className="px-3 py-2 text-xs text-slate-500">
                          {a.cree_le.slice(0, 16).replace("T", " ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// --- Carte de statistique ---
// Petite carte réutilisable affichant une statistique : une icône, une grande
// valeur chiffrée et deux libellés. Utilisée pour les quatre cartes du haut.
// La syntaxe « icon: Icon » renomme la prop « icon » en « Icon » localement,
// car un composant React doit commencer par une majuscule pour être rendu.
function CarteStat({
  icon: Icon,
  valeur,
  label,
  sous,
}: {
  icon: typeof Users;
  valeur: number;
  label: string;
  sous: string;
}) {
  return (
    <div className="glass relative overflow-hidden rounded-2xl p-5 text-center">
      <div
        className="absolute left-0 right-0 top-0 h-[3px]"
        style={{ background: `linear-gradient(90deg, ${VIOLET}, ${CYAN})` }}
      />
      <Icon size={20} className="mx-auto mb-2 text-slate-400" />
      <div
        className="text-4xl font-extrabold leading-none"
        style={{
          // Astuce CSS pour colorer le TEXTE avec un dégradé : on applique le
          // dégradé en fond, on le « clippe » sur la forme du texte, puis on
          // rend le texte lui-même transparent pour laisser voir le dégradé.
          background: `linear-gradient(135deg, ${VIOLET}, ${CYAN})`,
          WebkitBackgroundClip: "text",
          WebkitTextFillColor: "transparent",
        }}
      >
        {valeur}
      </div>
      <div className="mt-1.5 text-xs font-semibold uppercase tracking-wide text-slate-400">
        {label}
      </div>
      <div className="mt-1 text-xs text-slate-500">{sous}</div>
    </div>
  );
}

// Petit composant d'état vide, affiché à la place d'un graphique quand il n'y
// a aucune donnée à montrer. Le texte est personnalisable via une prop.
function PasDeDonnees({ texte = "Aucune donnée" }: { texte?: string }) {
  return (
    <div className="flex h-full items-center justify-center text-sm text-slate-600">
      {texte}
    </div>
  );
}