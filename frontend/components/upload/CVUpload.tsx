"use client";

import { useState, useRef, useCallback } from "react";
import { uploadCV } from "@/lib/uploadApi";
import { useI18n } from "@/lib/i18n";

interface Props {
  sessionId?: string;
  onSuccess: (result: { cv_upload_id: string; parsed_summary: string; skills: { name: string; level: string; years: number }[] }) => void;
}

export default function CVUpload({ sessionId, onSuccess }: Props) {
  const { t } = useI18n();
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [fileName, setFileName] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFile = useCallback(async (file: File) => {
    if (!file.type.includes("pdf")) {
      setError(t("cmp.cvUpload.errorPdfOnly", "Please upload a PDF file."));
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setError(t("cmp.cvUpload.errorTooLarge", "File too large (max 10MB)."));
      return;
    }
    setFileName(file.name);
    setUploading(true);
    setError("");
    try {
      const result = await uploadCV(file, sessionId);
      onSuccess(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : t("cmp.cvUpload.errorUploadFailed", "Upload failed. Please try again."));
      setFileName("");
    } finally {
      setUploading(false);
    }
  }, [sessionId, onSuccess]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) handleFile(file);
  };

  return (
    <div className="flex flex-col gap-3">
      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
        className={`
          relative border-2 border-dashed rounded-2xl p-8 flex flex-col items-center gap-3 cursor-pointer transition-all
          ${dragging ? "border-primary bg-primary/5 scale-[1.01]" : "border-border hover:border-primary/50 hover:bg-secondary/50"}
          ${uploading ? "pointer-events-none opacity-70" : ""}
        `}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,application/pdf"
          className="hidden"
          onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
        />

        {uploading ? (
          <>
            <div className="w-12 h-12 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
            <p className="text-sm text-muted-foreground">{t("cmp.cvUpload.analyzing", "Analyzing your CV with AI…")}</p>
          </>
        ) : fileName ? (
          <>
            <div className="w-12 h-12 rounded-xl bg-green-500/10 flex items-center justify-center">
              <svg className="w-6 h-6 text-green-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <p className="text-sm font-medium text-foreground">{fileName}</p>
            <p className="text-xs text-muted-foreground">{t("cmp.cvUpload.clickToReplace", "Click to replace")}</p>
          </>
        ) : (
          <>
            <div className="w-12 h-12 rounded-xl bg-primary/10 flex items-center justify-center">
              <svg className="w-6 h-6 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
            </div>
            <div className="text-center">
              <p className="text-sm font-medium text-foreground">{t("cmp.cvUpload.dropHere", "Drop your CV here")}</p>
              <p className="text-xs text-muted-foreground mt-1">{t("cmp.cvUpload.constraints", "PDF only · Max 10MB")}</p>
            </div>
            <span className="px-4 py-1.5 rounded-lg bg-primary/10 text-primary text-xs font-medium">
              {t("cmp.cvUpload.browseFiles", "Browse files")}
            </span>
          </>
        )}
      </div>

      {error && (
        <p className="text-sm text-destructive flex items-center gap-2">
          <svg className="w-4 h-4 flex-shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
          </svg>
          {error}
        </p>
      )}
    </div>
  );
}
