---
name: rendu-3d-maison
description: Modéliser une maison en 2D/3D dans Atelier Maison (house-planner/index.html) à partir d'une annonce, de photos ou d'un plan, et soigner le rendu 3D (éclairage réaliste, visite à la première personne, visite guidée animée). Utiliser quand l'utilisateur envoie des photos, un plan ou une annonce de maison, demande de refaire des cotes ou des murs, ou veut un rendu 3D plus réaliste ou des animations de caméra.
---

# Rendu 3D et modélisation de maison : Atelier Maison

L'application tient dans un seul fichier : `house-planner/index.html`, publié comme Artifact.
Elle utilise three.js r128 en UMD (cdnjs), avec OrbitControls et RoomEnvironment chargés depuis jsdelivr `three@0.128.0/examples/js/...`.

## Modèle de données (`S`)

- `levels: [{name}]` : l'index sert de niveau. L'altitude d'un niveau vaut `level * LEVEL_H`, avec `LEVEL_H` = 2,8 m et `WALL_H` = 2,5 m.
- `rooms: [{id, level, type, name, x, y, w, d, floor, wall, skylight, works}]` : rectangles en mètres, cotes intérieures.
  - `x` va vers la droite, `y` vers le bas du plan, et la façade sur rue est en y = 0.
  - Deux pièces voisines partagent exactement la même ligne de mur.
  - `type` est une clé de `ROOM_TYPES`, `floor` une clé de `FLOORS`.
- `openings: [{id, level, kind, x1, y1, x2, y2, h, sill, flip}]` : chaque ouverture est un segment posé sur une ligne de mur.
  - Horizontale quand y1 = y2, verticale quand x1 = x2.
  - `kind` vaut door, entry, window, french ou open.
  - Les murs 3D sont découpés automatiquement : allège sous `sill`, linteau au-dessus de `sill + h`. Une ouverture coupe les murs des deux pièces qui la bordent.
- `items: [{id, level, kind, x, y, rot, w, d, color}]` : `x, y` est le centre de l'objet.
  - `rot` donne le côté du dos : 0 = mur du haut, 90 = droite, 180 = bas, 270 = gauche.
  - `w` est la longueur le long du mur, `d` la profondeur.

## Modéliser à partir de photos (sans plan)

1. Repérer l'enchaînement des pièces depuis la rue : façade, porte d'entrée, fenêtres, escalier, extensions, cour.
2. Déduire les cotes à partir d'objets de taille connue :
   - porte 0,83 × 2,04 m ;
   - marche : 0,18 m de haut, 0,25 m de giron ;
   - plan de travail : 0,60 m de profondeur, 0,90 m de haut ;
   - lit double : 1,40 à 1,60 m de large ;
   - radiateur, carrelage 60 × 60.
3. Caler la somme des surfaces habitables sur la surface de l'annonce. Les combles sous 1,80 m ne comptent pas.
4. Placer les ouvertures sur les lignes de mur exactes et le mobilier existant visible (cuisine, sanitaires, placards) avec sa couleur réelle.
5. Marquer le projet `estimated: true` et lister à l'utilisateur les cotes à mesurer en priorité.
6. Le relevé de référence de la maison de ville 76 m² se trouve dans `maisonDeVille()`. Le mettre à jour quand l'utilisateur donne des mesures.

## Import automatique dans la page (onglet « Importer »)

- La page appelle Claude via la capacité `sample` avec images. Il faut la déclarer au publish : `capabilities: {sample: {images: true}}`.
- `importPrompt()` décrit le schéma JSON. `sanitizeProject()` valide toutes les valeurs : types, sols, couleurs hexadécimales, ouvertures remises à l'horizontale ou à la verticale, arrondi à 5 cm.
- Toute nouvelle valeur ajoutée à `ROOM_TYPES`, `FLOORS` ou `CATALOG` apparaît d'elle-même dans le prompt.

## Rendu réaliste (three.js r128)

- `renderer.outputEncoding = sRGBEncoding` et `toneMapping = ACESFilmicToneMapping`.
  - Toute couleur de matériau passe par `mat()`, qui fait `convertSRGBToLinear()`.
  - Les textures de sol sont en `sRGBEncoding`.
- L'environnement vient de `PMREMGenerator.fromScene(new THREE.RoomEnvironment())` et donne des reflets doux (`envMapIntensity` d'environ 0,6).
- Le soleil (DirectionalLight) projette des ombres PCFSoft. Chaque pièce du niveau affiché reçoit une PointLight au plafond, chaude en ambiance « Soirée ».
- En mode Visite, la page ajoute plafonds, spots encastrés émissifs et verrières (`room.skylight`). Les plinthes sont toujours dessinées.
- Limiter le nombre de lumières à une par pièce du niveau courant, sans ombre, pour garder un rendu fluide.

## Mouvements de caméra (motion design)

- `tween(pos, target, dur, yaw, pitch)` : interpolation avec ease-in-out cubique. Toutes les transitions passent par elle : `fit3D(true)`, `focusRoom`, `walkTo`, `walkIntoRoom`.
- Visite (`setCam('walk')`) :
  - œil à 1,60 m, champ de vision de 72° ;
  - glisser pour regarder, clic au sol pour avancer (raycast sur `userData.floor`) ;
  - flèches, ZQSD ou WASD, et pavé tactile sur mobile ;
  - collisions avec les meshes `userData.wall`.
- Visite guidée (`runTour`) : vue maquette, puis pour chaque niveau et chaque pièce, entrée par le seuil de porte (`viewPoint`) suivie d'un panoramique lent. Toute interaction l'arrête (`stopTour`).

## Vérifier avant de publier

Lancer Playwright avec les bibliothèques three servies en local, parce que le CDN est inaccessible depuis le conteneur :

```
npm pack three@0.128.0
page.route(/three.min.js/, …)
```

Faire une capture en vue Maquette, une en Visite et une en Soirée, puis publier avec la même `file_path` pour garder l'URL.
