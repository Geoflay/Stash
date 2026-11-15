import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";

interface Option<T extends string> {
  value: T;
  label: ReactNode;
}

/** A row of mutually exclusive buttons. */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: Option<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
}) {
  return (
    <div role="group" aria-label={label} className="flex h-field gap-1 rounded-lg bg-muted p-1">
      {options.map((option) => (
        <Button
          key={option.value}
          type="button"
          variant={option.value === value ? "outline" : "ghost"}
          aria-pressed={option.value === value}
          className="h-full min-w-0 flex-1 whitespace-nowrap"
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </Button>
      ))}
    </div>
  );
}
