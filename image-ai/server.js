import "dotenv/config";
import express from "express";
import multer from "multer";
import Anthropic from "@anthropic-ai/sdk";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import { GoogleGenAI } from "@google/genai";
import { z } from "zod";

const PORT = Number(process.env.PORT || 3000);
const CLAUDE_MODEL = process.env.CLAUDE_MODEL || "claude-opus-5-5";
const IMAGE_MODEL = process.env.GEMINI_IMAGE_MODEL || "gemini-3-pro-image";
const IMAGE_SIZE = process.env.IMAGE_SIZE || "2K";
const MAX_ATTEMPTS = Number(process.env.MAX_ATTEMPTS || 2);

const claude = new Anthropic();
const gemini = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

const app = express();
const upload = multer({ storage: multer.memoryStorage(), limits: { fileSize: 15 * 1024 * 1024, files: 8 } });
app.use(express.static("public"));

const ASPECTS = ["1:1", "2:3", "3:2", "3:4", "4:3", "9:16", "16:9", "21:9"];
const SUPPORTED = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);

// ---------- Schemas ----------
const PlanSchema = z.object({
  understanding: z.string().describe("Ce que l'utilisateur veut, reformulé précisément, en français"),
  assumptions: z.array(z.string()).describe("Hypothèses faites pour combler les détails non précisés, en français"),
  questions: z.array(z.string()).describe("Questions bloquantes à poser AVANT de générer, en français. Vide si la demande est assez claire."),
  base_image_index: z.number().int().describe("Index (0-based) de l'image à modifier, ou -1 pour créer une image nouvelle"),
  image_prompt: z.string().describe("Consigne détaillée en anglais pour le générateur d'images"),
  aspect_ratio: z.enum(ASPECTS),
});

const ReviewSchema = z.object({
  matches_request: z.boolean(),
  issues: z.array(z.string()).describe("Écarts entre l'image et la demande, en français"),
  corrected_prompt: z.string().describe("Consigne anglaise corrigée pour une nouvelle tentative (vide si matches_request)"),
});

const PLAN_SYSTEM = `Tu es le "cerveau" d'un outil de génération et de retouche d'images. L'utilisateur (francophone) décrit ce qu'il veut et joint parfois des images. Ton travail : comprendre EXACTEMENT sa demande et la traduire en une consigne parfaite pour un modèle de génération d'images (Gemini), qui reçoit les mêmes images que toi.

Méthode :
1. Examine chaque image : contenu, pièce/objets, perspective, point de vue, lumière, style, couleurs, matériaux. Repère les éléments que la demande désigne ("le mur blanc à droite", "le canapé", etc.) et localise-les précisément (position dans l'image, ce qui les entoure).
2. Détermine le rôle de chaque image : image de base à modifier, référence de style, objet à insérer, etc. Si l'utilisateur dit "modifie", "ajoute", "remplace", "sur cette photo", c'est une retouche : choisis l'image de base.
3. Tiens compte de l'historique de la conversation : une demande courte ("plus clair", "enlève la plante", "en bleu") s'applique au dernier résultat.
4. Rédige image_prompt en anglais :
   - commence par l'action principale, de façon explicite et localisée ("In the first image, along the white wall on the right side of the room, add ...");
   - décris précisément le résultat voulu : objets, matériaux, couleurs, dimensions réalistes, position, cohérence avec la perspective, la lumière et le style existants;
   - pour une retouche, liste ce qui doit rester STRICTEMENT identique (cadrage, angle de vue, meubles, murs, sol, lumière...) et précise "do not change anything else";
   - pour les références, dis quoi en reprendre ("use the style/colour of image 2");
   - style photographique réaliste sauf demande contraire.
5. Choisis aspect_ratio : celui de l'image de base (au plus proche), sinon celui qui convient à la demande.
6. Questions : n'en pose QUE si une ambiguïté changerait vraiment le résultat et ne peut pas être tranchée raisonnablement (ex : on ne sait pas quel élément modifier). Sinon, fais le choix le plus cohérent avec la photo et le style, et note-le dans assumptions. Maximum 3 questions, courtes, avec des propositions de réponse. Si l'utilisateur a déjà répondu à des questions, n'en repose pas.

understanding, assumptions et questions sont en français, en langage simple.`;

