Project Description Matrix: Masaar - New Sprints Chat-based Adaptive Assessment Platform
Brief Description
The AI Adaptive Assessment Agent is a conversational assessment tool where the entire assessment runs as a single chat. An admin configures it with a short prompt and a few options and enables the tools allowed for that assessment. From this, the agent builds a blueprint — its plan for the workflow, covering which question types run, how many questions, the difficulty progression, and interview time limits. A learner enters a simple profile and then completes the assessment by chatting with the examiner agent, which can invoke five tools: an MCQ tool, a diagram/image reasoning tool, a time-boxed voice technical or HR interview, a camera-on technical or HR interview, and an E2B tool for executing learner code. A proctoring and integrity system runs across every question type — verifying identity, keeping the camera on, and detecting AI usage, tab switching, screenshots, and copy-paste — so results are well verified. The agent adapts difficulty and follow-ups, grades silently against rubrics with an LLM judge, scores results across five skill dimensions (Thinking, Soft, Work, Digital/AI, Growth), and emails a final report with a radar chart, feedback, and recommendations.
Problem Statement
Traditional assessments are often rigid, fragmented, and difficult to personalize at scale. Admins usually need technical support to configure assessments, learners move through static question flows, and results may lack enough evidence to verify skills, integrity, or readiness.
Masaar needs a chat-based adaptive assessment platform that can generate an assessment workflow from a simple admin prompt, run personalized learner assessments through one conversational interface, invoke different assessment tools when needed, verify learner integrity through proctoring, and produce reliable skill-based reports for learning and selection decisions.
Goals (What Success Looks Like)
An admin can launch an assessment from a short prompt and a few options, with no technical support.
The agent produces a clear blueprint (question plan, count, difficulty, time limits) and runs the session against it
The agent produces a shareable assessment link for the learner after filling the profile info. 
The agent has the full capability to run multi-sessions in parallel. 
A learner completes a personalized, adaptive assessment entirely within one chat.
The agent invokes its five tools appropriately and only when enabled.
The proctoring system verifies identity and integrity across all question types, so results are well verified.
Responses are graded consistently against rubrics, validated by an LLM judge.
Results map to the five skill dimensions and render as a radar chart with feedback and recommendations.
Admins receive clear, verified outputs to support selection, diagnosis, or learning-recommendation decisions. Moreover, the learner receives brief results via email upon assignment completion. 
Users/Customers
Learning Team  — configure assessments and review verified outputs.
Learners — complete the adaptive chat via a shared link.
Program Managers  — use results to make decisions and assign next learning actions.
List of Features (LoF)
Admin configuration: prompt + config + per-tool enable/disable.
Agent blueprint generation (workflow plan: question types, count, difficulty, time limits).
Shareable assessment link / session start.
Learner profile intake and consent.
Single adaptive agentic chat interface for the learner with option to run in parallel assessments. 
MCQ Protocol tool (structured items, objectively scored).
Diagram / image reasoning tool (visual questions).
Voice technical/HR interview tool (live, time-boxed, transcribed).
Camera-on technical/HR interview tool (live, camera required).
E2B code-execution tool (sandboxed coding).
Proctoring & integrity system (identity, forced camera, AI-usage / tab / screenshot / copy-paste detection) across all question types.
Conversational difficulty adaptation with LLM follow-ups.
Silent rubric-based grading (MCQ, diagram, code, voice, interview).
LLM judge validation of grading.
Five-dimension scoring and radar-chart report with email delivery.
Functional Requirements
The system shall expose admin-configuration and learner-session endpoints via a FastAPI backend, letting an admin create an assessment from a prompt and basic config (skill weighting, question count, difficulty range, interview time limits).
The system shall let the admin enable or disable each tool (MCQ, diagram/image, voice interview, camera interview, E2B) per assessment with ability to run multiple sessions in parallel 
The system shall capture assessment objective, target role, and target learner level.
The system shall generate an agent blueprint (question types, order, count, difficulty progression, time limits, tool usage) and store/retrieve supporting content and rubrics using a Qdrant vector database.
The system shall generate a shareable link that starts a learner session and return a session/tracking ID via the API.
The system shall collect a simple learner profile and consent (including camera/voice capture) before starting, through the Next.js (or Streamlit) frontend.
The system shall verify the learner's identity at session start before the assessment begins.
The system shall require the learner's camera to remain on for the full assessment, across all question types.
The system shall detect and flag integrity violations — AI-tool usage, tab/window switching, screenshots, and copy-paste — via the proctoring module.
The system shall run the examiner as an agent built with LangChain / LangGraph, driving the assessment as a single chat conversation.
The system shall let the agent invoke the MCQ tool to present structured items and score them objectively against an answer key.
The system shall let the agent invoke the diagram/image tool to display a visual and pose a question about it.
The system shall let the agent invoke the voice tool to conduct a time-boxed voice technical or HR interview, transcribing responses via a speech-to-text service.
The system shall let the agent invoke the camera tool to conduct a camera-on technical or HR interview with live avatar if possible.
The system shall adapt all questions types, difficulty and content based on the user profile with progressive memory stored in a vector database as Qdrant. 
The system shall let the agent invoke the E2B tool to execute learner code in a sandbox and capture results for grading as coding challenges feature.
The system shall select each next question's difficulty from the learner's profile and prior answers, within the blueprint.
The system shall grade answers using objective scoring (MCQ, E2B code execution) and LLM rubric grading, without showing scores during the session.
The system saves all interactions and communication in a system database such as Postgres or Supabase. 
The system shall validate grading quality and consistency using an LLM judge, evaluated with DeepEval / G-Eval.
The system shall log every agent action, tool call, and LLM call — with execution time and token cost — using Langfuse / LangSmith, and incorporate proctoring flags into the result's verification status.
The system shall aggregate results into the five skill dimensions and generate and email a final report (radar chart, scores, feedback, recommendations, integrity status) to the learner and admin.
The system is version control with PR workflow systems and human review from day 0 with CI checks. 
Non-Functional Requirements
Scalability: Long-running work — live interviews, E2B code execution, rubric grading, and report generation — must run on decoupled background workers (e.g., Celery + Redis) so the FastAPI layer never blocks; the system must sustain at least 100 sessions per day at MVP.
Resilience / Guardrails: The agent workflow must include fallback handling for LLM/API rate limits (retry with backoff) and robust recovery for agent loops — max-iteration caps and per-tool timeouts so a stalled voice, camera, or E2B call cannot hang a session, which is operated with LiteLLM. 
Observability: Every agent action, tool use, and LLM call must be logged with execution time and token cost via Langfuse/LangSmith; scoring, adaptive, judge, and proctoring decisions must be traceable per session.
Accuracy: Rubric grading and judgment must keep hallucination/inconsistency below a defined threshold (target <5%) as measured by automated DeepEval/G-Eval test cases; code-task correctness must be verified by executing test cases in E2B.
Integrity: Proctoring (identity, camera enforcement, AI/tab/screenshot/copy-paste detection) must run continuously across all question types with low false-positive/false-negative rates and minimal disruption to the learner.
Maintainability: Agent orchestration, backend/API routing, tool integrations, and the proctoring module must be strictly decoupled for independent PR review and testing, so new tools or phases can be added without touching the core.
Performance: The session/job submission API must return a tracking ID in <500 ms; blueprint generation must complete in <20 s; next-question selection in text/MCQ/diagram phases in <3 s; async grading in <10 s and final report generation in <30 s; live interviews are bounded by their configured time limits (SLA-defined).
Security / Privacy: Voice recordings, camera/video, and learner PII must be encrypted at rest and in transit, captured only with consent, and governed by a defined retention policy aligned with Egypt's PDPL.
Deployment: the full front-end and back-end shall be happening on a live server ready for demos with a domain and certificate on nginx webserver.  
Deliverables
GitHub repository (Git version control).
Admin configuration interface (prompt + config + tool toggles).
Learner profile intake + agentic chat interface (Next.js or Streamlit).
Agent blueprint generation module (the workflow plan).
Examiner agent (LangChain/LangGraph) with five tool integrations: MCQ, diagram/image, voice interview, camera interview, E2B.
Proctoring & integrity system (identity, camera enforcement, AI / tab / screenshot / copy-paste detection).
Rubric grading + LLM judge workflow, evaluated with DeepEval / G-Eval, traced with Langfuse / LangSmith.
Async rendering/grading pipeline on Celery + Redis background workers.
Five-dimension scoring and radar-chart report with email delivery.
API documentation, JSON output schema, and handover document (architecture, limitations, next steps).
0-level testing cycle with document test cases and bug report in the last 2 weeks. 
Full end to end demo video using Arcade and presentation for the demo day. 
