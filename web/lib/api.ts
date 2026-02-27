export interface RunMeta {
  run_date: string;
  total_count: number;
  stored_at: string;
}

export interface ScoreMap {
  relevance?: number;
  novelty?: number;
  rigor?: number;
  impact?: number;
  total?: number;
}

export interface SimilarPaper {
  paper_id: string;
  title: string;
  venue: string;
  date: string;
  score: number;
}

export interface Report {
  methods_detailed?: string;
  main_conclusion?: string;
  future_direction?: string;
  value_assessment?: string;
  ai_feed_summary?: string;
}

export interface PaperCard {
  paper_id: string;
  title: string;
  venue: string;
  date: string;
  link: string;
  downloaded_pdf_url?: string;
  scores?: ScoreMap;
  source_abstract?: string;
  report?: Report;
  similar?: SimilarPaper[];
  tags?: string[];
  methods_in_one_line?: string;
  main_conclusion?: string;
  future_direction?: string;
  value_assessment?: string;
  ai_feed_summary?: string;
}

export interface RunData {
  run_date: string;
  report_cards: PaperCard[];
  also_notable: PaperCard[];
  slack_text: string;
  total_count: number;
  stored_at: string;
}

export interface AppSettings {
  language: "en" | "zh";
  timezone: string;
  journals: string[];
  custom_journals: string[];
  fields: string[];
  download_pdf: boolean;
  api_provider: "openai" | "gemini";
  openai_api_key: string;
  gemini_api_key: string;
  api_model: string;
  max_reports: number;
  date_days: number;
  strict_journal: boolean;
  exclude_keywords: string;
  webhook_url: string;
  archive_db: string;
  journal_options: string[];
  field_options: string[];
}

export interface NetworkNode {
  id: string;
  title: string;
  venue: string;
  date: string;
  link: string;
  group: string;
}

export interface NetworkEdge {
  source: string;
  target: string;
  weight: number;
  similarity: number;
}

export interface NetworkData {
  nodes: NetworkNode[];
  edges: NetworkEdge[];
}

// ── Client-side defaults (used when backend is offline) ────────────────────