const REVIEW_SYSTEM = `Tu contrôles la qualité d'un outil de génération d'images. On te donne la demande de l'utilisateur, la consigne envoyée au générateur, les images d'origine et l'image produite. Vérifie de façon exigeante :
- la demande est-elle entièrement respectée (bon élément, bon endroit, bonnes couleurs/matériaux, rien d'oublié) ?
- pour une retouche : le reste de l'image est-il préservé (cadrage, objets, lumière) ?
- défauts visibles : perspective fausse, objets déformés, texte illisible, artefacts.
Ne signale que des problèmes réels et significatifs. Si l'image convient, matches_request = true. Sinon, écris corrected_prompt : la consigne anglaise améliorée qui corrige précisément ces problèmes.`;

// ---------- Helpers ----------
function toClaudeImage(img) {
  return { type: "image", source: { type: "base64", media_type: img.mimeType, data: img.data } };
}

function readUploads(files = []) {
  return files.map((f) => {
    if (!SUPPORTED.has(f.mimetype)) throw new Error(`Format non supporté : ${f.originalname} (${f.mimetype}). Utilise JPG, PNG ou WebP.`);
    return { mimeType: f.mimetype, data: f.buffer.toString("base64"), name: f.originalname };
  });
}

function parseJSON(text, fallback) {
  try { return text ? JSON.parse(text) : fallback; } catch { return fallback; }
}

async function askClaude({ system, content, schema, effort = "high" }) {
  const response = await claude.beta.messages.parse({
    model: CLAUDE_MODEL,
    max_tokens: 16000,
    betas: ["server-side-fallback-2026-07-01"],
    fallbacks: "default",
    system,
    output_config: { effort, format: betaZodOutputFormat(schema) },
    messages: [{ role: "user", content }],
  });
  if (response.stop_reason === "refusal") {
    throw new Error("La demande a été refusée par le modèle d'analyse.");
  }
  if (!response.parsed_output) throw new Error("Réponse d'analyse illisible, réessaie.");
  return response.parsed_output;
}

function imageBlocks(images) {
  const blocks = [];
  images.forEach((img, i) => {
    blocks.push({ type: "text", text: `Image ${i} (${img.label || img.name || "fournie"}) :` });
    blocks.push(toClaudeImage(img));
  });
  return blocks;
}

function historyText(history) {
  if (!history.length) return "Aucun historique : c'est la première demande.";
  return history
    .map((h, i) => `Tour ${i + 1} — demande : "${h.request}"\n  compris : ${h.understanding || "-"}`)
    .join("\n");
}

async function plan({ prompt, images, history, answers }) {
  const content = [
    ...imageBlocks(images),
    {
      type: "text",
      text: [
        `Historique de la conversation :\n${historyText(history)}`,
        `Nouvelle demande de l'utilisateur :\n"""${prompt}"""`,
        answers ? `Réponses de l'utilisateur à tes questions :\n"""${answers}"""` : "",
        images.length ? `Nombre d'images jointes : ${images.length}.` : "Aucune image jointe : création à partir de zéro.",
      ].filter(Boolean).join("\n\n"),
    },
  ];
  const result = await askClaude({ system: PLAN_SYSTEM, content, schema: PlanSchema, effort: "high" });
  if (result.base_image_index >= images.length) result.base_image_index = images.length ? 0 : -1;
  if (answers) result.questions = [];
  return result;
}

