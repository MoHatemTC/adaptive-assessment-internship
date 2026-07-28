"use client";

import { Compass, Target } from "lucide-react";

interface Props {
  onSelect: (choice: "discover" | "tracks") => void;
}

export default function IntentSelector({ onSelect }: Props) {
  return (
    <div className="grid md:grid-cols-2 gap-4">
      {/* Help me discover */}
      <button
        onClick={() => onSelect("discover")}
        className="flex flex-col gap-4 p-6 rounded-2xl border-2 border-blue-500/30 bg-blue-500/5 hover:border-blue-500 hover:bg-blue-500/10 transition-all text-left group cursor-pointer"
      >
        <div className="w-10 h-10 rounded-xl bg-blue-500/20 flex items-center justify-center group-hover:bg-blue-500/30 transition-colors">
          <Compass className="w-5 h-5 text-blue-600" />
        </div>
        <div className="flex flex-col gap-1">
          <h3 className="text-base font-bold text-foreground">Help me discover my path</h3>
          <p className="text-sm text-muted-foreground">
            Not sure which track to pursue? A short adaptive assessment will map your personality, aptitude, and
            preferences to the best-fit track.
          </p>
        </div>
        <ul className="flex flex-col gap-1.5">
          {["MCQ + Voice + Visualization", "Personality + aptitude analysis", "Track recommendation with reasoning"].map(
            (f) => (
              <li key={f} className="text-xs text-muted-foreground flex items-center gap-2">
                <span className="w-1.5 h-1.5 rounded-full bg-blue-500 flex-shrink-0" />
                {f}
              </li>
            )
          )}
        </ul>
        <span className="text-sm font-semibold text-blue-600 group-hover:translate-x-0.5 transition-transform inline-block mt-auto">
          Discover my track →
        </span>
      </button>

      {/* I know my goal */}
      <button
        onClick={() => onSelect("tracks")}
        className="flex flex-col gap-4 p-6 rounded-2xl border-2 border-primary/30 bg-primary/5 hover:border-primary hover:bg-primary/10 transition-all text-left group cursor-pointer"
      >
        <div className="w-10 h-10 rounded-xl bg-primary/20 flex items-center justify-center group-hover:bg-primary/30 transition-colors">
          <Target className="w-5 h-5 text-primary" />
        </div>
        <div className="flex flex-col gap-1">
          <h3 className="text-base font-bold text-foreground">I know what I want</h3>
          <p className="text-sm text-muted-foreground">
            Pick your specialization track and get a smart AI assessment of your current skill level with a placement
            report.
          </p>
        </div>
        <ul className="flex flex-col gap-1.5">
          {[
            "9 specialization tracks",
            "AI-adaptive skill assessment",
            "Placement report + course recommendations",
          ].map((f) => (
            <li key={f} className="text-xs text-muted-foreground flex items-center gap-2">
              <span className="w-1.5 h-1.5 rounded-full bg-primary flex-shrink-0" />
              {f}
            </li>
          ))}
        </ul>
        <span className="text-sm font-semibold text-primary group-hover:translate-x-0.5 transition-transform inline-block mt-auto">
          Choose your track →
        </span>
      </button>
    </div>
  );
}
