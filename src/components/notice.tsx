import { Loader2 } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Failure } from "@/features/settings/settings";

/** An error message, read out when it appears. */
export function ErrorNote({ error }: { error: Failure }) {
  const { t } = useTranslation();
  return (
    <p role="alert" className="text-sm text-destructive">
      {t(error.key, error.params)}
    </p>
  );
}

/** A line saying something is under way. */
export function Busy({ children }: { children: ReactNode }) {
  return (
    <p className="flex items-center gap-2 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin text-current" />
      {children}
    </p>
  );
}