export const JOURNAL_OPTIONS_DEFAULT = [
  // ── Preprints ──
  "arXiv","bioRxiv","medRxiv","SSRN","ChemRxiv","PsyArXiv","SocArXiv",

  // ── Nature family ──
  "Nature","Nature Medicine","Nature Biotechnology","Nature Genetics",
  "Nature Neuroscience","Nature Machine Intelligence","Nature Communications",
  "Nature Methods","Nature Chemical Biology","Nature Aging","Nature Cancer",
  "Nature Metabolism","Nature Immunology","Nature Cell Biology",
  "Nature Structural & Molecular Biology","Nature Reviews Cancer",
  "Nature Reviews Neuroscience","Nature Reviews Immunology",
  "Nature Reviews Genetics","Nature Reviews Drug Discovery",
  "Nature Reviews Molecular Cell Biology","Nature Reviews Clinical Oncology",
  "Nature Microbiology","Nature Chemical Engineering","Nature Computational Science",
  "Scientific Reports","npj Digital Medicine","npj Genomic Medicine",
  "npj Regenerative Medicine","npj Aging","npj Precision Oncology",

  // ── Science family ──
  "Science","Science Translational Medicine","Science Advances",
  "Science Robotics","Science Immunology","Science Signaling",

  // ── Cell family ──
  "Cell","Cell Reports","Cell Metabolism","Cell Systems","Cell Host & Microbe",
  "Molecular Cell","Cancer Cell","Cell Stem Cell","Cell Chemical Biology",
  "Cell Genomics","Current Biology","Developmental Cell","iScience",

  // ── High-impact clinical ──
  "The Lancet","The Lancet Digital Health","The Lancet Oncology",
  "The Lancet Neurology","The Lancet Public Health","The Lancet Infectious Diseases",
  "The Lancet Psychiatry","The Lancet Regional Health","eClinicalMedicine",
  "NEJM","NEJM Evidence","NEJM AI",
  "JAMA","JAMA Network Open","JAMA Oncology","JAMA Neurology",
  "JAMA Psychiatry","JAMA Internal Medicine","JAMA Cardiology",
  "JAMA Pediatrics","JAMA Surgery","JAMA Dermatology",
  "BMJ","BMJ Open","BMJ Mental Health","BMJ Oncology",
  "Annals of Internal Medicine","CMAJ","MJA",

  // ── Open access / PLOS / eLife ──
  "eLife","PLOS ONE","PLOS Biology","PLOS Medicine",
  "PLOS Computational Biology","PLOS Genetics","PLOS Pathogens",
  "PNAS","PNAS Nexus","F1000Research","Wellcome Open Research",

  // ── Bioinformatics / Genomics / Omics ──
  "Bioinformatics","Briefings in Bioinformatics","Nucleic Acids Research",
  "Genome Biology","Genome Research","Genome Medicine","Genomics",
  "American Journal of Human Genetics","Human Genetics","Human Molecular Genetics",
  "Molecular Biology and Evolution","BMC Genomics","BMC Bioinformatics",
  "Cell Genomics","Epigenetics","Epigenetics & Chromatin",
  "Journal of Proteome Research","Molecular & Cellular Proteomics",
  "Metabolomics","Nature Protocols",

  // ── AI / ML / CV / NLP ──
  "IEEE Transactions on Pattern Analysis and Machine Intelligence",
  "IEEE Transactions on Medical Imaging",
  "IEEE Transactions on Neural Networks and Learning Systems",
  "IEEE Journal of Biomedical and Health Informatics",
  "Artificial Intelligence in Medicine","Journal of Biomedical Informatics",
  "Medical Image Analysis","Computer Methods and Programs in Biomedicine",
  "ICML","NeurIPS","CVPR","ECCV","ICCV","MICCAI","MIDL",
  "AAAI","IJCAI","ICLR","ACL","EMNLP","NAACL","COLING",
  "Transactions on Machine Learning Research","Journal of Machine Learning Research",

  // ── Oncology ──
  "Journal of Clinical Oncology","Annals of Oncology",
  "Cancer Discovery","Cancer Research","Cancer Cell",
  "Clinical Cancer Research","Cancer Medicine","Cancer Immunology Research",
  "ESMO Open","JCO Precision Oncology","Oncogene","Molecular Cancer",
  "Breast Cancer Research","Leukemia","Blood Cancer Journal",

  // ── Cardiology & Vascular ──
  "Circulation","Circulation Research","European Heart Journal",
  "JACC","JACC Cardiovascular Imaging","Heart Rhythm",
  "Arteriosclerosis Thrombosis and Vascular Biology",
  "Journal of the American Heart Association","Hypertension",

  // ── Hematology ──
  "Blood","American Journal of Hematology","Haematologica",
  "British Journal of Haematology","Journal of Hematology & Oncology",

  // ── Neuroscience ──
  "Neuron","Nature Neuroscience","Trends in Neurosciences",
  "Journal of Neuroscience","NeuroImage","Cerebral Cortex",
  "Human Brain Mapping","Brain and Behavior","Cortex",
  "Neuropsychopharmacology","Biological Psychiatry",

  // ── Aging & Neurodegeneration ──
  "Aging Cell","Ageing Research Reviews","GeroScience",
  "Journals of Gerontology","Age and Ageing","Experimental Gerontology",
  "Journal of Alzheimer's Disease","Alzheimer's & Dementia",
  "Alzheimer's Research & Therapy","Acta Neuropathologica",
  "Brain","JAMA Neurology","Lancet Neurology","Annals of Neurology",
  "Journal of Neurology Neurosurgery and Psychiatry",
  "Movement Disorders","Parkinsonism & Related Disorders",
  "npj Parkinson's Disease","Brain and Cognition","Cognitive Neuroscience",
  "Frontiers in Aging Neuroscience","Frontiers in Neurology",
  "Neurobiology of Aging","Neurobiology of Disease",
  "Annals of Clinical and Translational Neurology",
  "Journal of Neuroinflammation","Neuropathology and Applied Neurobiology",
  "Acta Neuropathologica Communications","Brain Communications",

  // ── Psychiatry & Psychology ──
  "World Psychiatry","Molecular Psychiatry","Translational Psychiatry",
  "JAMA Psychiatry","Psychological Medicine","Neuropsychopharmacology",
  "Depression and Anxiety","Journal of Affective Disorders","Schizophrenia Bulletin",

  // ── Immunology & Infection ──
  "Immunity","Journal of Experimental Medicine","Journal of Immunology",
  "Frontiers in Immunology","Cell Host & Microbe",
  "Nature Microbiology","mBio","PLOS Pathogens","Mucosal Immunology",
  "European Journal of Immunology","Clinical Immunology",

  // ── Metabolism & Endocrinology ──
  "Cell Metabolism","Diabetes","Diabetes Care","Diabetologia",
  "Journal of Clinical Endocrinology & Metabolism","Obesity",
  "Metabolism","Molecular Metabolism","Endocrinology",
  "Thyroid","European Journal of Endocrinology",

  // ── Respiratory / Pulmonary ──
  "American Journal of Respiratory and Critical Care Medicine",
  "Thorax","Chest","European Respiratory Journal",
  "Respiratory Research","ERJ Open Research",

  // ── Gastroenterology ──
  "Gastroenterology","Gut","Hepatology","Journal of Hepatology",
  "Nature Reviews Gastroenterology & Hepatology",
  "Alimentary Pharmacology & Therapeutics","Journal of Crohn's and Colitis",

  // ── Nephrology & Urology ──
  "Journal of the American Society of Nephrology","Kidney International",
  "American Journal of Kidney Diseases","Nephrology Dialysis Transplantation",

  // ── Rheumatology / Autoimmune ──
  "Annals of the Rheumatic Diseases","Arthritis & Rheumatology",
  "Rheumatology","Lupus Science & Medicine",

  // ── Radiology / Imaging ──
  "Radiology","AJR American Journal of Roentgenology",
  "European Radiology","Radiology: Artificial Intelligence",
  "Academic Radiology","Journal of Magnetic Resonance Imaging",

  // ── Biochemistry / Structural ──
  "Journal of Biological Chemistry","EMBO Journal","EMBO Reports",
  "Molecular Biology of the Cell","Biochemistry","Structure",
  "Journal of Molecular Biology","Acta Crystallographica D",
  "Nature Structural & Molecular Biology",

  // ── Drug Discovery / Pharmacology ──
  "Journal of Medicinal Chemistry","European Journal of Medicinal Chemistry",
  "Drug Discovery Today","ACS Chemical Biology","ChemMedChem",
  "Pharmacological Reviews","British Journal of Pharmacology",

  // ── Systems / Computational Biology ──
  "Molecular Systems Biology","PLOS Computational Biology",
  "Biophysical Journal","Physical Biology",
  "Journal of Chemical Information and Modeling",
  "Systems Biology and Applications",

  // ── Stem Cells & Regenerative ──
  "Cell Stem Cell","Stem Cell Reports","Stem Cells","Stem Cell Research",
  "npj Regenerative Medicine","Biomaterials",

  // ── Ophthalmology ──
  "JAMA Ophthalmology","Ophthalmology","Investigative Ophthalmology & Visual Science",
  "British Journal of Ophthalmology","Progress in Retinal and Eye Research",

  // ── Dermatology ──
  "JAMA Dermatology","Journal of Investigative Dermatology",
  "British Journal of Dermatology",

  // ── Surgery & Critical Care ──
  "Annals of Surgery","JAMA Surgery","Critical Care Medicine","Intensive Care Medicine",
  "Surgical Endoscopy",

  // ── Pediatrics ──
  "JAMA Pediatrics","Pediatrics","Journal of Pediatrics",
  "Archives of Disease in Childhood",

  // ── Alzheimer's / Dementia specific ──
  "Journal of Alzheimer's Disease & Parkinsonism",
  "Alzheimer's & Dementia: Diagnosis Assessment & Disease Monitoring",
  "Alzheimer's & Dementia: Translational Research & Clinical Interventions",
  "Dementia and Geriatric Cognitive Disorders",
  "International Journal of Geriatric Psychiatry",
  "Journal of the Neurological Sciences",
  "Neuropsychology","Neuropsychology Review",
  "Frontiers in Dementia",

  // ── Longevity / Geroscience ──
  "Cell Longevity","npj Aging","Biogerontology",
  "Aging (Albany NY)","Rejuvenation Research","Longevity & Health",
  "Journal of Gerontology: Biological Sciences",
  "Journal of Gerontology: Medical Sciences",

  // ── Sleep ──
  "Sleep","SLEEP Advances","Journal of Sleep Research",
  "Sleep Medicine","Sleep Medicine Reviews",

  // ── Stroke / Cerebrovascular ──
  "Stroke","Stroke and Vascular Neurology",
  "Journal of Cerebral Blood Flow & Metabolism",
  "International Journal of Stroke","Cerebrovascular Diseases",

  // ── Multiple Sclerosis / Neuroimmunology ──
  "Multiple Sclerosis Journal","Neurology: Neuroimmunology & Neuroinflammation",
  "Journal of Neuroimmunology","Journal of Neuroimmunology and Neuroinflammation",
  "ECTRIMS","Therapeutic Advances in Neurological Disorders",

  // ── Epilepsy ──
  "Epilepsia","Brain and Behavior","Epilepsy Research",
  "Seizure: European Journal of Epilepsy",

  // ── Pain / Headache ──
  "Pain","Cephalalgia","Headache","Journal of Pain",

  // ── Rehabilitation ──
  "Journal of NeuroEngineering and Rehabilitation",
  "Disability and Rehabilitation",

  // ── Environmental / Social Epidemiology ──
  "Environmental Health Perspectives","International Journal of Epidemiology",
  "American Journal of Epidemiology","Epidemiology","European Journal of Epidemiology",

  // ── Microbiome / Gut-Brain ──
  "Gut Microbes","Microbiome","Cell Host & Microbe",
  "npj Biofilms and Microbiomes",

  // ── Genetics / Rare Disease ──
  "Genetics in Medicine","European Journal of Human Genetics",
  "Orphanet Journal of Rare Diseases","Journal of Medical Genetics",
  "American Journal of Medical Genetics",

  // ── Neuro-oncology ──
  "Neuro-Oncology","Neuro-Oncology Advances","Journal of Neuro-Oncology",
  "Cancer Neuroscience",

  // ── Interventional / Translational ──
  "Translational Neurodegeneration","Translational Psychiatry",
  "NPP – Neuropsychopharmacology and Neuroscience",
  "Journal of Translational Medicine",
];

