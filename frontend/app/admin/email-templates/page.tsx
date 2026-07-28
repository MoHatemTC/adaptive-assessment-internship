"use client";

import { useEffect, useState } from "react";
import { getEmailTemplate, saveEmailTemplate } from "@/lib/adminApi";
import type { EmailTemplate } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

const TEMPLATE_KEY = "invitation";

const PLACEHOLDERS = [
  "{{candidate_name}}",
  "{{assessment_title}}",
  "{{invite_url}}",
  "{{time_limit}}",
];

export default function EmailTemplatesPage() {
  const { t } = useI18n();

  const [subject, setSubject] = useState("");
  const [htmlBody, setHtmlBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getEmailTemplate(TEMPLATE_KEY)
      .then((tpl: EmailTemplate) => {
        setSubject(tpl.subject ?? "");
        setHtmlBody(tpl.html_body ?? "");
        setLoading(false);
      })
      .catch((err) => {
        setLoadError(err?.message ?? t("emailtpl.loadError", "Failed to load template"));
        setLoading(false);
      });
  }, []);

  async function handleSave() {
    setSaving(true);
    setSaveError("");
    setSaved(false);
    try {
      const tpl = await saveEmailTemplate(TEMPLATE_KEY, { subject, html_body: htmlBody });
      setSubject(tpl.subject ?? subject);
      setHtmlBody(tpl.html_body ?? htmlBody);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      const e = err as { message?: string };
      setSaveError(e?.message ?? t("emailtpl.saveError", "Failed to save template"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">{t("emailtpl.title", "Email Templates")}</h1>
        <p className="text-sm text-muted-foreground mt-1">
          {t("emailtpl.subtitle", "Customize the invitation email candidates receive.")}
        </p>
      </div>

      {loadError && (
        <div className="text-sm text-destructive bg-destructive/10 border border-destructive/20 rounded-xl px-4 py-3">
          {loadError}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-20">
          <div className="w-6 h-6 rounded-full border-2 border-primary/20 border-t-primary animate-spin" />
        </div>
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Editor */}
          <div className="bg-card border border-border rounded-2xl p-6 flex flex-col gap-5">
            <div>
              <h2 className="text-base font-semibold text-foreground">
                {t("emailtpl.invitationHeading", "Invitation email")}
              </h2>
              <p className="text-xs text-muted-foreground mt-1">
                {t("emailtpl.invitationDesc", "Sent to candidates when you invite them to an assessment.")}
              </p>
            </div>

            <div className="flex flex-col gap-1.5">
              <label htmlFor="subject" className="text-sm font-medium text-foreground">
                {t("emailtpl.subjectLabel", "Subject")}
              </label>
              <input
                id="subject"
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                placeholder={t("emailtpl.subjectPlaceholder", "You're invited to complete the {{assessment_title}} assessment")}
                className="border border-border rounded-xl px-3 py-2 text-sm bg-card focus:outline-none focus:ring-2 focus:ring-primary/30"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label htmlFor="html_body" className="text-sm font-medium text-foreground">
                {t("emailtpl.bodyLabel", "HTML body")}
              </label>
              <textarea
                id="html_body"
                dir="ltr"
                value={htmlBody}
                onChange={(e) => setHtmlBody(e.target.value)}
                spellCheck={false}
                rows={16}
                placeholder={t("emailtpl.bodyPlaceholder", "<p>Hi {{candidate_name}}, …</p>")}
                className="border border-border rounded-xl px-3 py-2 text-xs font-mono leading-relaxed bg-card focus:outline-none focus:ring-2 focus:ring-primary/30 resize-y min-h-[20rem]"
              />
            </div>

            <div className="rounded-xl bg-secondary/40 border border-border px-4 py-3">
              <p className="text-xs font-medium text-muted-foreground mb-2">
                {t("emailtpl.placeholdersHint", "Available placeholders (inserted automatically when the email is sent):")}
              </p>
              <div className="flex flex-wrap gap-2">
                {PLACEHOLDERS.map((p) => (
                  <code
                    key={p}
                    dir="ltr"
                    className="text-xs font-mono bg-primary/10 text-primary border border-primary/20 rounded-md px-2 py-0.5"
                  >
                    {p}
                  </code>
                ))}
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={handleSave}
                disabled={saving}
                className="px-4 py-2 rounded-xl bg-primary text-white text-sm font-medium hover:bg-primary/90 disabled:opacity-50 transition-colors"
              >
                {saving ? t("emailtpl.saving", "Saving…") : t("emailtpl.save", "Save template")}
              </button>
              {saved && (
                <span className="text-sm font-medium text-green-700 dark:text-green-300">
                  {t("emailtpl.saved", "Template saved")}
                </span>
              )}
              {saveError && (
                <span className="text-sm text-destructive">{saveError}</span>
              )}
            </div>
          </div>

          {/* Live preview */}
          <div className="bg-card border border-border rounded-2xl p-6 flex flex-col gap-3">
            <div>
              <h2 className="text-base font-semibold text-foreground">
                {t("emailtpl.preview", "Live preview")}
              </h2>
              <p className="text-xs text-muted-foreground mt-1">
                {t("emailtpl.previewNote", "Placeholders are shown as-is; they'll be replaced when the email is sent.")}
              </p>
            </div>

            <div className="text-xs text-muted-foreground">
              <span className="font-medium text-foreground">{t("emailtpl.subjectLabel", "Subject")}:</span>{" "}
              {subject || <span className="italic">{t("emailtpl.noSubject", "(no subject)")}</span>}
            </div>

            <div
              dir="ltr"
              className="border border-border rounded-xl bg-white text-black p-4 overflow-x-auto min-h-[20rem] text-sm"
              dangerouslySetInnerHTML={{ __html: htmlBody }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
