import type { ComponentProps, ReactNode } from "react";
import { cn } from "@/lib/utils";

/** A tab's cards: one column, two side by side on a wide window once there is something to set. */
export function TabColumns({ wide, children }: { wide: boolean; children: ReactNode }) {
  return (
    <div className={cn("mx-auto grid w-full grid-cols-1 gap-3 lg:items-start", wide ? "max-w-5xl lg:grid-cols-2" : "max-w-2xl")}>
      {children}
    </div>
  );
}

/** A card of a tab. */
export function Card({ className, ...props }: ComponentProps<"section">) {
  return <section className={cn("grid gap-3 rounded-xl border bg-card p-3 sm:p-4", className)} {...props} />;
}
