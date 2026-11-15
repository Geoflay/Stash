import { Film, Play } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { isAndroid } from "@/lib/platform";
import { cn } from "@/lib/utils";

/** The bar along the bottom of a tab: what will be saved on the left, the action that
 * saves it on the right. Its picture opens the preview, when there is one. On Android it
 * sits above the tab bar. */
export function ActionBar({
  thumbnail = null,
  title,
  details,
  onPreview,
  previewing = false,
  children,
}: {
  thumbnail?: string | null;
  title: string;
  /** The kind of file and its estimated size. */
  details: string;
  /** Opens or closes the preview; none without one. */
  onPreview?: () => void;
  previewing?: boolean;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const picture = thumbnail ? (
    <img src={thumbnail} alt="" referrerPolicy="no-referrer" className="size-10 shrink-0 rounded-md bg-muted object-cover" />
  ) : (
    <span className="flex size-10 shrink-0 items-center justify-center rounded-md border border-dashed text-muted-foreground">
      <Film className="size-4" />
    </span>
  );
  return (
    <div
      className={cn(
        "fixed inset-x-0 z-20 flex h-16 items-center gap-3 border-t bg-background/95 px-3 backdrop-blur sm:px-4",
        isAndroid ? "bottom-[calc(4rem+1px+env(safe-area-inset-bottom))]" : "bottom-0",
      )}
    >
      {onPreview ? (
        <button
          type="button"
          aria-label={t("edit.playPreview")}
          aria-pressed={previewing}
          onClick={onPreview}
          className={cn("relative size-10 shrink-0 rounded-md", previewing && "ring-2 ring-foreground ring-offset-2 ring-offset-background")}
        >
          {picture}
          <span className="absolute inset-0 flex items-center justify-center rounded-md bg-black/45 text-white">
            <Play className="size-4 fill-current" />
          </span>
        </button>
      ) : (
        picture
      )}
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium" title={title}>
          {title}
        </p>
        <p className="truncate text-xs text-muted-foreground tabular-nums">{details}</p>
      </div>
      {children}
    </div>
  );
}
