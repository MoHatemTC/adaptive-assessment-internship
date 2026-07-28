import asyncio
import resend
from app.config.settings import settings


def _score_color(score: float, out_of: float = 5.0) -> str:
    pct = score / out_of
    if pct >= 0.7:
        return "#22c55e"
    if pct >= 0.4:
        return "#f59e0b"
    return "#ef4444"


def _skill_table(skill_scores: dict) -> str:
    SKILL_LABELS = {
        "thinking":   "Critical Thinking",
        "soft":       "Communication",
        "work":       "Technical Work",
        "digital_ai": "Digital & AI",
        "growth":     "Learning & Growth",
    }
    rows = "".join(
        f"""<tr>
          <td style='padding:6px 10px;color:#6b7280;font-size:13px'>
            {SKILL_LABELS.get(k, k.replace('_',' ').title())}
          </td>
          <td style='padding:6px 10px;font-weight:700;font-size:13px;color:{_score_color(v.get("score", 0))}'>
            {v.get("score", 0):.1f}/5
          </td>
        </tr>"""
        for k, v in skill_scores.items()
    )
    return f"""
    <table border='0' cellpadding='0' cellspacing='0'
           style='border-collapse:collapse;width:100%;background:#f9fafb;
                  border-radius:8px;overflow:hidden;margin:8px 0'>
      <thead>
        <tr style='background:#f3f4f6'>
          <th style='padding:6px 10px;text-align:left;font-size:11px;color:#9ca3af;
                     text-transform:uppercase;letter-spacing:.05em;font-weight:600'>Skill</th>
          <th style='padding:6px 10px;text-align:left;font-size:11px;color:#9ca3af;
                     text-transform:uppercase;letter-spacing:.05em;font-weight:600'>Score</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>"""


def _base_layout(content: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f3f4f6;font-family:Inter,-apple-system,sans-serif">
  <div style="max-width:600px;margin:32px auto;background:white;border-radius:16px;
              overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.1)">
    <!-- Header -->
    <div style="background:linear-gradient(135deg,#1d4ed8,#2563eb);padding:28px 32px">
      <h1 style="margin:0;color:white;font-size:22px;font-weight:700;letter-spacing:-.02em">
        Masar Assessment
      </h1>
    </div>
    <!-- Body -->
    <div style="padding:32px">
      {content}
    </div>
    <!-- Footer -->
    <div style="background:#f9fafb;border-top:1px solid #e5e7eb;
                padding:16px 32px;text-align:center">
      <p style="margin:0;font-size:12px;color:#9ca3af">
        Masar — AI Adaptive Assessment Platform
      </p>
    </div>
  </div>
