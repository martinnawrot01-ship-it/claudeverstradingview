# Atelier d'images (image-ai)

Génère ou retouche des images à partir d'une description et de photos.

## Fonctionnement

1. **Analyse (Claude)** : Claude regarde tes images et lit ta demande (en tenant compte de la conversation). Il repère précisément l'élément visé, par exemple « le mur blanc à droite », puis rédige une consigne détaillée en anglais. Il précise aussi ce qui ne doit pas bouger : cadrage, lumière, meubles…
2. **Questions** : si un point ambigu changerait vraiment le résultat, Claude pose jusqu'à 3 questions. Sinon, il fait le choix le plus cohérent et te l'indique.
3. **Génération (Gemini)** : le modèle d'image reçoit tes photos et la consigne.
4. **Vérification (Claude)** : Claude compare le résultat à ta demande. Si ça ne correspond pas, il corrige la consigne et relance une fois.
5. **Itération** : le résultat devient l'image de départ. Tu peux enchaîner : « plus clair », « enlève la plante »…

## Installation

Il faut Node.js 20 ou plus récent.

```bash
cd image-ai
npm install
cp .env.example .env    # puis colle tes deux clés dans .env
npm start               # ouvre http://localhost:3000
```

Clés nécessaires (toutes deux payantes à l'usage) :
- `ANTHROPIC_API_KEY` : https://console.anthropic.com
- `GEMINI_API_KEY` : https://aistudio.google.com/apikey

Les options (modèles, taille d'image, nombre de tentatives) sont dans `.env.example`.
