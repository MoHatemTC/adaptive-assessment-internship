"use client";

import { AlertTriangle, XOctagon } from "lucide-react";
import { useI18n } from "@/lib/i18n";

interface Props { violations: number; lastType?: string | null; }

export default function IntegrityBanner({ violations, lastType }: Props) {
  const { t } = useI18n();
  if (violations === 0) return null;

  const blocked = violations >= 5;

  // Type-specific warning copy so the banner matches what actually happened
  // (camera off, looking away, another person…), not a generic tab/paste message.
  const TYPE_MSG: Record<string, string> = {
    camera_off: t("proctor.cameraOff", "Your camera looks covered or turned off — please keep it on and visible."),
    looking_away: t("proctor.lookingAway", "Please keep your eyes on the screen during the assessment."),
    another_person: t("proctor.anotherPerson", "Another person was detected in view — you must be alone."),
    secondary_device: t("proctor.secondaryDevice", "A secondary device was detected — please put it away."),
    candidate_absent: t("proctor.candidateAbsent", "You appear to have left the camera view — please stay in frame."),
    identity_mismatch: t("proctor.identityMismatch", "We couldn't verify it's still you on camera."),
  };
  const specific = lastType ? TYPE_MSG[lastType] : undefined;
  const generic = t("proctor.generic", "Leaving the assessment window or pasting content is not allowed.");

  return (
    <div className={`fixed top-0 inset-x-0 z-50 px-4 py-3 flex items-center gap-3 text-sm font-medium ${blocked ? "bg-destructive text-white" : "bg-warning/90 text-white"}`}>
      {blocked ? <XOctagon className="w-4 h-4 shrink-0" /> : <AlertTriangle className="w-4 h-4 shrink-0" />}
      {blocked
        ? t("proctor.flagged", "Your session has been flagged due to repeated integrity violations.")
        : `${t("proctor.warning", "Warning")} (${violations}): ${specific ?? generic}`}
    </div>
  );
}
