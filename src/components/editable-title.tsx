import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";

/** A title that is its own text field: typing in it renames what is saved. The new name is
 * kept on Enter or when the field loses focus; Escape or an empty name keeps the old one. */
export function EditableTitle({
  value,
  onRename,
  label,
  className,
}: {
  value: string;
  onRename: (title: string) => void;
  /** What the field names; a file name by default. */
  label?: string;
  className?: string;
}) {
  const { t } = useTranslation();
  return (
    <input
      // A new value (another file) starts the field over.
      key={value}
      defaultValue={value}
      title={value}
      aria-label={label ?? t("download.name")}
      spellCheck={false}
      className={cn(
        "-mx-1.5 w-[calc(100%+0.75rem)] min-w-0 truncate rounded-md border border-transparent bg-transparent px-1.5 font-medium outline-none",
        "hover:border-input focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50",
        className,
      )}
      onKeyDown={(e) => {
        if (e.key === "Escape") e.currentTarget.value = value;
        if (e.key === "Enter" || e.key === "Escape") e.currentTarget.blur();
      }}
      onBlur={(e) => {
        const title = e.currentTarget.value.trim();
        if (title && title !== value) onRename(title);
        else e.currentTarget.value = value;
      }}
    />
  );
}
