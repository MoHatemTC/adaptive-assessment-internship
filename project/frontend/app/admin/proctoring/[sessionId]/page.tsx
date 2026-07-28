"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { getProctoringDetail } from "@/lib/adminApi";
import type { ProctoringDetail, ProctoringFrame, ProctoringEvent } from "@/lib/types";
import { useI18n } from "@/lib/i18n";

const STATUS_STYLES: Record<string, string> = {
  flagged: "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  warned:  "bg-yellow-100 text-yellow-700 dark:bg-yellow-950 dark:text-yellow-300",
  clean:   "bg-green-100 text-green-700 dark:bg-green-950 dark:text-green-300",
};

const SEV_STYLES: Record<string, string> = {
  high:   "bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-300",
  medium: "bg-yellow-100 text-yellow-700 dark:bg-yellow-950 dark:text-yellow-300",
  low:    "bg-secondary text-muted-foreground",
};

const VTYPE_LABELS: Record<string, string> = {
  identity_mismatch:  "Identity mismatch",
  secondary_device:   "Secondary device",
  phone_detected:     "Phone detected",      // legacy label for old records
  another_person:     "Another person",
  candidate_absent:   "Absent",
  camera_off:         "Camera off",
};

function fmt(iso: string) {
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "medium" });
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleTimeString(undefined, { timeStyle: "medium" });
}