async function generateImage({ prompt, images, baseIndex, aspectRatio }) {
  // Put the base image first so the model treats it as the canvas to edit.
  const ordered = baseIndex >= 0 ? [images[baseIndex], ...images.filter((_, i) => i !== baseIndex)] : images;
  const parts = [
    ...ordered.map((img) => ({ inlineData: { mimeType: img.mimeType, data: img.data } })),
    { text: prompt },
  ];
  const res = await gemini.models.generateContent({
    model: IMAGE_MODEL,
    contents: [{ role: "user", parts }],
    config: {
      responseModalities: ["TEXT", "IMAGE"],
      imageConfig: { aspectRatio, imageSize: IMAGE_SIZE },
    },
  });
  const outParts = res.candidates?.[0]?.content?.parts || [];
  const imgPart = outParts.find((p) => p.inlineData?.data);
  if (!imgPart) {
    const text = outParts.map((p) => p.text).filter(Boolean).join(" ");
    const reason = res.candidates?.[0]?.finishReason || res.promptFeedback?.blockReason || "";
    throw new Error(`Le générateur n'a pas renvoyé d'image. ${reason} ${text}`.trim());
  }
  return { mimeType: imgPart.inlineData.mimeType || "image/png", data: imgPart.inlineData.data };
}

async function review({ request, prompt, images, result }) {
  const content = [
    ...imageBlocks(images),
    { type: "text", text: "Image produite par le générateur :" },
    toClaudeImage(result),
    { type: "text", text: `Demande de l'utilisateur : """${request}"""\n\nConsigne envoyée au générateur : """${prompt}"""` },
  ];
  return askClaude({ system: REVIEW_SYSTEM, content, schema: ReviewSchema, effort: "medium" });
}

function inputsFrom(req) {
  const images = readUploads(req.files);
  const labels = parseJSON(req.body.labels, []);
  images.forEach((img, i) => { if (labels[i]) img.label = labels[i]; });
  return {
    prompt: (req.body.prompt || "").trim(),
    answers: (req.body.answers || "").trim(),
    history: parseJSON(req.body.history, []),
    images,
  };
}

function friendly(err) {
  if (err instanceof Anthropic.AuthenticationError) return "Clé ANTHROPIC_API_KEY absente ou invalide (fichier .env).";
  if (err instanceof Anthropic.RateLimitError) return "Trop de demandes vers Claude, réessaie dans une minute.";
  if (/API key|PERMISSION_DENIED|API_KEY_INVALID/i.test(err.message)) return "Clé GEMINI_API_KEY absente ou invalide (fichier .env).";
  return err.message;
}

// ---------- Routes ----------
app.post("/api/plan", upload.array("images", 8), async (req, res) => {
  try {
    const input = inputsFrom(req);
    if (!input.prompt) return res.status(400).json({ error: "Écris ta demande." });
    res.json(await plan(input));
  } catch (err) {
    console.error(err.message);
    res.status(500).json({ error: friendly(err) });
  }
});

app.post("/api/generate", upload.array("images", 8), async (req, res) => {
  try {
    const input = inputsFrom(req);
    const p = parseJSON(req.body.plan, null);
    if (!p?.image_prompt) return res.status(400).json({ error: "Plan manquant." });

    let prompt = p.image_prompt;
    const attempts = [];
    let result;
    for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
      result = await generateImage({ prompt, images: input.images, baseIndex: p.base_image_index, aspectRatio: p.aspect_ratio });
      const check = await review({ request: input.prompt, prompt, images: input.images, result });
      attempts.push({ attempt, prompt, ok: check.matches_request, issues: check.issues });
      if (check.matches_request || attempt === MAX_ATTEMPTS || !check.corrected_prompt) break;
      prompt = check.corrected_prompt;
    }
    res.json({ image: result, attempts });
  } catch (err) {
    console.error(err.message);
    res.status(500).json({ error: friendly(err) });
  }
});

app.listen(PORT, () => {
  console.log(`image-ai prêt sur http://localhost:${PORT}`);
  if (!process.env.GEMINI_API_KEY) console.warn("⚠ GEMINI_API_KEY manquante (voir .env.example)");
});
