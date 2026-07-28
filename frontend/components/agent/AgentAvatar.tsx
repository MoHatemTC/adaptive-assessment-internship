import { cn } from "@/lib/utils";

interface Props {
  size?: "sm" | "md" | "lg";
  pulse?: boolean;
}

const sizes = { sm: "w-8 h-8 text-sm", md: "w-10 h-10 text-base", lg: "w-14 h-14 text-xl" };

export default function AgentAvatar({ size = "md", pulse = true }: Props) {
  return (
    <div className="relative shrink-0">
      <div
        className={cn(
          "rounded-full bg-gradient-to-br from-primary to-blue-700 flex items-center justify-center text-white font-bold shadow-lg",
          sizes[size],
          pulse && "agent-orb-pulse"
        )}
      >
        م
      </div>
      {/* Online indicator */}
      <span className="absolute bottom-0 right-0 w-2.5 h-2.5 rounded-full bg-success border-2 border-background" />
    </div>
  );
}
