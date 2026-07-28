import type { ToolConfig } from "./types";

export interface Track {
  slug: string;
  name: string;
  fullName: string;
  color: string;
  personality: string;
  hashtag: string;
  icon: string;
  specializationUrl: string;
  journeys: string[];
  timeLimit: number;
  adminPrompt: string;
  toolConfig: ToolConfig;
}

export const TRACKS: Track[] = [
  {
    slug: "front-end",
    name: "Front End",
    fullName: "Front End Development",
    color: "#5521B5",
    personality: "The Craftsman",
    hashtag: "#TheCraftsman",
    icon: "Code",
    specializationUrl: "https://sprints.ai/en-eg/sprint-up/plus/4",
    journeys: ["Web Foundations", "JavaScript", "React.js Development"],
    timeLimit: 45,
    adminPrompt:
      "Assess HTML and CSS fundamentals including semantic markup, flexbox, grid, and responsive design. Test JavaScript core concepts: closures, async/await, event loop, and DOM manipulation. Evaluate React knowledge: hooks, state management, component composition. Include modern tooling awareness. Focus on practical coding ability and browser behavior understanding.",
    toolConfig: {
      mcq:           { enabled: true,  count: 3 },
      voice:         { enabled: true,  count: 1 },
      coding:        { enabled: true,  count: 2, language: "javascript" },
      visualization: { enabled: true,  count: 1 },
      task:          { enabled: false, count: 0 },
    },
  },
  {
    slug: "back-end",
    name: "Back End",
    fullName: "Back End Development",
    color: "#0D3B6E",
    personality: "The Architect",
    hashtag: "#TheArchitect",
    icon: "Server",
    specializationUrl: "https://sprints.ai/en-eg/sprint-up/plus/8",
    journeys: ["Web Foundations", "JavaScript", "Intro to Back-end", "Database for Back-end", "Node.js & Express.js"],
    timeLimit: 45,
    adminPrompt:
      "Assess REST API design principles (HTTP methods, status codes, versioning), Node.js fundamentals (event loop, streams, modules), Express.js routing and middleware, database concepts (SQL joins, indexing, transactions, ORM), JWT authentication, and system design thinking (scalability, error handling, caching strategies).",
    toolConfig: {
      mcq:           { enabled: true,  count: 3 },
      voice:         { enabled: true,  count: 1 },
      coding:        { enabled: true,  count: 2, language: "javascript" },
      visualization: { enabled: true,  count: 1 },
      task:          { enabled: false, count: 0 },
    },
  },
  {
    slug: "ai-ml",
    name: "AI & Machine Learning",
    fullName: "AI & Machine Learning",
    color: "#1E3A5F",
    personality: "The Scientist",
    hashtag: "#TheScientist",
    icon: "Brain",
    specializationUrl: "https://sprints.ai/en-eg/sprint-up/plus/3",
    journeys: ["AI & Data Fundamentals", "Machine Learning", "Deep Learning", "Generative AI & LLMs", "MLOps"],
    timeLimit: 50,
    adminPrompt:
      "Assess supervised vs unsupervised learning, key algorithms (linear regression, decision trees, neural networks, clustering), model evaluation metrics (precision, recall, F1, ROC-AUC), data preprocessing and feature engineering, deep learning basics (backpropagation, CNNs, transformers), prompt engineering for LLMs, and MLOps fundamentals (model deployment, monitoring, versioning).",
    toolConfig: {
      mcq:           { enabled: true,  count: 3 },
      voice:         { enabled: true,  count: 1 },
      coding:        { enabled: true,  count: 1, language: "python" },
      visualization: { enabled: true,  count: 2 },
      task:          { enabled: false, count: 0 },
    },
  },
  {
    slug: "data-analytics",
    name: "Data Analytics",
    fullName: "Data Analytics",
    color: "#A81919",
    personality: "The Detective",
    hashtag: "#TheDetective",
    icon: "BarChart3",
    specializationUrl: "https://sprints.ai/en-eg/sprint-up/plus/9",
    journeys: ["AI & Data Fundamentals", "Data Analytics & Visualization"],
    timeLimit: 50,
    adminPrompt:
      "Assess SQL proficiency (joins, aggregations, window functions, CTEs), data cleaning and transformation skills, statistical concepts (distributions, correlation, hypothesis testing basics), data visualization principles (chart type selection, storytelling with data), Python/Pandas for data manipulation, and business intelligence thinking (KPI design, metric interpretation, dashboard structure).",
    toolConfig: {
      mcq:           { enabled: true,  count: 3 },
      voice:         { enabled: true,  count: 1 },
      coding:        { enabled: false, count: 0 },
      visualization: { enabled: true,  count: 3 },
      task:          { enabled: false, count: 0 },
    },
  },
  {
    slug: "product-management",
    name: "Product Management",
    fullName: "Product Management",
    color: "#1849C6",
    personality: "The Orchestrator",
    hashtag: "#TheOrchestrator",
    icon: "Briefcase",
    specializationUrl: "https://sprints.ai/en-eg/sprint-up/plus/5",
    journeys: ["Product Design (Advanced)", "Product Management Essentials", "Product Execution & Growth"],
    timeLimit: 55,
    adminPrompt:
      "Assess product discovery skills (user research, problem framing, opportunity sizing), prioritization frameworks (RICE, ICE, MoSCoW), requirements writing (user stories, acceptance criteria, PRDs), agile methodology (sprints, ceremonies, backlog grooming), product metrics and analytics (activation, retention, NPS), and stakeholder alignment and communication.",
    toolConfig: {
      mcq:           { enabled: true,  count: 3 },
      voice:         { enabled: true,  count: 2 },
      coding:        { enabled: false, count: 0 },
      visualization: { enabled: true,  count: 1 },
      task:          { enabled: true,  count: 1 },
    },
  },
  {
    slug: "ui-ux",
    name: "UI & UX",
    fullName: "UI & UX Design",
    color: "#0A6B4A",
    personality: "The Artisan",
    hashtag: "#TheArtisan",
    icon: "Palette",
    specializationUrl: "https://sprints.ai/en-eg/sprint-up/plus/10",
    journeys: ["User Experience Design (UX)", "User Interface Design with Figma"],
    timeLimit: 50,
    adminPrompt:
      "Assess UX research methods (user interviews, usability testing, card sorting), information architecture and user flow design, wireframing and prototyping concepts, UI design principles (visual hierarchy, typography, color theory, spacing systems), accessibility standards (WCAG basics), and design handoff and developer collaboration practices.",
    toolConfig: {
      mcq:           { enabled: true,  count: 3 },
      voice:         { enabled: true,  count: 2 },
      coding:        { enabled: false, count: 0 },
      visualization: { enabled: true,  count: 2 },
      task:          { enabled: true,  count: 1 },
    },
  },
  {
    slug: "digital-marketing",
    name: "Digital Marketing",
    fullName: "Digital Marketing",
    color: "#C05C08",
    personality: "The Amplifier",
    hashtag: "#TheAmplifier",
    icon: "Megaphone",
    specializationUrl: "https://sprints.ai/en-eg/sprint-up/plus/6",
    journeys: ["Foundations of Marketing", "Digital Marketing Channels", "Content Creation", "Media Production", "Media Buying & Performance", "Growth Strategies"],
    timeLimit: 45,
    adminPrompt:
      "Assess digital marketing channel knowledge (SEO, SEM, social media, email, content marketing), analytics and data interpretation (campaign metrics, attribution), paid media fundamentals (campaign structure, bidding, ad formats), content strategy and copywriting principles, marketing funnel and conversion optimization, and ROI measurement and reporting.",
    toolConfig: {
      mcq:           { enabled: true,  count: 3 },
      voice:         { enabled: true,  count: 2 },
      coding:        { enabled: false, count: 0 },
      visualization: { enabled: true,  count: 2 },
      task:          { enabled: false, count: 0 },
    },
  },
  {
    slug: "mobile",
    name: "Mobile Development",
    fullName: "Mobile Development",
    color: "#6B2D8B",
    personality: "The Builder",
    hashtag: "#TheBuilder",
    icon: "Smartphone",
    specializationUrl: "https://sprints.ai/en-eg/sprint-up/plus/7",
    journeys: ["Software Engineering", "Mobile Dev Fundamentals", "Flutter Development", "Advanced Flutter"],
    timeLimit: 45,
    adminPrompt:
      "Assess Dart language fundamentals and Flutter framework (widgets, state management with Provider/Bloc/Riverpod, navigation patterns), mobile UX conventions (gesture handling, responsive layouts, platform differences), app lifecycle management, async programming (futures, streams, isolates), REST API integration and error handling, and mobile performance considerations.",
    toolConfig: {
      mcq:           { enabled: true,  count: 3 },
      voice:         { enabled: true,  count: 1 },
      coding:        { enabled: true,  count: 2, language: "python" },
      visualization: { enabled: true,  count: 1 },
      task:          { enabled: false, count: 0 },
    },
  },
  {
    slug: "software-testing",
    name: "Software Testing",
    fullName: "Software Testing",
    color: "#1A4731",
    personality: "The Guardian",
    hashtag: "#TheGuardian",
    icon: "ShieldCheck",
    specializationUrl: "https://sprints.ai/en-eg/sprint-up/plus/11",
    journeys: ["Web Foundations", "Software Testing", "Testing Toolkit", "Java for Testing"],
    timeLimit: 45,
    adminPrompt:
      "Assess software testing types (unit, integration, system, acceptance, regression), test planning and test case design techniques (equivalence partitioning, boundary value analysis, decision tables), test automation fundamentals (Selenium, Playwright, or JUnit basics), bug reporting best practices (severity, reproducibility, evidence), API testing concepts, and QA integration in the software development lifecycle.",
    toolConfig: {
      mcq:           { enabled: true,  count: 3 },
      voice:         { enabled: true,  count: 1 },
      coding:        { enabled: true,  count: 2, language: "java" },
      visualization: { enabled: true,  count: 1 },
      task:          { enabled: false, count: 0 },
    },
  },
];

export const getTrackBySlug = (slug: string): Track | undefined =>
  TRACKS.find((t) => t.slug === slug);
