// ##############################################################
// # Nom ......... : main.tsx
// # Rôle ........ : Point d'entrée du frontend React de RAG
// #                 Enterprise. Monte le composant racine <App>
// #                 dans le DOM et charge les styles globaux.
// # Auteur ...... : Maxim Khomenko
// # Version ..... : V1.0.0 du 26/06/2026
// # Licence ..... : Réalisé dans le cadre d'un projet de fin de
// #                 licence en Informatique (L3)
// # Usage ....... : Référencé par index.html (script type=module).
// # Dépendances . : react, react-dom, ./App, ./index.css.
// ##############################################################

// StrictMode : outil de développement de React. Il n'a aucun effet en
// production, mais en développement il aide à détecter des problèmes (effets
// de bord mal nettoyés, API dépréciées…) en exécutant volontairement certains
// traitements deux fois. C'est purement un garde-fou pour le développeur.
import { StrictMode } from 'react'
// createRoot : la fonction de React 18 qui « accroche » l'application React à
// un élément HTML existant de la page et active le rendu moderne (concurrent).
import { createRoot } from 'react-dom/client'
// Import des styles globaux (Tailwind et styles personnalisés). Importer le CSS
// ici suffit : l'outil de build (Vite) l'inclut automatiquement dans la page.
import './index.css'
// Le composant racine de toute l'application (défini dans App.tsx).
import App from './App.tsx'

// Démarrage de l'application en trois temps :
//   1. document.getElementById('root') récupère la <div id="root"> présente
//      dans index.html ; c'est le conteneur où toute l'interface sera injectée.
//      Le « ! » indique à TypeScript que cet élément existe forcément (il est
//      garanti par index.html), ce qui évite une vérification de nullité.
//   2. createRoot(...) crée la racine React attachée à ce conteneur.
//   3. .render(...) y affiche le composant <App>, enveloppé dans <StrictMode>.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)