</body>
</html>"""


def _retro_section(items: list[str], color: str, label: str, icon: str) -> str:
    if not items:
        return ""
    rows = "".join(
        f"<li style='margin:5px 0;font-size:13px;color:#374151;line-height:1.6'>{i}</li>"
        for i in items
    )
    return f"""
    <div style='background:{color}0d;border:1px solid {color}33;border-radius:10px;
                padding:14px 16px;margin:0 0 14px'>
      <p style='margin:0 0 8px;font-size:12px;font-weight:700;color:{color};
                text-transform:uppercase;letter-spacing:.07em'>{icon} {label}</p>
      <ul style='margin:0;padding-left:18px'>{rows}</ul>
    </div>"""


def _rec_item(text: str) -> str:
    # Parse "ACTION — Course Name (X hrs) → URL" format
    if " → " in text:
        parts = text.rsplit(" → ", 1)
        action_part = parts[0]
        url = parts[1].strip()
        # Find course name in the action part (after " — ")
        if " — " in action_part:
            action, course = action_part.split(" — ", 1)
        else:
            action, course = action_part, ""
        link = (f"<a href='{url}' style='color:#2563eb;font-weight:600;text-decoration:none'>"
                f"{course}</a>") if course else f"<a href='{url}' style='color:#2563eb'>Link</a>"
        return (f"<li style='margin:8px 0;font-size:13px;color:#374151;line-height:1.6'>"
                f"{action}<br><span style='font-size:12px;color:#6b7280'>→ </span>{link}</li>")
    return f"<li style='margin:8px 0;font-size:13px;color:#374151;line-height:1.6'>{text}</li>"


def _candidate_html(
    candidate_name: str,
    placement: str,
    total_score: float,
    skill_scores: dict,
    feedback: str,
    went_well: list[str],
    needs_improvement: list[str],
    recommendations: list[str],
    report_url: str,
) -> str:
    placement_color = "#22c55e" if placement == "PRO" else "#f59e0b"
    rec_items = "".join(_rec_item(r) for r in recommendations)
    score_pct = min(100, (total_score / 25) * 100)

    retro_html = (
        _retro_section(went_well,        "#22c55e", "What Went Well",      "✅") +
        _retro_section(needs_improvement, "#ef4444", "Needs Improvement",   "⚠️")
    )

    content = f"""
    <p style='margin:0 0 8px;font-size:16px;color:#111827'>Hi <strong>{candidate_name}</strong>,</p>
    <p style='margin:0 0 24px;font-size:14px;color:#6b7280;line-height:1.6'>
      Your Masar assessment is complete. Here is your personalised report.
    </p>

    <!-- Placement badge -->
    <div style='text-align:center;margin:0 0 24px'>
      <span style='display:inline-block;background:{placement_color}1a;
                   color:{placement_color};border:2px solid {placement_color};
                   border-radius:999px;padding:8px 28px;font-size:20px;font-weight:800;
                   letter-spacing:.05em'>
        {placement}
      </span>
    </div>

    <!-- Total score -->
    <p style='margin:0 0 6px;font-size:13px;font-weight:600;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em'>Total Score</p>
    <p style='margin:0 0 4px;font-size:28px;font-weight:800;color:#111827'>
      {total_score:.1f}<span style='font-size:16px;color:#6b7280;font-weight:400'>/25</span>
    </p>
    <div style='background:#e5e7eb;border-radius:4px;height:8px;margin:8px 0 24px;overflow:hidden'>
      <div style='height:100%;width:{score_pct:.0f}%;border-radius:4px;
                  background:{_score_color(total_score, 25)}'></div>
    </div>

    <!-- Skill breakdown -->
    <p style='margin:0 0 8px;font-size:13px;font-weight:600;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em'>Skill Breakdown</p>
    {_skill_table(skill_scores)}

    <!-- Summary -->
    <p style='margin:24px 0 8px;font-size:13px;font-weight:600;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em'>Assessment Summary</p>
    <p style='margin:0 0 20px;font-size:14px;color:#374151;line-height:1.7;
              background:#f9fafb;border-left:3px solid #2563eb;padding:12px 16px;
              border-radius:0 8px 8px 0'>
      {feedback}
    </p>

    <!-- Retro sections -->
    {retro_html}

    <!-- Recommendations -->
    <p style='margin:20px 0 8px;font-size:13px;font-weight:600;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em'>Recommended Next Steps</p>
    <ul style='margin:0 0 28px;padding-left:20px'>{rec_items}</ul>

    <!-- CTA -->
    <div style='text-align:center'>
      <a href='{report_url}'
         style='display:inline-block;background:#1d4ed8;color:white;text-decoration:none;
                font-size:14px;font-weight:600;padding:12px 28px;border-radius:10px'>
        View Full Report →
      </a>
    </div>
    """
    return _base_layout(content)


def _admin_html(
    candidate_name: str,
    candidate_email: str,
    template_title: str,
    placement: str,
    total_score: float,
    skill_scores: dict,
    integrity_status: str,
    admin_review_url: str,
    session_id: str,
) -> str:
    placement_color = "#22c55e" if placement == "PRO" else "#f59e0b"
    integrity_color = "#22c55e" if integrity_status == "clean" else "#f59e0b" if integrity_status == "warned" else "#ef4444"
    score_pct = min(100, (total_score / 25) * 100)

    content = f"""
    <p style='margin:0 0 24px;font-size:15px;font-weight:600;color:#111827'>
      New assessment completed
    </p>

    <!-- Candidate info -->
    <table border='0' cellpadding='0' cellspacing='0'
           style='width:100%;background:#f9fafb;border-radius:10px;margin:0 0 20px'>
      <tr>
        <td style='padding:14px 16px'>
          <p style='margin:0 0 2px;font-size:15px;font-weight:700;color:#111827'>{candidate_name}</p>
          <p style='margin:0;font-size:13px;color:#6b7280'>{candidate_email}</p>
        </td>
        <td style='padding:14px 16px;text-align:right;vertical-align:middle'>
          <span style='background:{placement_color}1a;color:{placement_color};
                       border:1.5px solid {placement_color};border-radius:999px;
                       padding:4px 14px;font-size:13px;font-weight:700'>{placement}</span>
        </td>
      </tr>
    </table>

    <!-- Template + integrity -->
    <table border='0' cellpadding='0' cellspacing='0' style='width:100%;margin:0 0 20px'>
      <tr>
        <td style='padding:0 8px 0 0;width:50%'>
          <div style='background:#eff6ff;border-radius:8px;padding:12px'>
            <p style='margin:0 0 2px;font-size:11px;color:#3b82f6;font-weight:600;
                      text-transform:uppercase;letter-spacing:.05em'>Template</p>
            <p style='margin:0;font-size:13px;font-weight:600;color:#1e40af'>{template_title}</p>
          </div>
        </td>
        <td style='padding:0 0 0 8px;width:50%'>
          <div style='background:#f9fafb;border-radius:8px;padding:12px'>
            <p style='margin:0 0 2px;font-size:11px;color:#9ca3af;font-weight:600;
                      text-transform:uppercase;letter-spacing:.05em'>Integrity</p>
            <p style='margin:0;font-size:13px;font-weight:700;color:{integrity_color};
                      text-transform:capitalize'>{integrity_status}</p>
          </div>
        </td>
      </tr>
    </table>

    <!-- Score -->
    <p style='margin:0 0 6px;font-size:13px;font-weight:600;color:#6b7280;
              text-transform:uppercase;letter-spacing:.05em'>Total Score</p>
    <p style='margin:0 0 4px;font-size:26px;font-weight:800;color:#111827'>
      {total_score:.1f}<span style='font-size:14px;color:#6b7280;font-weight:400'>/25</span>
    </p>
    <div style='background:#e5e7eb;border-radius:4px;height:6px;margin:8px 0 20px;overflow:hidden'>
      <div style='height:100%;width:{score_pct:.0f}%;border-radius:4px;
                  background:{_score_color(total_score, 25)}'></div>
    </div>

    {_skill_table(skill_scores)}

    <!-- CTA -->
    <div style='text-align:center;margin-top:28px'>
      <a href='{admin_review_url}'
         style='display:inline-block;background:#111827;color:white;text-decoration:none;
                font-size:14px;font-weight:600;padding:12px 28px;border-radius:10px'>
        Review in Admin Panel →
      </a>
    </div>

    <p style='margin:20px 0 0;font-size:11px;color:#9ca3af;text-align:center'>
      Session ID: {session_id}
    </p>
    """
    return _base_layout(content)


async def send_report_email(
    candidate_name: str,
    candidate_email: str,
    admin_email: str,
    placement: str,
    total_score: float,
    skill_scores: dict,
    feedback: str,
    recommendations: list[str],
    session_id: str,
    base_url: str,
    went_well: list[str] = None,
    needs_improvement: list[str] = None,
    template_title: str = "",
    integrity_status: str = "clean",
) -> bool:
    if not settings.resend_api_key:
        print("[Email] Skipping — RESEND_API_KEY not configured.")
        return False

    resend.api_key = settings.resend_api_key

    frontend_base = base_url.replace(":8000", ":3000")
    report_url       = f"{frontend_base}/report/{session_id}"
    admin_review_url = f"{frontend_base}/admin/sessions/{session_id}"

    from_address = settings.report_from_email

    # ── Candidate email ──────────────────────────────────────────────────────
    candidate_html = _candidate_html(
        candidate_name=candidate_name,
        placement=placement,
        total_score=total_score,
        skill_scores=skill_scores,
        feedback=feedback,
        went_well=went_well or [],
        needs_improvement=needs_improvement or [],
        recommendations=recommendations,
        report_url=report_url,
    )

    # ── Admin notification email ─────────────────────────────────────────────
    admin_html = _admin_html(
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        template_title=template_title or "Unknown Template",
        placement=placement,
        total_score=total_score,
        skill_scores=skill_scores,
        integrity_status=integrity_status,
        admin_review_url=admin_review_url,
        session_id=session_id,
    )

    # Determine admin notification recipients
    admin_recipients: list[str] = []
    if settings.admin_notification_email:
        admin_recipients = [e.strip() for e in settings.admin_notification_email.split(",") if e.strip()]
    elif admin_email and admin_email != from_address:
        admin_recipients = [admin_email]

    success = True

    # ── Send candidate email ─────────────────────────────────────────────────
    if candidate_email:
        try:
            await asyncio.to_thread(
                resend.Emails.send,
                {
                    "from":    from_address,
                    "to":      [candidate_email],
                    "subject": f"Your Masar Assessment Report — {placement}",
                    "html":    candidate_html,
                },
            )
            print(f"[Email] Candidate report sent → {candidate_email}")
        except Exception as e:
            print(f"[Email] Failed to send candidate email to {candidate_email}: {e}")
            success = False

    # ── Send admin notification ──────────────────────────────────────────────
    if admin_recipients:
        try:
            await asyncio.to_thread(
                resend.Emails.send,
                {
                    "from":    from_address,
                    "to":      admin_recipients,
                    "subject": f"[Masar] Assessment complete — {candidate_name} ({placement})",
                    "html":    admin_html,
                },
            )
            print(f"[Email] Admin notification sent → {', '.join(admin_recipients)}")
        except Exception as e:
            print(f"[Email] Failed to send admin notification: {e}")
            success = False

    return success


# ── Invitation email ───────────────────────────────────────────────────────────

def _invitation_html(
    candidate_name: str,
    template_title: str,
    time_limit_minutes: int,
    invite_url: str,
) -> str:
    import html as _html
    safe_name  = _html.escape(candidate_name)
    safe_title = _html.escape(template_title)
    # Only allow https URLs in href to prevent javascript: injection
    safe_url   = invite_url if invite_url.startswith("https://") or invite_url.startswith("http://localhost") else "#"

    hours = time_limit_minutes // 60
    mins  = time_limit_minutes % 60
    duration = f"{hours}h {mins}min" if hours else f"{mins} minutes"

    content = f"""
    <p style='margin:0 0 8px;font-size:16px;color:#111827'>Hi <strong>{safe_name}</strong>,</p>
    <p style='margin:0 0 24px;font-size:14px;color:#6b7280;line-height:1.6'>
      You have been invited to complete an assessment as part of a hiring process.
    </p>

    <!-- Assessment card -->
    <div style='background:#f9fafb;border:1px solid #e5e7eb;border-radius:12px;
                padding:20px;margin:0 0 24px'>
      <p style='margin:0 0 4px;font-size:11px;font-weight:700;color:#9ca3af;
                text-transform:uppercase;letter-spacing:.07em'>Assessment</p>
      <p style='margin:0 0 12px;font-size:18px;font-weight:700;color:#111827'>{safe_title}</p>
      <p style='margin:0;font-size:13px;color:#6b7280'>
        ⏱ Takes approximately <strong>{duration}</strong>
      </p>
    </div>

    <p style='margin:0 0 20px;font-size:13px;color:#6b7280;line-height:1.6'>
      This link is personal to you — please do not share it with others.
      Complete the assessment in one sitting in a quiet environment.
    </p>

    <!-- CTA -->
    <div style='text-align:center;margin:0 0 24px'>
      <a href='{safe_url}'
         style='display:inline-block;background:#1d4ed8;color:white;text-decoration:none;
                font-size:15px;font-weight:600;padding:14px 36px;border-radius:10px'>
        Start Assessment →
      </a>
    </div>

    <p style='margin:0;font-size:12px;color:#9ca3af;text-align:center;line-height:1.6'>
      If you have trouble clicking the button, copy and paste this link into your browser:<br>
      <span style='color:#6b7280;word-break:break-all'>{safe_url}</span>
    </p>
    """
    return _base_layout(content)


def _render_placeholders(text: str, values: dict) -> str:
    """Substitute {{placeholder}} tokens in a template string. Values are
    HTML-escaped so a candidate name/title containing < > & can't inject markup
    into the admin's HTML template body."""
    import html as _html
    out = text or ""
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", _html.escape(str(val), quote=True))
    return out