function FrameCard({ frame }: { frame: ProctoringFrame }) {
  const { t } = useI18n();
  const [enlarged, setEnlarged] = useState(false);
  const v = frame.gemini_verdict;
  const vtype = v?.violation_type && v.violation_type !== "null" ? v.violation_type : null;

  return (
    <>
      <div
        className="bg-card border border-border rounded-xl overflow-hidden cursor-pointer hover:border-primary/40 transition-colors"
        onClick={() => setEnlarged(true)}
      >
        {/* Frame image */}
        <div className="relative bg-black aspect-[4/3]">
          <img
            src={`data:image/jpeg;base64,${frame.frame_base64}`}
            alt={t("admin.proctoring.flaggedFrame", "Flagged frame")}
            className="w-full h-full object-cover"
          />
          {/* Confidence badge top-left */}
          {v && (
            <span className={`absolute top-1.5 left-1.5 text-[10px] font-medium px-1.5 py-0.5 rounded ${SEV_STYLES[v.confidence] ?? "bg-secondary text-muted-foreground"}`}>
              {v.confidence}
            </span>
          )}
          {/* Violation type badge top-right */}
          {vtype && (
            <span className="absolute top-1.5 right-1.5 text-[10px] font-semibold px-1.5 py-0.5 rounded bg-red-600 text-white">
              {t(`admin.proctoring.vtype.${vtype}`, VTYPE_LABELS[vtype] ?? vtype)}
            </span>
          )}
        </div>

        {/* Verdict details */}
        <div className="p-3 flex flex-col gap-2">
          <p className="text-xs text-muted-foreground">{fmtTime(frame.created_at)}</p>
          {v?.reasoning && (
            <p className="text-[11px] text-foreground/80 italic leading-snug line-clamp-2">
              {v.reasoning}
            </p>
          )}
          {v && (
            <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
              <Row label={t("admin.proctoring.rowOnScreen", "On screen")}        ok={v.looking_at_screen} />
              <Row label={t("admin.proctoring.rowNo2ndDevice", "No 2nd device")}    ok={!v.secondary_device_visible} />
              <Row label={t("admin.proctoring.rowNoOtherPerson", "No other person")}  ok={!v.another_person_present} />
              <Row label={t("admin.proctoring.rowPresent", "Present")}          ok={!v.candidate_absent} />
              <Row label={t("admin.proctoring.rowFaceVisible", "Face visible")}     ok={!!v.face_clearly_visible} />
              {v.identity_matches_reference !== null && (
                <Row label={t("admin.proctoring.rowIdentityMatch", "Identity match")} ok={!!v.identity_matches_reference} />
              )}
            </div>
          )}
          {v?.observations && (
            <p className="text-[11px] text-muted-foreground border-t border-border pt-2 mt-1 line-clamp-2">
              {v.observations}
            </p>
          )}
        </div>
      </div>

      {/* Lightbox */}
      {enlarged && (
        <div
          className="fixed inset-0 z-50 bg-black/80 flex items-center justify-center p-6"
          onClick={() => setEnlarged(false)}
        >
          <div className="max-w-2xl w-full bg-card rounded-2xl overflow-hidden shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <img
              src={`data:image/jpeg;base64,${frame.frame_base64}`}
              alt={t("admin.proctoring.enlargedFrame", "Enlarged frame")}
              className="w-full"
            />
            <div className="p-4">
              <p className="text-sm font-medium text-foreground mb-1">
                {vtype ? t(`admin.proctoring.vtype.${vtype}`, VTYPE_LABELS[vtype] ?? vtype) : t("admin.proctoring.flaggedFrame", "Flagged frame")} — {v?.confidence} {t("admin.proctoring.confidence", "confidence")}
              </p>
              <p className="text-xs text-muted-foreground">{fmt(frame.created_at)}</p>
              {v?.reasoning && <p className="text-sm text-foreground/80 mt-2 italic">{v.reasoning}</p>}
              {v?.observations && <p className="text-sm text-muted-foreground mt-1">{v.observations}</p>}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function Row({ label, ok }: { label: string; ok: boolean }) {
  return (
    <>
      <span className="text-muted-foreground">{label}</span>
      <span className={ok ? "text-green-600 dark:text-green-400 font-medium" : "text-red-600 dark:text-red-400 font-medium"}>
        {ok ? "✓" : "✗"}
      </span>
    </>
  );
}

function EventRow({ event }: { event: ProctoringEvent }) {
  const { t } = useI18n();
  return (
    <div className="flex items-start gap-3 py-2.5 border-b border-border last:border-0">
      <span className={`shrink-0 text-[11px] font-medium px-2 py-0.5 rounded-full mt-0.5 ${SEV_STYLES[event.severity]}`}>
        {event.severity}
      </span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-foreground">
          {t(`admin.proctoring.vtype.${event.event_type}`, VTYPE_LABELS[event.event_type] ?? event.event_type.replace(/_/g, " "))}
        </p>
        {event.metadata?.notes && (
          <p className="text-xs text-muted-foreground mt-0.5 line-clamp-1">{String(event.metadata.notes)}</p>
        )}
      </div>
      <span className="shrink-0 text-xs text-muted-foreground whitespace-nowrap">{fmtTime(event.created_at)}</span>
    </div>
  );
}

export default function ProctoringDetailPage() {
  const { t } = useI18n();
  const { sessionId } = useParams<{ sessionId: string }>();
  const router = useRouter();
  const [data, setData] = useState<ProctoringDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getProctoringDetail(sessionId)
      .then(setData)
      .catch(() => router.push("/admin/proctoring"))
      .finally(() => setLoading(false));
  }, [sessionId, router]);

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <div className="w-7 h-7 rounded-full border-4 border-primary/20 border-t-primary animate-spin" />
      </div>
    );
  }

  if (!data) return null;
  const { session, frames, events } = data;

  const startTime = session.started_at ? new Date(session.started_at) : null;
  const endTime   = session.completed_at ? new Date(session.completed_at) : null;
  const durationMin = startTime && endTime
    ? Math.round((endTime.getTime() - startTime.getTime()) / 60000)
    : null;

  return (
    <div className="flex flex-col gap-8">
      {/* Back + header */}
      <div>
        <button
          onClick={() => router.push("/admin/proctoring")}
          className="text-xs text-muted-foreground hover:text-foreground flex items-center gap-1 mb-4 transition-colors"
        >
          ← {t("admin.proctoring.backToSessions", "Back to sessions")}
        </button>

        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold text-foreground">{session.candidate_name}</h1>
            <p className="text-sm text-muted-foreground">{session.candidate_email}</p>
          </div>
          <div className="flex items-center gap-2 flex-wrap justify-end">
            <span className={`text-xs font-semibold px-2.5 py-1 rounded-full ${STATUS_STYLES[session.integrity_status] ?? ""}`}>
              {session.integrity_status}
            </span>
            {durationMin !== null && (
              <span className="text-xs text-muted-foreground bg-secondary px-2.5 py-1 rounded-full">
                {durationMin} {t("admin.proctoring.minutes", "min")}
              </span>
            )}
            {startTime && (
              <span className="text-xs text-muted-foreground">{fmt(session.started_at!)}</span>
            )}
          </div>
        </div>
      </div>

      {/* Flagged frames */}
      <section>
        <h2 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
          {t("admin.proctoring.flaggedFramesHeading", "Flagged frames")}
          <span className="text-xs font-normal text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">
            {frames.length}
          </span>
        </h2>

        {frames.length === 0 ? (
          <div className="bg-card border border-border rounded-xl p-6 text-center">
            <p className="text-sm text-muted-foreground">{t("admin.proctoring.noFrames", "No frames were stored for this session.")}</p>
            <p className="text-xs text-muted-foreground mt-1">{t("admin.proctoring.framesCaptureNote", "Only medium/high-confidence violations are captured.")}</p>
          </div>
        ) : (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
            {frames.map((f) => (
              <FrameCard key={f.id} frame={f} />
            ))}
          </div>
        )}
      </section>

      {/* Event timeline */}
      <section>
        <h2 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
          {t("admin.proctoring.eventTimeline", "Event timeline")}
          <span className="text-xs font-normal text-muted-foreground bg-secondary px-2 py-0.5 rounded-full">
            {events.length}
          </span>
        </h2>

        {events.length === 0 ? (
          <div className="bg-card border border-border rounded-xl p-6 text-center">
            <p className="text-sm text-muted-foreground">{t("admin.proctoring.noEvents", "No events recorded.")}</p>
          </div>
        ) : (
          <div className="bg-card border border-border rounded-xl px-4 divide-y divide-border">
            {events.map((e) => (
              <EventRow key={e.id} event={e} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
