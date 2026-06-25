// ##############################################################
// # Nom ......... : main.tsx
// # Rôle ........ : Point d'entrée du frontend React de RAG
// #                 Enterprise. Monte le composant racine <App>
// #                 dans le DOM et charge les styles globaux.
// # Auteur ...... : Maxim Khomenko
// # Version ..... : V1.0.0 du 23/06/2026
// # Licence ..... : Réalisé dans le cadre d'un projet de fin de
// #                 licence en Informatique (L3)
// # Usage ....... : Référencé par index.html (script type=module).
// # Dépendances . : react, react-dom, ./App, ./index.css.
// ##############################################################

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)