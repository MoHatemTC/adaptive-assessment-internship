const BASE = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

export async function uploadCV(file: File, sessionId?: string): Promise<{
  cv_upload_id: string;
  parsed_summary: string;
  skills: { name: string; level: string; years: number }[];
}> {
  const form = new FormData();
  form.append("file", file);
  const url = `${BASE}/upload/cv${sessionId ? `?session_id=${sessionId}` : ""}`;
  const res = await fetch(url, { method: "POST", body: form });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "CV upload failed");
  }
  return res.json();
}

export async function submitTaskZip(sessionId: string, file: File): Promise<{
  criteria_scores: { criterion: string; passed: boolean; evidence: string }[];
  total_score: number;
  rationale: string;
}> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/upload/task-submission/${sessionId}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? "Task submission failed");
  }
  return res.json();
}

export async function sendReferencePhoto(
  sessionId: string,
  userId: string,
  frameBase64: string,
): Promise<void> {
  await fetch(`${BASE}/upload/reference-photo`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, user_id: userId, frame_base64: frameBase64 }),
  });
  // Non-critical — swallow errors silently
}

export async function sendCameraFrame(
  sessionId: string,
  userId: string,
  frameBase64: string,
): Promise<{ violation: boolean; violation_type?: string }> {
  const res = await fetch(`${BASE}/upload/frame`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, user_id: userId, frame_base64: frameBase64 }),
  });
  if (!res.ok) return { violation: false };
  return res.json();
}
