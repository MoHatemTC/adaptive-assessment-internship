-- ============================================================
-- Migration 004: Onboarding tracks + 9 default templates
-- Run this in Supabase SQL Editor
-- ============================================================

-- Step 1: Add track + time limit columns to assessment_templates
ALTER TABLE assessment_templates
  ADD COLUMN IF NOT EXISTS track_slug         VARCHAR(50),
  ADD COLUMN IF NOT EXISTS is_default         BOOLEAN DEFAULT false,
  ADD COLUMN IF NOT EXISTS time_limit_minutes INT     DEFAULT 60;

CREATE INDEX IF NOT EXISTS idx_templates_track_default
  ON assessment_templates(track_slug)
  WHERE is_default = true;

-- ============================================================
-- Step 2: Seed 9 default assessment templates
-- ============================================================

INSERT INTO assessment_templates
  (title, track, track_slug, is_default, is_published, time_limit_minutes, admin_prompt, tool_config)
VALUES

-- 1. Front End
(
  'Front End Smart Assessment',
  'Front End Development', 'front-end', true, false, 45,
  'Assess HTML and CSS fundamentals including semantic markup, flexbox, grid, and responsive design. Test JavaScript core concepts: closures, async/await, event loop, and DOM manipulation. Evaluate React knowledge: hooks, state management, component composition. Include modern tooling awareness. Focus on practical coding ability and browser behavior understanding.',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 1, "coding": true, "coding_count": 2, "visualization": true, "visualization_count": 1, "task": false, "task_count": 0}'::jsonb
),

-- 2. Back End
(
  'Back End Smart Assessment',
  'Back End Development', 'back-end', true, false, 45,
  'Assess REST API design principles (HTTP methods, status codes, versioning), Node.js fundamentals (event loop, streams, modules), Express.js routing and middleware, database concepts (SQL joins, indexing, transactions, ORM), JWT authentication, and system design thinking (scalability, error handling, caching strategies).',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 1, "coding": true, "coding_count": 2, "visualization": true, "visualization_count": 1, "task": false, "task_count": 0}'::jsonb
),

-- 3. AI & Machine Learning
(
  'AI & Machine Learning Smart Assessment',
  'AI & Machine Learning', 'ai-ml', true, false, 50,
  'Assess supervised vs unsupervised learning, key algorithms (linear regression, decision trees, neural networks, clustering), model evaluation metrics (precision, recall, F1, ROC-AUC), data preprocessing and feature engineering, deep learning basics (backpropagation, CNNs, transformers), prompt engineering for LLMs, and MLOps fundamentals (model deployment, monitoring, versioning).',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 1, "coding": true, "coding_count": 1, "visualization": true, "visualization_count": 2, "task": false, "task_count": 0}'::jsonb
),

-- 4. Data Analytics
(
  'Data Analytics Smart Assessment',
  'Data Analytics', 'data-analytics', true, false, 50,
  'Assess SQL proficiency (joins, aggregations, window functions, CTEs), data cleaning and transformation skills, statistical concepts (distributions, correlation, hypothesis testing basics), data visualization principles (chart type selection, storytelling with data), Python/Pandas for data manipulation, and business intelligence thinking (KPI design, metric interpretation, dashboard structure).',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 1, "coding": false, "coding_count": 0, "visualization": true, "visualization_count": 3, "task": false, "task_count": 0}'::jsonb
),

-- 5. Product Management
(
  'Product Management Smart Assessment',
  'Product Management', 'product-management', true, false, 55,
  'Assess product discovery skills (user research, problem framing, opportunity sizing), prioritization frameworks (RICE, ICE, MoSCoW), requirements writing (user stories, acceptance criteria, PRDs), agile methodology (sprints, ceremonies, backlog grooming), product metrics and analytics (activation, retention, NPS), and stakeholder alignment and communication.',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 2, "coding": false, "coding_count": 0, "visualization": true, "visualization_count": 1, "task": true, "task_count": 1}'::jsonb
),

-- 6. UI & UX
(
  'UI & UX Smart Assessment',
  'UI & UX Design', 'ui-ux', true, false, 50,
  'Assess UX research methods (user interviews, usability testing, card sorting), information architecture and user flow design, wireframing and prototyping concepts, UI design principles (visual hierarchy, typography, color theory, spacing systems), accessibility standards (WCAG basics), and design handoff and developer collaboration practices.',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 2, "coding": false, "coding_count": 0, "visualization": true, "visualization_count": 2, "task": true, "task_count": 1}'::jsonb
),

-- 7. Digital Marketing
(
  'Digital Marketing Smart Assessment',
  'Digital Marketing', 'digital-marketing', true, false, 45,
  'Assess digital marketing channel knowledge (SEO, SEM, social media, email, content marketing), analytics and data interpretation (campaign metrics, attribution), paid media fundamentals (campaign structure, bidding, ad formats), content strategy and copywriting principles, marketing funnel and conversion optimization, and ROI measurement and reporting.',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 2, "coding": false, "coding_count": 0, "visualization": true, "visualization_count": 2, "task": false, "task_count": 0}'::jsonb
),

-- 8. Mobile Development
(
  'Mobile Development Smart Assessment',
  'Mobile Development', 'mobile', true, false, 45,
  'Assess Dart language fundamentals and Flutter framework (widgets, state management with Provider/Bloc/Riverpod, navigation patterns), mobile UX conventions (gesture handling, responsive layouts, platform differences), app lifecycle management, async programming (futures, streams, isolates), REST API integration and error handling, and mobile performance considerations.',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 1, "coding": true, "coding_count": 2, "visualization": true, "visualization_count": 1, "task": false, "task_count": 0}'::jsonb
),

-- 9. Software Testing
(
  'Software Testing Smart Assessment',
  'Software Testing', 'software-testing', true, false, 45,
  'Assess software testing types (unit, integration, system, acceptance, regression), test planning and test case design techniques (equivalence partitioning, boundary value analysis, decision tables), test automation fundamentals (Selenium, Playwright, or JUnit basics), bug reporting best practices (severity, reproducibility, evidence), API testing concepts, and QA integration in the software development lifecycle.',
  '{"mcq": true, "mcq_count": 3, "voice": true, "voice_count": 1, "coding": true, "coding_count": 2, "visualization": true, "visualization_count": 1, "task": false, "task_count": 0}'::jsonb
);