export const FIELD_OPTIONS_DEFAULT = [
  // General AI/CS
  "AI","Machine Learning","Deep Learning","Reinforcement Learning",
  "Large Language Models","Foundation Models","Generative AI",
  "Computer Vision","NLP","Natural Language Processing","Robotics",
  "Statistics","Causal Inference","Federated Learning",
  // Biology & Medicine
  "Healthcare","Biology","Medicine","Bioinformatics",
  "Systems Biology","Computational Biology","Single-cell Analysis",
  "Spatial Transcriptomics","Multi-omics","Epigenetics",
  "Genomics","Proteomics","Metabolomics","Structural Biology",
  // Aging & Brain
  "Aging","Longevity","Senescence","Geroscience",
  "Alzheimer's Disease","Parkinson's Disease","Neurodegeneration",
  "Dementia","Cognitive Decline","Neuroinflammation",
  "Neuroscience","Psychiatry","Sleep","Brain Imaging",
  // Disease areas
  "Oncology","Cancer","Immunotherapy","Cardiology","Cardiovascular",
  "Immunology","Autoimmune","Infectious Disease","Virology",
  "Metabolism","Diabetes","Obesity","Endocrinology",
  "Hematology","Gastroenterology","Nephrology","Pulmonology","Rheumatology",
  // Research types
  "Drug Discovery","Clinical Trial","Epidemiology","Biomarkers",
  "Precision Medicine","Gene Therapy","Cell Therapy","CRISPR",
  "Protein Structure","Drug Design","Pharmacology",
  "Regenerative Medicine","Stem Cells",
];