async def send_invitation_email(
    candidate_name: str,
    candidate_email: str,
    template_title: str,
    time_limit_minutes: int,
    invite_url: str,
    db=None,
    subject_override: str | None = None,
    html_override: str | None = None,
) -> bool:
    if not settings.resend_api_key:
        raise RuntimeError("Email is not configured — RESEND_API_KEY is missing.")

    resend.api_key = settings.resend_api_key

    # Built-in defaults (used when no editable template row / any load error)
    subject = f"You're invited to complete the {template_title} assessment"
    html = _invitation_html(candidate_name, template_title, time_limit_minutes, invite_url)

    # Prefer an admin-edited 'invitation' template when a db handle is provided.
    if db is not None:
        try:
            res = await db.table("email_templates").select("subject, html_body").eq("key", "invitation").execute()
            if res.data:
                row = res.data[0]
                values = {
                    "candidate_name": candidate_name,
                    "assessment_title": template_title,
                    "invite_url": invite_url,
                    "time_limit": time_limit_minutes,
                }
                subject = _render_placeholders(row["subject"], values)
                html = _render_placeholders(row["html_body"], values)
        except Exception as e:
            print(f"[Email] Could not load invitation template, using default: {e}")

    # Per-assessment override (highest precedence) — email set on the assessment itself.
    if subject_override or html_override:
        values = {
            "candidate_name": candidate_name,
            "assessment_title": template_title,
            "invite_url": invite_url,
            "time_limit": time_limit_minutes,
        }
        if subject_override:
            subject = _render_placeholders(subject_override, values)
        if html_override:
            html = _render_placeholders(html_override, values)

    try:
        await asyncio.to_thread(
            resend.Emails.send,
            {
                "from":    settings.report_from_email,
                "to":      [candidate_email],
                "subject": subject,
                "html":    html,
            },
        )
        print(f"[Email] Invitation sent → {candidate_email}")
        return True
    except Exception as e:
        # Raise (don't swallow) so callers report the real status + reason. Most
        # common cause: an unverified sender. Resend's shared test sender
        # (onboarding@resend.dev) can only email your own Resend account address —
        # verify a domain in Resend and set REPORT_FROM_EMAIL to an address on it.
        print(f"[Email] Invitation failed for {candidate_email}: {e}")
        raise RuntimeError(
            f"Resend rejected the send from '{settings.report_from_email}': {e}. "
            f"If this is a test/unverified sender, verify a domain in Resend and set "
            f"REPORT_FROM_EMAIL to an address on that domain."
        ) from e
