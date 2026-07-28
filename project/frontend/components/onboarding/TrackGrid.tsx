import Link from "next/link";
import {
  Code,
  Server,
  Brain,
  BarChart3,
  Briefcase,
  Palette,
  Megaphone,
  Smartphone,
  ShieldCheck,
} from "lucide-react";
import { TRACKS } from "@/lib/journeyData";
import type { LucideIcon } from "lucide-react";

const ICON_MAP: Record<string, LucideIcon> = {
  Code,
  Server,
  Brain,
  BarChart3,
  Briefcase,
  Palette,
  Megaphone,
  Smartphone,
  ShieldCheck,
};

export default function TrackGrid() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
      {TRACKS.map((track) => {
        const Icon = ICON_MAP[track.icon] ?? Code;
        return (
          <Link
            key={track.slug}
            href={`/onboarding/tracks/${track.slug}`}
            className="group flex flex-col gap-3 p-5 rounded-2xl border border-border bg-card hover:shadow-md transition-all hover:-translate-y-0.5"
            style={{ borderLeftWidth: "3px", borderLeftColor: track.color }}
          >
            <div
              className="w-10 h-10 rounded-xl flex items-center justify-center"
              style={{ backgroundColor: `${track.color}20` }}
            >
              <Icon className="w-5 h-5" style={{ color: track.color }} />
            </div>

            <div className="flex flex-col gap-0.5">
              <h3 className="text-sm font-bold text-foreground group-hover:text-primary transition-colors">
                {track.name}
              </h3>
              <p className="text-xs text-muted-foreground">{track.hashtag}</p>
            </div>

            <div className="flex flex-wrap gap-1 mt-auto">
              {track.journeys.slice(0, 2).map((j) => (
                <span
                  key={j}
                  className="text-[10px] px-1.5 py-0.5 rounded-md bg-secondary text-muted-foreground font-medium"
                >
                  {j}
                </span>
              ))}
              {track.journeys.length > 2 && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-md bg-secondary text-muted-foreground font-medium">
                  +{track.journeys.length - 2} more
                </span>
              )}
            </div>
          </Link>
        );
      })}
    </div>
  );
}