const SETTINGS_KEY = "research_pipeline_settings_v1";

function detectBrowserTimezone(): string {
  if (typeof window === "undefined") return "UTC";
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const DEFAULT_SETTINGS: AppSettings = {
  language: "en",
  timezone: "UTC",
  journals: [],
  custom_journals: [],
  fields: [],
  download_pdf: true,
  api_provider: "gemini",
  openai_api_key: "",
  gemini_api_key: "",
  api_model: "gemini-2.5-flash-lite",
  max_reports: 5,
  date_days: 3,
  strict_journal: true,
  exclude_keywords: "",
  webhook_url: "",
  archive_db: "",
  journal_options: JOURNAL_OPTIONS_DEFAULT,
  field_options: FIELD_OPTIONS_DEFAULT,
};

// ── localStorage helpers ───────────────────────────────────────────────────

export function loadLocalSettings(): AppSettings {
  if (typeof window === "undefined") return { ...DEFAULT_SETTINGS };
  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Partial<AppSettings>;
    const merged = {
      ...DEFAULT_SETTINGS,
      ...parsed,
      timezone: (parsed.timezone || detectBrowserTimezone()),
      // always keep full option lists client-side
      journal_options: JOURNAL_OPTIONS_DEFAULT,
      field_options: FIELD_OPTIONS_DEFAULT,
    };
    // One-time migration for older localStorage without provider field.
    if (!("api_provider" in parsed)) {
      merged.api_provider = parsed.openai_api_key ? "openai" : "gemini";
      if (!parsed.api_model) {
        merged.api_model = merged.api_provider === "gemini" ? "gemini-2.5-flash-lite" : "gpt-4.1-mini";
      }
    }
    if (!parsed.timezone) {
      merged.timezone = detectBrowserTimezone();
    }
    if (merged.api_provider === "gemini" && ["gemini-2.0-flash-lite", "gemini-2.0-flash"].includes(merged.api_model)) {
      merged.api_model = "gemini-2.5-flash-lite";
    }
    return merged;
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveLocalSettings(s: AppSettings): void {
  if (typeof window === "undefined") return;
  // Don't persist the static option lists — they're always re-injected on load
  const { journal_options, field_options, ...rest } = s;
  void journal_options;
  void field_options;
  localStorage.setItem(SETTINGS_KEY, JSON.stringify(rest));
}

// ── Remote API (optional — used when Python backend is running) ────────────

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${path}: ${res.status} ${text}`);
  }
  return res.json() as Promise<T>;
}

export interface ReportDate {
  date: string;
  files: number;
}

export interface ReportFileMeta {
  name: string;
  size: number;
  title?: string;
  tags?: string[];
}

export interface NoteMeta {
  slug: string;
  name: string;
  tags: string[];
  folder: string;
  size: number;
  modified: number;
}

export interface NoteDetail {
  slug: string;
  name: string;
  content: string;
  modified: number;
}

export const api = {
  /** Load settings: localStorage is the source of truth.
   *  Backend is only consulted to fill archive_db if not set locally. */
  getSettings: async (): Promise<AppSettings> => {
    const local = loadLocalSettings();
    try {
      const remote = await apiFetch<AppSettings>("/api/settings");
      // Only borrow archive_db from backend as a fallback (server-side path)
      const merged: AppSettings = {
        ...local,
        archive_db: local.archive_db || remote.archive_db,
        journal_options: JOURNAL_OPTIONS_DEFAULT,
        field_options: FIELD_OPTIONS_DEFAULT,
      };
      return merged;
    } catch {
      return local;
    }
  },

  /** Save settings: always write localStorage, then try backend. */
  saveSettings: async (s: AppSettings): Promise<void> => {
    saveLocalSettings(s);
    try {
      await apiFetch<{ ok: boolean }>("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(s),
      });
    } catch {
      // Backend offline — localStorage save is enough
    }
  },

  listRuns: () => apiFetch<RunMeta[]>("/api/runs"),
  getRun: (date: string) => apiFetch<RunData>(`/api/runs/${date}`),
  getNetwork: (limit = 200, threshold = 0.25) =>
    apiFetch<NetworkData>(`/api/network?limit=${limit}&threshold=${threshold}&summarized_only=true`),

  /** Get content of a markdown report file */
  getReport: (date: string, filename: string) =>
    apiFetch<{ content: string; path: string }>(`/api/reports/${date}/${filename}`),

  /** List report files for a date */
  listReports: (date: string) =>
    apiFetch<{ date: string; path: string; files: ReportFileMeta[] }>(`/api/reports/${date}`),

  /** List downloaded PDF assets for a report date */
  listReportAssets: (date: string) =>
    apiFetch<{ date: string; files: { name: string; size: number }[] }>(`/api/reports/${date}/assets`),

  /** Save edited markdown content back to file */
  saveReport: (date: string, filename: string, content: string) =>
    apiFetch<{ ok: boolean; path: string }>(`/api/reports/${date}/${filename}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content }),
    }),

  deleteReport: (date: string, filename: string) =>
    apiFetch<{ ok: boolean }>(`/api/reports/${date}/${filename}`, { method: "DELETE" }),

  /** Generate a structured reference note for a paper */
  generateNote: (date: string, card: PaperCard, report: Report, similar: SimilarPaper[], settings: AppSettings) =>
    apiFetch<{ ok: boolean; slug: string; filename: string; path: string; content: string }>(
      `/api/papers/note`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date, card, report, similar, settings }),
      }
    ),

  /** Generate a summary for a paper on-demand and add it to a run */
  summarizePaper: (date: string, card: PaperCard, settings: AppSettings) =>
    apiFetch<{ ok: boolean; report: Report; md_path: string; card: PaperCard }>(
      `/api/papers/summarize`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date, card, settings }),
      }
    ),

  /** Try downloading/caching local PDF for a deep-read card */
  cachePaperPdf: (date: string, card: PaperCard) =>
    apiFetch<{ ok: boolean; downloaded_pdf_url?: string; source_pdf_url?: string; slug?: string; reason?: string }>(
      `/api/papers/cache-pdf`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ date, card }),
      }
    ),

  /** Delete a run */
  deleteRun: (date: string) =>
    apiFetch<{ ok: boolean }>(`/api/runs/${date}`, { method: "DELETE" }),

  /** Delete all report files for a date */
  deleteReportDate: (date: string) =>
    apiFetch<{ ok: boolean }>(`/api/reports/${date}`, { method: "DELETE" }),

  /** List all report dates */
  listReportDates: () => apiFetch<ReportDate[]>("/api/reports"),

  /** List user notes */
  listNotes: () => apiFetch<NoteMeta[]>("/api/notes"),

  /** Get a user note */
  getNote: (slug: string) => apiFetch<NoteDetail>(`/api/notes/${slug}`),

  /** Save a user note */
  saveNote: (slug: string, content: string, name: string = "") =>
    apiFetch<{ ok: boolean; slug: string }>(`/api/notes/${slug}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, name }),
    }),

  /** Delete a user note */
  deleteNote: (slug: string) =>
    apiFetch<{ ok: boolean }>(`/api/notes/${slug}`, { method: "DELETE" }),

  /** Update a note's folder assignment */
  patchNoteMeta: (slug: string, folder: string) =>
    apiFetch<{ ok: boolean }>("/api/notes-meta", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slug, folder }),
    }),

  /** Rename a folder across all notes */
  renameFolder: (old_name: string, new_name: string) =>
    apiFetch<{ ok: boolean }>("/api/folders/rename", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ old_name, new_name }),
    }),
};
