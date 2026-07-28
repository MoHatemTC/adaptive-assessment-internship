"use client";

import { useState } from "react";
import { CheckCircle } from "lucide-react";
import CVUpload from "@/components/upload/CVUpload";
import { useI18n } from "@/lib/i18n";
import type { CVParseResult } from "@/lib/types";

// value = stored/backend value (English, do not translate); key = i18n key for the displayed label.
const EDUCATION_OPTIONS = [
  { value: "High School", key: "onboarding.eduHighSchool" },
  { value: "Diploma", key: "onboarding.eduDiploma" },
  { value: "Bachelor's", key: "onboarding.eduBachelors" },
  { value: "Master's", key: "onboarding.eduMasters" },
  { value: "PhD", key: "onboarding.eduPhd" },
  { value: "Other", key: "onboarding.eduOther" },
];
const STATUS_OPTIONS = [
  { value: "Student", key: "onboarding.statusStudent" },
  { value: "Fresh Graduate", key: "onboarding.statusFreshGraduate" },
  { value: "Employed", key: "onboarding.statusEmployed" },
  { value: "Freelancer", key: "onboarding.statusFreelancer" },
  { value: "Career Changer", key: "onboarding.statusCareerChanger" },
];
const EXPERIENCE_OPTIONS = [
  { value: "0 years", key: "onboarding.exp0Years" },
  { value: "1–2 years", key: "onboarding.exp1To2Years" },
  { value: "3–5 years", key: "onboarding.exp3To5Years" },
  { value: "6–10 years", key: "onboarding.exp6To10Years" },
  { value: "10+ years", key: "onboarding.exp10PlusYears" },
];

export interface ProfileData {
  education: string;
  status: string;
  experience: string;
}

interface Props {
  onComplete: (profile: ProfileData, cv: CVParseResult) => void;
}

function ChipGroup({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { value: string; key: string }[];
  value?: string;
  onChange: (v: string) => void;
}) {
  const { t } = useI18n();
  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{label}</p>
      <div className="flex flex-wrap gap-2">
        {options.map((opt) => (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            className={`px-3 py-1.5 rounded-full text-sm font-medium border transition-all ${
              value === opt.value
                ? "bg-primary text-white border-primary shadow-sm"
                : "bg-secondary text-foreground border-border hover:border-primary/40 hover:bg-primary/5"
            }`}
          >
            {t(opt.key)}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function ProfileForm({ onComplete }: Props) {
  const { t } = useI18n();
  const [profile, setProfile] = useState<Partial<ProfileData>>({});
  const [cv, setCv] = useState<CVParseResult | null>(null);

  const allDone = profile.education && profile.status && profile.experience && cv;

  return (
    <div className="flex flex-col gap-6">
      <ChipGroup
        label={t("onboarding.educationLevel")}
        options={EDUCATION_OPTIONS}
        value={profile.education}
        onChange={(v) => setProfile((p) => ({ ...p, education: v }))}
      />
      <ChipGroup
        label={t("onboarding.employmentStatus")}
        options={STATUS_OPTIONS}
        value={profile.status}
        onChange={(v) => setProfile((p) => ({ ...p, status: v }))}
      />
      <ChipGroup
        label={t("onboarding.yearsOfExperience")}
        options={EXPERIENCE_OPTIONS}
        value={profile.experience}
        onChange={(v) => setProfile((p) => ({ ...p, experience: v }))}
      />

      {/* CV Upload — mandatory */}
      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          {t("onboarding.yourCv")} <span className="text-destructive ml-0.5">*</span>
        </p>

        {cv ? (
          <div className="bg-primary/5 border border-primary/20 rounded-xl p-4 flex items-start gap-3">
            <CheckCircle className="w-4 h-4 text-primary mt-0.5 flex-shrink-0" />
            <div className="flex flex-col gap-1 min-w-0">
              <p className="text-sm text-foreground leading-relaxed line-clamp-2">{cv.parsed_summary}</p>
              <div className="flex flex-wrap gap-1.5 mt-1">
                {cv.skills.slice(0, 5).map((s) => (
                  <span
                    key={s.name}
                    className="text-xs px-2 py-0.5 rounded-full bg-primary/10 text-primary font-medium"
                  >
                    {s.name}
                  </span>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <CVUpload onSuccess={setCv} />
        )}
      </div>

      <button
        type="button"
        disabled={!allDone}
        onClick={() => allDone && onComplete(profile as ProfileData, cv!)}
        className="w-full py-3.5 rounded-xl bg-primary text-white font-semibold text-sm hover:bg-primary/90 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        {t("onboarding.continue")}
      </button>
    </div>
  );
}
